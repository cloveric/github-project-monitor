import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import subprocess

from github_watch import (
    ActionError,
    classify_package_version,
    classify_branch_status,
    classify_release_status,
    infer_category,
    is_github_repo_slug,
    merge_project_entries,
    normalize_github_repository,
    parse_git_porcelain,
    parse_ahead_behind,
    perform_action,
    repo_slug_from_url,
    select_latest_release,
    trash_project,
    update_project_to_commit,
    update_project_to_release,
)


class GitHubWatchTests(unittest.TestCase):
    def git(self, args, cwd):
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def commit_file(self, repo: Path, name: str, body: str, message: str):
        (repo / name).write_text(body, encoding="utf-8")
        self.git(["add", name], repo)
        self.git(
            [
                "-c",
                "user.email=test@example.com",
                "-c",
                "user.name=Test",
                "commit",
                "-m",
                message,
            ],
            repo,
        )

    def test_repo_slug_from_common_github_remote_forms(self):
        self.assertEqual(
            repo_slug_from_url("https://github.com/openai/skills.git"),
            "openai/skills",
        )
        self.assertEqual(
            repo_slug_from_url("git@github.com:heygen-com/hyperframes.git"),
            "heygen-com/hyperframes",
        )
        self.assertEqual(
            repo_slug_from_url("ssh://git@github.com/nexu-io/open-design.git"),
            "nexu-io/open-design",
        )

    def test_parse_ahead_behind_counts(self):
        self.assertEqual(parse_ahead_behind("2\t15\n"), (2, 15))
        self.assertEqual(parse_ahead_behind("0 0"), (0, 0))

    def test_classify_branch_status_prioritizes_behind_and_local_changes(self):
        self.assertEqual(classify_branch_status(0, 5, False), "behind")
        self.assertEqual(classify_branch_status(0, 5, True), "behind+dirty")
        self.assertEqual(classify_branch_status(2, 0, False), "ahead")
        self.assertEqual(classify_branch_status(0, 0, True), "dirty")
        self.assertEqual(classify_branch_status(0, 0, False), "sync")

    def test_select_latest_release_skips_drafts_and_prereleases_by_default(self):
        releases = [
            {
                "tag_name": "v2.0.0-beta.1",
                "draft": False,
                "prerelease": True,
                "published_at": "2026-05-28T00:00:00Z",
            },
            {
                "tag_name": "v1.9.0",
                "draft": False,
                "prerelease": False,
                "published_at": "2026-05-20T00:00:00Z",
            },
            {
                "tag_name": "v3.0.0-draft",
                "draft": True,
                "prerelease": False,
                "published_at": "2026-05-29T00:00:00Z",
            },
        ]

        self.assertEqual(select_latest_release(releases, False)["tag_name"], "v1.9.0")
        self.assertEqual(
            select_latest_release(releases, True)["tag_name"], "v2.0.0-beta.1"
        )

    def test_classify_release_status_compares_current_and_latest_release_tags(self):
        self.assertEqual(
            classify_release_status("v1.0.0", "v1.0.0", 0),
            "release-current",
        )
        self.assertEqual(
            classify_release_status("v1.0.0", "v1.1.0", 7),
            "release-behind",
        )
        self.assertEqual(
            classify_release_status(None, "v1.1.0", None),
            "release-unknown-local-tag",
        )
        self.assertEqual(
            classify_release_status("v1.0.0", None, None),
            "release-unavailable",
        )

    def test_classify_package_version_handles_floating_and_pinned_specs(self):
        self.assertEqual(
            classify_package_version("latest", "1.2.3"),
            "package-floating-latest",
        )
        self.assertEqual(
            classify_package_version("1.2.3", "1.2.3"),
            "package-current",
        )
        self.assertEqual(
            classify_package_version("1.2.2", "1.2.3"),
            "package-behind",
        )
        self.assertEqual(
            classify_package_version("latest", None),
            "version-unavailable",
        )

    def test_github_repo_slug_validation(self):
        self.assertTrue(is_github_repo_slug("openai/skills"))
        self.assertTrue(is_github_repo_slug("cloveric/github-project-monitor"))
        self.assertFalse(is_github_repo_slug("https://github.com/openai/skills"))
        self.assertFalse(is_github_repo_slug("../openai/skills"))

    def test_parse_git_porcelain_counts_local_pollution(self):
        summary = parse_git_porcelain(" M README.md\n?? scratch.txt\n D old.txt\n")

        self.assertEqual(summary["dirty_files"], 3)
        self.assertEqual(summary["untracked_files"], 1)
        self.assertEqual(summary["modified_files"], 1)
        self.assertEqual(summary["deleted_files"], 1)

    def test_infer_category_covers_skills_plugins_and_mcp_packages(self):
        self.assertEqual(
            infer_category(
                "npm:@playwright/mcp@latest",
                {"type": "npm", "name": "playwright-mcp"},
            ),
            "mcp",
        )
        self.assertEqual(
            infer_category("/Users/me/.claude/plugins/marketplaces/example"),
            "plugin",
        )
        self.assertEqual(
            infer_category("/Users/me/projects/example", {"category": "app"}),
            "app",
        )

    def test_merge_project_entries_deduplicates_watchlist_and_scan_results(self):
        primary = [
            {
                "name": "monitor",
                "repo": "cloveric/github-project-monitor",
                "path": "/Users/me/projects/github-project-monitor",
            }
        ]
        discovered = [
            {
                "name": "monitor",
                "repo": "cloveric/github-project-monitor",
                "path": "/Users/me/other/github-project-monitor",
            },
            {
                "name": "skills",
                "repo": "openai/skills",
                "path": "/Users/me/.codex/skills",
            },
        ]

        merged = merge_project_entries(primary, discovered)

        self.assertEqual([item["repo"] for item in merged], [
            "cloveric/github-project-monitor",
            "openai/skills",
        ])

    def test_normalize_github_repository_keeps_store_fields(self):
        normalized = normalize_github_repository(
            {
                "name": "skills",
                "full_name": "openai/skills",
                "owner": {"login": "openai"},
                "description": "Agent skills",
                "private": False,
                "stargazers_count": 42,
                "forks_count": 7,
                "default_branch": "main",
                "html_url": "https://github.com/openai/skills",
            }
        )

        self.assertEqual(normalized["full_name"], "openai/skills")
        self.assertEqual(normalized["visibility"], "public")
        self.assertEqual(normalized["stars"], 42)

    def test_trash_project_moves_clean_git_worktree_to_local_trash(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo = root / "demo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)

            with patch("github_watch.Path.home", return_value=root):
                result = trash_project(str(repo))

            self.assertEqual(result["action"], "uninstall")
            self.assertFalse(repo.exists())
            self.assertTrue(Path(result["trashedTo"]).exists())
            self.assertTrue(str(result["trashedTo"]).startswith(str(root / ".local")))

    def test_update_commit_refuses_dirty_worktree_before_fetching(self):
        with TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "dirty"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
            (repo / "scratch.txt").write_text("local work", encoding="utf-8")

            with self.assertRaises(ActionError) as context:
                update_project_to_commit(str(repo))

            self.assertIn("worktree is dirty", str(context.exception))

    def test_update_release_checks_out_latest_release_tag(self):
        with TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "release"
            repo.mkdir()
            self.git(["init"], repo)
            self.commit_file(repo, "README.md", "release repo", "initial")
            self.git(["tag", "v1.0.0"], repo)

            with (
                patch("github_watch.latest_release", return_value=("v1.0.0", None)),
                patch("github_watch.fetch_remotes_without_tags"),
                patch("github_watch.fetch_tag"),
            ):
                result = update_project_to_release(str(repo), repo="owner/release")

            self.assertEqual(result["action"], "updateRelease")
            self.assertEqual(result["tag"], "v1.0.0")

    def test_update_commit_ignores_conflicting_unrelated_remote_tags(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            upstream = root / "upstream"
            remote = root / "remote.git"
            local = root / "local"

            upstream.mkdir()
            self.git(["init", "-b", "main"], upstream)
            self.commit_file(upstream, "README.md", "one", "initial")
            self.git(["tag", "moved-tag"], upstream)
            self.git(["clone", "--bare", str(upstream), str(remote)], root)
            self.git(["clone", str(remote), str(local)], root)

            self.git(["remote", "add", "origin", str(remote)], upstream)
            self.commit_file(upstream, "README.md", "two", "second")
            self.git(["tag", "-f", "moved-tag"], upstream)
            self.git(["push", "origin", "main"], upstream)
            self.git(["push", "--force", "origin", "refs/tags/moved-tag"], upstream)

            result = update_project_to_commit(str(local))

            self.assertEqual(result["action"], "updateCommit")
            self.assertEqual((local / "README.md").read_text(encoding="utf-8"), "two")

    def test_update_release_fetches_only_target_tag_when_other_tags_conflict(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            upstream = root / "upstream"
            remote = root / "remote.git"
            local = root / "local"

            upstream.mkdir()
            self.git(["init", "-b", "main"], upstream)
            self.commit_file(upstream, "README.md", "one", "initial")
            self.git(["tag", "v1.0.0"], upstream)
            self.git(["tag", "moved-tag"], upstream)
            self.git(["clone", "--bare", str(upstream), str(remote)], root)
            self.git(["clone", str(remote), str(local)], root)

            self.git(["remote", "add", "origin", str(remote)], upstream)
            self.commit_file(upstream, "README.md", "two", "second")
            self.git(["tag", "-f", "moved-tag"], upstream)
            self.git(["push", "origin", "main"], upstream)
            self.git(["push", "--force", "origin", "refs/tags/moved-tag"], upstream)

            with patch("github_watch.latest_release", return_value=("v1.0.0", None)):
                result = update_project_to_release(str(local), repo="owner/release")

            self.assertEqual(result["action"], "updateRelease")
            self.assertEqual(result["tag"], "v1.0.0")

    def test_perform_action_rejects_unknown_actions(self):
        with self.assertRaises(ActionError):
            perform_action("explode", {})


if __name__ == "__main__":
    unittest.main()
