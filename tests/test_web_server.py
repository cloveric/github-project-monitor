import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from github_watch import ProjectStatus
from github_watch import ActionError
from github_watch_web import (
    action_request_error,
    authorize_action_payload,
    build_api_payload,
    filter_projects,
    resolve_static_path,
    roots_query,
)


class GitHubWatchWebTests(unittest.TestCase):
    def test_build_api_payload_summarizes_statuses_for_dashboard(self):
        statuses = [
            ProjectStatus(
                kind="git",
                path="/tmp/a",
                repo="owner/a",
                name="a",
                visibility="public",
                branch="main",
                upstream="origin/main",
                ahead=0,
                behind=3,
                dirty=False,
                branch_status="behind",
                current_tag="v1.0.0",
                latest_release="v1.2.0",
                latest_release_published_at="2026-05-28T00:00:00Z",
                release_status="release-behind",
                release_commits_behind=7,
                fetch_status="ok",
            ),
            ProjectStatus(
                kind="git",
                path="/tmp/b",
                repo="owner/b",
                name="b",
                visibility="private",
                branch="main",
                upstream="origin/main",
                ahead=0,
                behind=0,
                dirty=True,
                branch_status="dirty",
                current_tag=None,
                latest_release=None,
                latest_release_published_at=None,
                release_status="release-unavailable",
                release_commits_behind=None,
                fetch_status="ok",
            ),
        ]

        payload = build_api_payload(statuses)

        self.assertEqual(payload["summary"]["projects"], 2)
        self.assertEqual(payload["summary"]["branchBehind"], 1)
        self.assertEqual(payload["summary"]["releaseBehind"], 1)
        self.assertEqual(payload["summary"]["dirty"], 1)
        self.assertEqual(payload["projects"][0]["name"], "a")

    def test_build_api_payload_includes_update_plan_and_instance_groups(self):
        statuses = [
            ProjectStatus(
                kind="git",
                path="/tmp/a1",
                repo="owner/a",
                name="a-codex",
                visibility="public",
                branch="main",
                upstream="origin/main",
                ahead=0,
                behind=2,
                dirty=False,
                branch_status="behind",
                current_tag="v1.0.0",
                latest_release="v1.0.0",
                latest_release_published_at=None,
                release_status="release-current",
                release_commits_behind=0,
                fetch_status="ok",
            ),
            ProjectStatus(
                kind="git",
                path="/tmp/a2",
                repo="owner/a",
                name="a-claude",
                visibility="public",
                branch="main",
                upstream="origin/main",
                ahead=0,
                behind=0,
                dirty=True,
                branch_status="dirty",
                current_tag="v1.0.0",
                latest_release="v1.0.0",
                latest_release_published_at=None,
                release_status="release-current",
                release_commits_behind=0,
                fetch_status="ok",
            ),
        ]

        payload = build_api_payload(statuses)

        self.assertEqual(payload["updatePlan"]["summary"]["actions"], 1)
        self.assertEqual(payload["instanceGroups"][0]["repo"], "owner/a")
        self.assertEqual(payload["instanceGroups"][0]["instances"], 2)

    def test_filter_projects_matches_name_repo_or_path(self):
        projects = [
            {"name": "hyperframes", "repo": "heygen-com/hyperframes", "path": "/a/b"},
            {"name": "open-design", "repo": "nexu-io/open-design", "path": "/c/d"},
        ]

        self.assertEqual(filter_projects(projects, "heygen")[0]["name"], "hyperframes")
        self.assertEqual(filter_projects(projects, "design")[0]["name"], "open-design")
        self.assertEqual(filter_projects(projects, "/a/")[0]["name"], "hyperframes")

    def test_roots_query_keeps_default_scan_roots_for_extra_install_root(self):
        with patch("github_watch_web.default_scan_roots", return_value=[Path("/default")]):
            roots = roots_query({"extraRoot": ["/tmp/custom"]})

        self.assertEqual(roots, [Path("/default"), Path("/tmp/custom")])

    def test_action_request_requires_json_token_and_local_host(self):
        valid_headers = {
            "Host": "127.0.0.1:8765",
            "Origin": "http://127.0.0.1:8765",
            "Content-Type": "application/json",
            "X-GitHub-Watch-Token": "secret",
        }

        self.assertIsNone(
            action_request_error(valid_headers, "secret", {"127.0.0.1", "localhost", "::1"})
        )
        self.assertEqual(
            action_request_error(
                {**valid_headers, "Content-Type": "text/plain"},
                "secret",
                {"127.0.0.1", "localhost", "::1"},
            )[0],
            415,
        )
        self.assertEqual(
            action_request_error(
                {**valid_headers, "X-GitHub-Watch-Token": "wrong"},
                "secret",
                {"127.0.0.1", "localhost", "::1"},
            )[0],
            403,
        )
        self.assertEqual(
            action_request_error(
                {**valid_headers, "Host": "example.com:8765"},
                "secret",
                {"127.0.0.1", "localhost", "::1"},
            )[0],
            403,
        )

    def test_authorize_action_payload_requires_inventory_path(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            allowed = root / "allowed"
            outside = root / "outside"
            allowed.mkdir()
            outside.mkdir()
            watchlist = root / "watchlist.json"
            watchlist.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "projects": [
                            {
                                "name": "allowed",
                                "repo": "owner/allowed",
                                "path": str(allowed),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            authorize_action_payload(watchlist, {"action": "updateCommit", "path": str(allowed)})
            with self.assertRaises(ActionError):
                authorize_action_payload(watchlist, {"action": "updateCommit", "path": str(outside)})

    def test_resolve_static_path_rejects_sibling_prefix_escape(self):
        web_root = Path("/tmp/project/web")

        self.assertEqual(resolve_static_path("/", web_root), (web_root / "index.html").resolve())
        self.assertIsNone(resolve_static_path("/../web_evil/secret.txt", web_root))


if __name__ == "__main__":
    unittest.main()
