import unittest

from github_watch import (
    classify_package_version,
    classify_branch_status,
    classify_release_status,
    parse_ahead_behind,
    repo_slug_from_url,
    select_latest_release,
)


class GitHubWatchTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
