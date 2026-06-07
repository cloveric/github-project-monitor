#!/usr/bin/env python3
"""Local web dashboard for GitHub Project Monitor."""

from __future__ import annotations

import json
import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from github_watch import (
    ActionError,
    DEFAULT_WATCHLIST,
    INSTALL_AGENTS,
    ProjectStatus,
    attach_history_insights,
    build_update_plan,
    check_entry_safe,
    current_listening_services,
    detect_project_service,
    default_install_root,
    default_scan_roots,
    load_history_snapshots,
    load_inventory,
    load_watchlist,
    perform_action,
    render_markdown,
    run_post_update_steps,
    save_history_snapshot,
    search_github_repositories,
)


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
MAX_ACTION_BODY_BYTES = 64 * 1024
PROJECT_ACTIONS = {"updateCommit", "updateRelease", "uninstall"}
DEFAULT_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}


def filter_projects(projects: list[dict[str, Any]], query: str | None) -> list[dict[str, Any]]:
    if not query:
        return projects
    needle = query.lower()
    return [
        project
        for project in projects
        if needle in project.get("name", "").lower()
        or needle in project.get("repo", "").lower()
        or needle in project.get("package", "").lower()
        or needle in project.get("path", "").lower()
    ]


def build_api_payload(statuses: list[ProjectStatus]) -> dict[str, Any]:
    branch_behind = [
        item for item in statuses if item.branch_status in {"behind", "behind+dirty"}
    ]
    release_behind = [
        item for item in statuses if item.release_status in {"release-behind", "package-behind"}
    ]
    dirty = [item for item in statuses if item.dirty]
    sync = [
        item
        for item in statuses
        if item.branch_status == "sync"
        and item.release_status not in {"release-behind", "package-behind"}
    ]
    update_plan = build_update_plan(statuses)
    groups: dict[str, dict[str, Any]] = {}
    for item in statuses:
        key = item.repo or item.package or item.name
        group = groups.setdefault(
            key,
            {
                "repo": key,
                "instances": 0,
                "dirty": 0,
                "branchBehind": 0,
                "releaseBehind": 0,
                "paths": [],
                "categories": [],
            },
        )
        group["instances"] += 1
        group["dirty"] += 1 if item.dirty else 0
        group["branchBehind"] += 1 if item.branch_status in {"behind", "behind+dirty"} else 0
        group["releaseBehind"] += 1 if item.release_status in {"release-behind", "package-behind"} else 0
        group["paths"].append(item.path)
        if item.category not in group["categories"]:
            group["categories"].append(item.category)
    instance_groups = sorted(
        [item for item in groups.values() if item["instances"] > 1],
        key=lambda item: (-item["instances"], item["repo"]),
    )

    return {
        "summary": {
            "projects": len(statuses),
            "branchBehind": len(branch_behind),
            "releaseBehind": len(release_behind),
            "dirty": len(dirty),
            "sync": len(sync),
        },
        "updatePlan": update_plan,
        "instanceGroups": instance_groups,
        "projects": [asdict(item) for item in statuses],
    }


def check_watchlist(
    watchlist_path: Path,
    no_fetch: bool,
    include_prereleases: bool,
    only: str | None,
    include_discovered: bool = False,
    scan_roots: list[Path] | None = None,
) -> list[ProjectStatus]:
    projects = filter_projects(
        load_inventory(
            watchlist_path,
            include_discovered=include_discovered,
            scan_roots=scan_roots,
        ),
        only,
    )
    if not projects:
        return []
    workers = min(8, len(projects))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        statuses = list(
            executor.map(
                lambda project: check_entry_safe(
                    project,
                    no_fetch=no_fetch,
                    include_prereleases=include_prereleases,
                ),
                projects,
            )
        )
    listeners = current_listening_services()
    for project, status in zip(projects, statuses):
        service = detect_project_service(project, listeners)
        status.service_name = service.get("name")
        status.service_running = service.get("running")
        status.service_ports = service.get("ports")
        status.service_restart = service.get("restart")
        status.service_log = service.get("log")
    return statuses


def bool_query(params: dict[str, list[str]], name: str) -> bool:
    value = params.get(name, ["0"])[0].lower()
    return value in {"1", "true", "yes", "on"}


def int_query(params: dict[str, list[str]], name: str, default: int) -> int:
    value = params.get(name, [str(default)])[0]
    try:
        return int(value)
    except ValueError:
        return default


def roots_query(params: dict[str, list[str]]) -> list[Path] | None:
    raw_values = params.get("roots")
    extra_values = params.get("extraRoot") or params.get("extraRoots")
    if not raw_values and not extra_values:
        return None

    roots: list[Path] = []
    if extra_values:
        roots.extend(default_scan_roots())

    for raw_value in raw_values or extra_values or []:
        for item in raw_value.split("|"):
            item = item.strip()
            if item:
                roots.append(Path(item).expanduser())
    return roots or None


def header_value(headers: Any, name: str) -> str | None:
    value = headers.get(name) if hasattr(headers, "get") else None
    if value is not None:
        return str(value)
    if isinstance(headers, dict):
        lowered = name.lower()
        for key, item in headers.items():
            if str(key).lower() == lowered:
                return str(item)
    return None


def normalized_host(value: str | None) -> str:
    value = (value or "").strip()
    if value.startswith("[") and "]" in value:
        return value[1:].split("]", 1)[0].lower()
    return value.split(":", 1)[0].lower()


def allowed_hosts_for(bind_host: str) -> set[str]:
    env_value = os.environ.get("GITHUB_STORE_ALLOWED_HOSTS")
    if env_value:
        return {item.strip().lower() for item in env_value.split(",") if item.strip()}
    allowed = set(DEFAULT_ALLOWED_HOSTS)
    normalized = normalized_host(bind_host)
    if normalized and normalized not in {"0.0.0.0", "::"}:
        allowed.add(normalized)
    return allowed


def is_allowed_host(host_header: str | None, allowed_hosts: set[str]) -> bool:
    host = normalized_host(host_header)
    return bool(host and host in allowed_hosts)


def is_allowed_origin(origin_header: str | None, allowed_hosts: set[str]) -> bool:
    if not origin_header:
        return True
    parsed = urlparse(origin_header)
    return parsed.scheme in {"http", "https"} and is_allowed_host(parsed.netloc, allowed_hosts)


def action_request_error(
    headers: Any,
    expected_token: str,
    allowed_hosts: set[str],
) -> tuple[int, str] | None:
    if not is_allowed_host(header_value(headers, "Host"), allowed_hosts):
        return 403, "host is not allowed"
    if not is_allowed_origin(header_value(headers, "Origin"), allowed_hosts):
        return 403, "origin is not allowed"

    content_type = (header_value(headers, "Content-Type") or "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        return 415, "Content-Type must be application/json"

    supplied_token = header_value(headers, "X-GitHub-Watch-Token") or ""
    if not expected_token or not secrets.compare_digest(supplied_token, expected_token):
        return 403, "invalid action token"
    return None


def resolve_static_path(request_path: str, web_root: Path = WEB_ROOT) -> Path | None:
    relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
    root = web_root.resolve()
    file_path = (root / relative).resolve()
    try:
        file_path.relative_to(root)
    except ValueError:
        return None
    return file_path


def inventory_project_paths(watchlist_path: Path) -> set[Path]:
    paths: set[Path] = set()
    for item in load_inventory(watchlist_path, include_discovered=True):
        path_value = str(item.get("path") or "")
        if not path_value or path_value.startswith("npm:"):
            continue
        paths.add(Path(path_value).expanduser().resolve())
    return paths


def project_for_action_path(watchlist_path: Path, raw_path: str) -> dict[str, Any] | None:
    if not raw_path:
        return None
    candidate = Path(raw_path).expanduser().resolve()
    for item in load_inventory(watchlist_path, include_discovered=True):
        path_value = str(item.get("path") or "")
        if not path_value or path_value.startswith("npm:"):
            continue
        root = Path(path_value).expanduser().resolve()
        if candidate == root or path_is_under(candidate, root):
            return item
    return None


def path_is_under(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def authorize_action_payload(watchlist_path: Path, payload: dict[str, Any]) -> None:
    action = str(payload.get("action") or "")
    if action not in PROJECT_ACTIONS:
        return

    raw_path = str(payload.get("path") or "")
    if not raw_path:
        raise ActionError("path is required")
    candidate = Path(raw_path).expanduser().resolve()
    allowed_paths = inventory_project_paths(watchlist_path)
    if not any(candidate == path or path_is_under(candidate, path) for path in allowed_paths):
        raise ActionError(f"path is not in monitored inventory: {candidate}")


def run_configured_post_update(
    watchlist_path: Path,
    raw_path: str,
) -> list[dict[str, Any]]:
    project = project_for_action_path(watchlist_path, raw_path)
    if not project:
        return []
    from github_watch import normalize_post_update_steps

    steps = normalize_post_update_steps(project.get("postUpdate") or project.get("post_update"))
    if not steps:
        return []
    return run_post_update_steps(steps, Path(raw_path).expanduser().resolve())


def perform_authorized_action(watchlist_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "")
    if action == "runPlan":
        results = []
        for item in payload.get("actions", []):
            child = {
                "action": item.get("action"),
                "path": item.get("path"),
                "repo": item.get("repo"),
                "includePrereleases": payload.get("includePrereleases"),
            }
            authorize_action_payload(watchlist_path, child)
            result = perform_action(str(child["action"]), child)
            if payload.get("runPostUpdate"):
                result["postUpdate"] = run_configured_post_update(
                    watchlist_path,
                    str(child.get("path") or ""),
                )
            results.append(result)
        return {
            "ok": True,
            "action": "runPlan",
            "results": results,
            "message": f"ran {len(results)} planned actions",
        }

    authorize_action_payload(watchlist_path, payload)
    result = perform_action(action, payload)
    if payload.get("runPostUpdate") and action in PROJECT_ACTIONS:
        result["postUpdate"] = run_configured_post_update(
            watchlist_path,
            str(payload.get("path") or ""),
        )
    return result


def make_handler(
    watchlist_path: Path,
    action_token: str | None = None,
    allowed_hosts: set[str] | None = None,
):
    action_token = action_token or secrets.token_urlsafe(32)
    allowed_hosts = allowed_hosts or set(DEFAULT_ALLOWED_HOSTS)

    class GitHubWatchHandler(BaseHTTPRequestHandler):
        server_version = "GitHubWatch/1.0"

        def log_message(self, format: str, *args: Any) -> None:
            return

        def host_allowed(self) -> bool:
            return is_allowed_host(self.headers.get("Host"), allowed_hosts)

        def do_GET(self) -> None:
            if not self.host_allowed():
                self.send_json({"ok": False, "error": "host is not allowed"}, status=403)
                return

            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            if parsed.path == "/api/config":
                self.send_json(
                    {
                        "installRoot": str(default_install_root()),
                        "defaultInstallerAgent": "codex",
                        "installAgents": [
                            {"id": key, "label": label}
                            for key, label in INSTALL_AGENTS.items()
                        ],
                        "scanRoots": [str(path) for path in default_scan_roots()],
                        "actionToken": action_token,
                    }
                )
                return
            if parsed.path == "/api/projects":
                self.send_json({"projects": load_watchlist(watchlist_path)})
                return
            if parsed.path == "/api/search":
                query = params.get("q", [""])[0]
                limit = int_query(params, "limit", 20)
                self.send_json(
                    {"query": query, "results": search_github_repositories(query, limit)}
                )
                return
            if parsed.path == "/api/check":
                self.handle_check(parsed.query)
                return
            if parsed.path == "/api/report.md":
                self.handle_report(parsed.query)
                return
            self.serve_static(parsed.path)

        def do_POST(self) -> None:
            request_error = action_request_error(self.headers, action_token, allowed_hosts)
            if request_error:
                status, message = request_error
                self.send_json({"ok": False, "error": message}, status=status)
                return

            parsed = urlparse(self.path)
            if parsed.path != "/api/action":
                self.send_error(404)
                return

            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length > MAX_ACTION_BODY_BYTES:
                self.send_json({"ok": False, "error": "request body too large"}, status=413)
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            except json.JSONDecodeError:
                self.send_json({"ok": False, "error": "invalid JSON"}, status=400)
                return

            action = str(payload.get("action") or "")
            try:
                result = perform_authorized_action(watchlist_path, payload)
            except ActionError as error:
                self.send_json({"ok": False, "error": str(error)}, status=409)
                return
            except Exception as error:
                self.send_json({"ok": False, "error": str(error)}, status=500)
                return
            self.send_json(result)

        def handle_check(self, query: str) -> None:
            params = parse_qs(query)
            try:
                statuses = check_watchlist(
                    watchlist_path=watchlist_path,
                    no_fetch=bool_query(params, "noFetch"),
                    include_prereleases=bool_query(params, "includePrereleases"),
                    only=params.get("only", [None])[0],
                    include_discovered=bool_query(params, "discover"),
                    scan_roots=roots_query(params),
                )
            except Exception as error:
                self.send_json({"ok": False, "error": str(error)}, status=500)
                return
            snapshots = load_history_snapshots(limit=50)
            attach_history_insights(statuses, snapshots)
            payload = build_api_payload(statuses)
            if not bool_query(params, "noHistory"):
                save_history_snapshot(statuses, payload["summary"])
                payload["history"] = {
                    "snapshots": load_history_snapshots(limit=10),
                }
            else:
                payload["history"] = {"snapshots": snapshots[-10:]}
            self.send_json(payload)

        def handle_report(self, query: str) -> None:
            params = parse_qs(query)
            try:
                statuses = check_watchlist(
                    watchlist_path=watchlist_path,
                    no_fetch=bool_query(params, "noFetch"),
                    include_prereleases=bool_query(params, "includePrereleases"),
                    only=params.get("only", [None])[0],
                    include_discovered=bool_query(params, "discover"),
                    scan_roots=roots_query(params),
                )
            except Exception as error:
                self.send_json({"ok": False, "error": str(error)}, status=500)
                return
            attach_history_insights(statuses, load_history_snapshots(limit=50))
            body = render_markdown(statuses).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def serve_static(self, request_path: str) -> None:
            file_path = resolve_static_path(request_path)
            if not file_path or not file_path.is_file():
                self.send_error(404)
                return
            content_type = "text/plain"
            if file_path.suffix == ".html":
                content_type = "text/html; charset=utf-8"
            elif file_path.suffix == ".css":
                content_type = "text/css; charset=utf-8"
            elif file_path.suffix == ".js":
                content_type = "application/javascript; charset=utf-8"
            body = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return GitHubWatchHandler


def serve(host: str = "127.0.0.1", port: int = 8765, watchlist_path: Path = DEFAULT_WATCHLIST) -> None:
    action_token = os.environ.get("GITHUB_STORE_ACTION_TOKEN") or secrets.token_urlsafe(32)
    allowed_hosts = allowed_hosts_for(host)
    handler = make_handler(Path(watchlist_path), action_token=action_token, allowed_hosts=allowed_hosts)
    server = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{server.server_port}"
    print(f"GitHub Project Monitor running at {url}", flush=True)
    server.serve_forever()


def main() -> int:
    serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
