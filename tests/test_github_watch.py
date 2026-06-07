import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import subprocess

from github_watch import (
    ActionError,
    ProjectStatus,
    attach_history_insights,
    build_update_plan,
    build_install_agent_command,
    check_entry_safe,
    check_project,
    classify_package_version,
    classify_branch_status,
    classify_release_status,
    detect_project_service,
    diagnostic_for_status,
    infer_category,
    install_repository,
    is_github_repo_slug,
    merge_project_entries,
    normalize_post_update_steps,
    normalize_install_agent,
    normalize_github_repository,
    parse_git_porcelain,
    parse_ahead_behind,
    perform_action,
    repo_slug_from_url,
    run_post_update_steps,
    save_history_snapshot,
    load_history_snapshots,
    select_latest_release,
    trash_project,
    update_project_to_commit,
    update_project_to_release,
)


class GitHubWatchTests(unittest.TestCase):
    def status(self, **overrides):
        values = {
            "kind": "git",
            "path": "/tmp/project",
            "repo": "owner/project",
            "name": "project",
            "visibility": "public",
            "branch": "main",
            "upstream": "origin/main",
            "ahead": 0,
            "behind": 0,
            "dirty": False,
            "branch_status": "sync",
            "current_tag": "v1.0.0",
            "latest_release": "v1.0.0",
            "latest_release_published_at": None,
            "release_status": "release-current",
            "release_commits_behind": 0,
            "fetch_status": "skipped",
        }
        values.update(overrides)
        return ProjectStatus(**values)

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

    def test_build_update_plan_lists_safe_updates_and_blocked_items(self):
        statuses = [
            self.status(name="behind", path="/tmp/behind", branch_status="behind", behind=2),
            self.status(
                name="release",
                path="/tmp/release",
                branch_status="sync",
                current_tag="v1.0.0",
                latest_release="v1.1.0",
                release_status="release-behind",
                release_commits_behind=3,
            ),
            self.status(
                name="dirty-release",
                path="/tmp/dirty",
                dirty=True,
                branch_status="dirty",
                current_tag="v1.0.0",
                latest_release="v1.1.0",
                release_status="release-behind",
                release_commits_behind=3,
            ),
        ]

        plan = build_update_plan(statuses)

        self.assertEqual([item["action"] for item in plan["actions"]], [
            "updateCommit",
            "updateRelease",
        ])
        self.assertEqual(plan["actions"][0]["name"], "behind")
        self.assertEqual(plan["blocked"][0]["name"], "dirty-release")
        self.assertIn("dirty", plan["blocked"][0]["reason"])

    def test_history_snapshots_attach_last_dirty_and_trend(self):
        with TemporaryDirectory() as tmpdir:
            history_path = Path(tmpdir) / "snapshots.jsonl"
            save_history_snapshot(
                [self.status(path="/tmp/project", dirty=True, branch_status="dirty")],
                {"projects": 1},
                history_path,
                now="2026-06-01T00:00:00+08:00",
            )
            snapshots = load_history_snapshots(history_path, limit=5)
            status = self.status(path="/tmp/project", dirty=False, branch_status="sync")
            attach_history_insights([status], snapshots)

        self.assertEqual(status.last_dirty_seen_at, "2026-06-01T00:00:00+08:00")
        self.assertEqual(status.previous_branch_status, "dirty")
        self.assertEqual(status.status_trend, "improved")

    def test_diagnostic_for_status_explains_common_failures(self):
        invalid = self.status(branch_status="invalid", error="not a git worktree")
        npm = self.status(kind="npm", release_status="version-unavailable", fetch_status="fail:npm")
        private_release = self.status(
            visibility="private",
            latest_release=None,
            release_status="release-unavailable",
        )

        self.assertIn("not a git worktree", diagnostic_for_status(invalid))
        self.assertIn("npm", diagnostic_for_status(npm))
        self.assertIn("release", diagnostic_for_status(private_release))

    def test_normalize_and_run_post_update_steps(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            steps = normalize_post_update_steps(["python3 -c \"open('ok.txt','w').write('ok')\""])
            results = run_post_update_steps(steps, root, timeout=30)

            self.assertEqual(results[0]["status"], "ok")
            self.assertEqual((root / "ok.txt").read_text(encoding="utf-8"), "ok")

    def test_detect_project_service_uses_configured_match_and_ports(self):
        service = detect_project_service(
            {
                "name": "monitor",
                "path": "/Users/me/projects/monitor",
                "service": {
                    "name": "Monitor Web",
                    "match": "github_watch.py web",
                    "restart": "python3 github_watch.py web",
                },
            },
            [
                {
                    "pid": 123,
                    "command": "python3 github_watch.py web",
                    "ports": [8765],
                }
            ],
        )

        self.assertEqual(service["name"], "Monitor Web")
        self.assertTrue(service["running"])
        self.assertEqual(service["ports"], [8765])
        self.assertEqual(service["restart"], "python3 github_watch.py web")

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

    def test_merge_project_entries_deduplicates_by_path_not_repo(self):
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
            "cloveric/github-project-monitor",
            "openai/skills",
        ])
        self.assertEqual(
            [item["path"] for item in merged],
            [
                "/Users/me/projects/github-project-monitor",
                "/Users/me/other/github-project-monitor",
                "/Users/me/.codex/skills",
            ],
        )

    def test_check_project_marks_existing_non_git_paths_invalid(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "not-git"
            path.mkdir()

            status = check_project(
                {"name": "not-git", "repo": "", "path": str(path), "visibility": "private"},
                no_fetch=True,
                include_prereleases=False,
            )

        self.assertEqual(status.branch_status, "invalid")
        self.assertEqual(status.fetch_status, "skipped")
        self.assertEqual(status.error, "not a git worktree")

    def test_safe_check_entry_returns_error_status_for_malformed_entries(self):
        status = check_entry_safe(
            {"name": "broken", "repo": "owner/broken"},
            no_fetch=True,
            include_prereleases=False,
        )

        self.assertEqual(status.branch_status, "error")
        self.assertIn("path", status.error)

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

    def test_normalize_install_agent_defaults_to_codex_and_accepts_claude(self):
        self.assertEqual(normalize_install_agent(None), "codex")
        self.assertEqual(normalize_install_agent(""), "codex")
        self.assertEqual(normalize_install_agent("claude"), "claude")
        self.assertEqual(normalize_install_agent("git"), "direct")

        with self.assertRaises(ActionError):
            normalize_install_agent("unknown")

    def test_build_install_agent_command_covers_codex_and_claude(self):
        destination = Path("/tmp/example")

        codex_command, codex_cwd = build_install_agent_command(
            "codex", "owner/example", destination, Path("/tmp/summary.txt")
        )
        self.assertEqual(codex_cwd, destination)
        self.assertEqual(codex_command[:2], ["codex", "exec"])
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", codex_command)
        self.assertIn("owner/example", codex_command[-1])

        claude_command, claude_cwd = build_install_agent_command(
            "claude", "owner/example", destination, Path("/tmp/summary.txt")
        )
        self.assertEqual(claude_cwd, destination)
        self.assertEqual(claude_command[:2], ["claude", "-p"])
        self.assertIn("bypassPermissions", claude_command)
        self.assertIn("owner/example", claude_command[-1])

    def test_install_repository_runs_codex_agent_by_default_after_clone(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            destination = root / "example"

            def fake_clone(args, timeout=8):
                destination.mkdir()
                return subprocess.CompletedProcess(["gh", *args], 0, stdout="", stderr="")

            with (
                patch("github_watch.shutil.which", return_value="/usr/bin/gh"),
                patch("github_watch.run_gh", side_effect=fake_clone),
                patch(
                    "github_watch.run_install_agent",
                    return_value={
                        "agent": "codex",
                        "summary": "dependencies installed",
                        "log": "/tmp/install.log",
                    },
                ) as run_agent,
            ):
                result = install_repository("owner/example", str(root))

            self.assertEqual(result["installerAgent"], "codex")
            self.assertEqual(result["agentSummary"], "dependencies installed")
            run_agent.assert_called_once_with("codex", "owner/example", destination)

    def test_install_repository_allows_claude_and_direct_installers(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            clone_count = 0

            def fake_clone(args, timeout=8):
                nonlocal clone_count
                clone_count += 1
                Path(args[-1]).mkdir()
                return subprocess.CompletedProcess(["gh", *args], 0, stdout="", stderr="")

            with (
                patch("github_watch.shutil.which", return_value="/usr/bin/gh"),
                patch("github_watch.run_gh", side_effect=fake_clone),
                patch(
                    "github_watch.run_install_agent",
                    return_value={"agent": "claude", "summary": "ready", "log": "/tmp/log"},
                ) as run_agent,
            ):
                claude_result = install_repository("owner/claude-demo", str(root), "claude")

            self.assertEqual(claude_result["installerAgent"], "claude")
            run_agent.assert_called_once()

            with (
                patch("github_watch.shutil.which", return_value="/usr/bin/gh"),
                patch("github_watch.run_gh", side_effect=fake_clone),
                patch("github_watch.run_install_agent") as run_agent,
            ):
                direct_result = install_repository("owner/direct-demo", str(root), "direct")

            self.assertEqual(direct_result["installerAgent"], "direct")
            run_agent.assert_not_called()
            self.assertEqual(clone_count, 2)

    def test_install_repository_quarantines_partial_clone_when_agent_fails(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            install_root = root / "projects"
            destination = install_root / "example"

            def fake_clone(args, timeout=8):
                destination.mkdir(parents=True)
                return subprocess.CompletedProcess(["gh", *args], 0, stdout="", stderr="")

            with (
                patch("github_watch.Path.home", return_value=root),
                patch("github_watch.shutil.which", return_value="/usr/bin/gh"),
                patch("github_watch.run_gh", side_effect=fake_clone),
                patch("github_watch.run_install_agent", side_effect=ActionError("agent failed")),
            ):
                with self.assertRaises(ActionError) as context:
                    install_repository("owner/example", str(install_root), "codex")

            self.assertFalse(destination.exists())
            partial_root = root / ".local" / "share" / "github-project-monitor" / "partial"
            self.assertTrue(partial_root.exists())
            self.assertIn("partial clone moved to", str(context.exception))

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
