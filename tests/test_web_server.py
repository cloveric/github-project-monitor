import unittest
from pathlib import Path
from unittest.mock import patch

from github_watch import ProjectStatus
from github_watch_web import build_api_payload, filter_projects, roots_query


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


if __name__ == "__main__":
    unittest.main()
