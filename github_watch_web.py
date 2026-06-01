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
    DEFAULT_WATCHLIST,
    ProjectStatus,
    check_entry,
    load_watchlist,
    render_markdown,
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
) -> list[ProjectStatus]:
    projects = filter_projects(load_watchlist(watchlist_path), only)
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


def make_handler(watchlist_path: Path):
    class GitHubWatchHandler(BaseHTTPRequestHandler):
        server_version = "GitHubWatch/1.0"

        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/projects":
                self.send_json({"projects": load_watchlist(watchlist_path)})
                return
            if parsed.path == "/api/check":
                self.handle_check(parsed.query)
                return
            if parsed.path == "/api/report.md":
                self.handle_report(parsed.query)
                return
            self.serve_static(parsed.path)

        def handle_check(self, query: str) -> None:
            params = parse_qs(query)
            statuses = check_watchlist(
                watchlist_path=watchlist_path,
                no_fetch=bool_query(params, "noFetch"),
                include_prereleases=bool_query(params, "includePrereleases"),
                only=params.get("only", [None])[0],
            )
            self.send_json(build_api_payload(statuses))

        def handle_report(self, query: str) -> None:
            params = parse_qs(query)
            statuses = check_watchlist(
                watchlist_path=watchlist_path,
                no_fetch=bool_query(params, "noFetch"),
                include_prereleases=bool_query(params, "includePrereleases"),
                only=params.get("only", [None])[0],
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

        def send_json(self, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
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
