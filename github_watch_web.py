#!/usr/bin/env python3
"""Local web dashboard for GitHub Project Monitor."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from github_watch import (
    ActionError,
    DEFAULT_WATCHLIST,
    ProjectStatus,
    check_entry,
    default_install_root,
    default_scan_roots,
    load_inventory,
    load_watchlist,
    perform_action,
    render_markdown,
    search_github_repositories,
)


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"


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
    return {
        "summary": {
            "projects": len(statuses),
            "branchBehind": len(branch_behind),
            "releaseBehind": len(release_behind),
            "dirty": len(dirty),
            "sync": len(sync),
        },
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
        return list(
            executor.map(
                lambda project: check_entry(
                    project,
                    no_fetch=no_fetch,
                    include_prereleases=include_prereleases,
                ),
                projects,
            )
        )


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


def make_handler(watchlist_path: Path):
    class GitHubWatchHandler(BaseHTTPRequestHandler):
        server_version = "GitHubWatch/1.0"

        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            if parsed.path == "/api/config":
                self.send_json(
                    {
                        "installRoot": str(default_install_root()),
                        "scanRoots": [str(path) for path in default_scan_roots()],
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
            parsed = urlparse(self.path)
            if parsed.path != "/api/action":
                self.send_error(404)
                return

            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            except json.JSONDecodeError:
                self.send_json({"ok": False, "error": "invalid JSON"}, status=400)
                return

            action = str(payload.get("action") or "")
            try:
                result = perform_action(action, payload)
            except ActionError as error:
                self.send_json({"ok": False, "error": str(error)}, status=409)
                return
            except Exception as error:
                self.send_json({"ok": False, "error": str(error)}, status=500)
                return
            self.send_json(result)

        def handle_check(self, query: str) -> None:
            params = parse_qs(query)
            statuses = check_watchlist(
                watchlist_path=watchlist_path,
                no_fetch=bool_query(params, "noFetch"),
                include_prereleases=bool_query(params, "includePrereleases"),
                only=params.get("only", [None])[0],
                include_discovered=bool_query(params, "discover"),
                scan_roots=roots_query(params),
            )
            self.send_json(build_api_payload(statuses))

        def handle_report(self, query: str) -> None:
            params = parse_qs(query)
            statuses = check_watchlist(
                watchlist_path=watchlist_path,
                no_fetch=bool_query(params, "noFetch"),
                include_prereleases=bool_query(params, "includePrereleases"),
                only=params.get("only", [None])[0],
                include_discovered=bool_query(params, "discover"),
                scan_roots=roots_query(params),
            )
            body = render_markdown(statuses).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def serve_static(self, request_path: str) -> None:
            relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
            file_path = (WEB_ROOT / relative).resolve()
            web_root = WEB_ROOT.resolve()
            if not str(file_path).startswith(str(web_root)) or not file_path.is_file():
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
    handler = make_handler(Path(watchlist_path))
    server = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{server.server_port}"
    print(f"GitHub Project Monitor running at {url}", flush=True)
    server.serve_forever()


def main() -> int:
    serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
