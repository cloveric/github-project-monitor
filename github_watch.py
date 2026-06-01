#!/usr/bin/env python3
"""Track local GitHub checkouts against branches and GitHub releases."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
GITHUB_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SKIP_SCAN_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".cache",
    ".venv",
    "__pycache__",
    "node_modules",
    "Library",
}
GITHUB_METADATA_CACHE: dict[str, dict[str, Any]] = {}


def resolve_default_watchlist() -> Path:
    env_path = os.environ.get("GITHUB_WATCHLIST")
    if env_path:
        return Path(env_path).expanduser()
    local_path = ROOT / "watchlist.local.json"
    if local_path.exists():
        return local_path
    return ROOT / "watchlist.json"


DEFAULT_WATCHLIST = resolve_default_watchlist()


@dataclass
class ProjectStatus:
    kind: str
    path: str
    repo: str
    name: str
    visibility: str
    branch: str
    upstream: str | None
    ahead: int | None
    behind: int | None
    dirty: bool
    branch_status: str
    current_tag: str | None
    latest_release: str | None
    latest_release_published_at: str | None
    release_status: str
    release_commits_behind: int | None
    fetch_status: str
    error: str | None = None
    package: str | None = None
    configured_version: str | None = None
    current_version: str | None = None
    latest_version: str | None = None
    source: str | None = None
    category: str = "project"
    default_branch: str | None = None
    private: bool | None = None
    stars: int | None = None
    description: str | None = None
    html_url: str | None = None
    dirty_files: int = 0
    untracked_files: int = 0
    modified_files: int = 0
    deleted_files: int = 0


class ActionError(RuntimeError):
    """Raised when a local project action is unsafe or cannot be completed."""


def repo_slug_from_url(url: str) -> str | None:
    url = url.strip()
    patterns = [
        r"^https://github\.com/([^/\s]+/[^/\s]+?)(?:\.git)?/?$",
        r"^git@github\.com:([^/\s]+/[^/\s]+?)(?:\.git)?/?$",
        r"^ssh://git@github\.com/([^/\s]+/[^/\s]+?)(?:\.git)?/?$",
    ]
    for pattern in patterns:
        match = re.match(pattern, url)
        if match:
            return match.group(1)
    return None


def is_github_repo_slug(value: str | None) -> bool:
    return bool(value and GITHUB_REPO_RE.match(value))


def parse_ahead_behind(output: str) -> tuple[int, int]:
    parts = output.strip().split()
    if len(parts) != 2:
        raise ValueError(f"expected two rev-list counts, got: {output!r}")
    return int(parts[0]), int(parts[1])


def classify_branch_status(ahead: int | None, behind: int | None, dirty: bool) -> str:
    if behind and behind > 0:
        return "behind+dirty" if dirty else "behind"
    if ahead and ahead > 0:
        return "ahead+dirty" if dirty else "ahead"
    return "dirty" if dirty else "sync"


def select_latest_release(
    releases: list[dict[str, Any]], include_prereleases: bool
) -> dict[str, Any] | None:
    candidates = []
    for release in releases:
        if release.get("draft"):
            continue
        if release.get("prerelease") and not include_prereleases:
            continue
        candidates.append(release)
    candidates.sort(key=lambda item: item.get("published_at") or "", reverse=True)
    return candidates[0] if candidates else None


def classify_release_status(
    current_tag: str | None, latest_tag: str | None, commits_behind: int | None
) -> str:
    if not latest_tag:
        return "release-unavailable"
    if not current_tag:
        return "release-unknown-local-tag"
    if current_tag == latest_tag:
        return "release-current"
    if commits_behind == 0:
        return "release-in-history"
    return "release-behind"


def classify_package_version(configured_version: str | None, latest_version: str | None) -> str:
    if not latest_version:
        return "version-unavailable"
    if not configured_version or configured_version in {"latest", "@latest"}:
        return "package-floating-latest"
    if configured_version == latest_version:
        return "package-current"
    return "package-behind"


def run_git(path: str, args: list[str], check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", path, *args],
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def run_gh(args: list[str], timeout: int = 8) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.setdefault("GH_NO_UPDATE_NOTIFIER", "1")
    command = ["gh", *args]
    try:
        return subprocess.run(
            command,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            command,
            124,
            stdout="",
            stderr=f"timed out after {timeout}s",
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(command, 127, stdout="", stderr="gh not found")


def run_npm(args: list[str], timeout: int = 8) -> subprocess.CompletedProcess[str]:
    command = ["npm", *args]
    try:
        return subprocess.run(
            command,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            command,
            124,
            stdout="",
            stderr=f"timed out after {timeout}s",
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(command, 127, stdout="", stderr="npm not found")


def git_output(path: str, args: list[str]) -> str | None:
    result = run_git(path, args)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def parse_git_porcelain(output: str | None) -> dict[str, int]:
    summary = {
        "dirty_files": 0,
        "untracked_files": 0,
        "modified_files": 0,
        "deleted_files": 0,
    }
    for line in (output or "").splitlines():
        if not line:
            continue
        code = line[:2]
        summary["dirty_files"] += 1
        if code == "??":
            summary["untracked_files"] += 1
            continue
        if "D" in code:
            summary["deleted_files"] += 1
        if any(marker in code for marker in ("M", "A", "R", "C", "U", "T")):
            summary["modified_files"] += 1
    return summary


def infer_repo_from_git_remote(path: str) -> str | None:
    remotes = git_output(path, ["remote", "-v"])
    if not remotes:
        return None
    for line in remotes.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        slug = repo_slug_from_url(parts[1])
        if slug:
            return slug
    return None


def infer_category(path: str, project: dict[str, Any] | None = None) -> str:
    project = project or {}
    explicit = project.get("category") or project.get("kind")
    if explicit:
        return str(explicit)

    type_name = str(project.get("type") or project.get("ecosystem") or "").lower()
    text = f"{project.get('name', '')} {project.get('source', '')} {path}".lower()
    path_obj = Path(path)

    if type_name in {"npm", "npm-package"}:
        return "mcp" if "mcp" in text else "npm"
    if (path_obj / "SKILL.md").exists() or "/skills/" in text or text.endswith("-skill"):
        return "skill"
    if "/plugins/" in text or "/plugin" in text:
        return "plugin"
    if "mcp" in text:
        return "mcp"
    return "project"


def github_repo_metadata(repo: str | None) -> dict[str, Any]:
    if not is_github_repo_slug(repo):
        return {}
    assert repo is not None
    if repo in GITHUB_METADATA_CACHE:
        return GITHUB_METADATA_CACHE[repo]

    result = run_gh(["api", f"repos/{repo}"], timeout=8)
    if result.returncode != 0:
        GITHUB_METADATA_CACHE[repo] = {}
        return {}

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        GITHUB_METADATA_CACHE[repo] = {}
        return {}

    metadata = {
        "visibility": payload.get("visibility")
        or ("private" if payload.get("private") else "public"),
        "private": payload.get("private"),
        "stars": payload.get("stargazers_count"),
        "default_branch": payload.get("default_branch"),
        "description": payload.get("description"),
        "html_url": payload.get("html_url"),
    }
    GITHUB_METADATA_CACHE[repo] = metadata
    return metadata


def resolve_upstream(path: str, branch: str) -> str | None:
    upstream = git_output(path, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    if upstream:
        return upstream
    if branch and branch != "DETACHED":
        remote_ref = f"origin/{branch}"
        if run_git(path, ["rev-parse", "--verify", remote_ref]).returncode == 0:
            return remote_ref
    return None


def latest_release(repo: str, include_prereleases: bool) -> tuple[str | None, str | None]:
    result = run_gh(["api", f"repos/{repo}/releases"])
    if result.returncode != 0:
        return None, None
    try:
        releases = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None, None
    selected = select_latest_release(releases, include_prereleases)
    if not selected:
        return None, None
    return selected.get("tag_name"), selected.get("published_at")


def latest_npm_version(package: str) -> str | None:
    result = run_npm(["view", package, "version", "--silent"])
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def count_commits_behind_ref(path: str, ref: str) -> int | None:
    if run_git(path, ["rev-parse", "--verify", f"{ref}^{{}}"]).returncode != 0:
        return None
    output = git_output(path, ["rev-list", "--count", f"HEAD..{ref}"])
    return int(output) if output and output.isdigit() else None


def check_npm_package(project: dict[str, Any]) -> ProjectStatus:
    package = project["package"]
    name = project.get("name") or package
    source = project.get("source") or "npm"
    configured_version = (
        project.get("configuredVersion")
        or project.get("configured_version")
        or project.get("version")
        or "latest"
    )
    latest_version = latest_npm_version(package)
    version_status = classify_package_version(configured_version, latest_version)
    current_version = latest_version if version_status == "package-floating-latest" else configured_version
    branch_status = "sync"
    fetch_status = "ok" if latest_version else "fail:npm"

    return ProjectStatus(
        kind="npm",
        path=project.get("path") or f"npm:{package}@{configured_version}",
        repo=project.get("repo") or package,
        name=name,
        visibility=project.get("visibility") or "public",
        branch=source,
        upstream="npm registry",
        ahead=None,
        behind=0,
        dirty=False,
        branch_status=branch_status,
        current_tag=current_version,
        latest_release=latest_version,
        latest_release_published_at=None,
        release_status=version_status,
        release_commits_behind=None,
        fetch_status=fetch_status,
        error=None if latest_version else f"npm view {package} failed",
        package=package,
        configured_version=configured_version,
        current_version=current_version,
        latest_version=latest_version,
        source=source,
        category=infer_category(project.get("path") or source, project),
    )


def check_project(project: dict[str, Any], no_fetch: bool, include_prereleases: bool) -> ProjectStatus:
    path = project["path"]
    repo = project.get("repo") or infer_repo_from_git_remote(path) or ""
    metadata = github_repo_metadata(repo)
    name = project.get("name") or repo or Path(path).name
    configured_visibility = project.get("visibility")
    visibility = (
        metadata.get("visibility")
        if not configured_visibility or configured_visibility == "unknown"
        else configured_visibility
    ) or "unknown"
    category = infer_category(path, project)

    if not Path(path).exists():
        return ProjectStatus(
            kind="git",
            path=path,
            repo=repo,
            name=name,
            visibility=visibility,
            branch="missing",
            upstream=None,
            ahead=None,
            behind=None,
            dirty=False,
            branch_status="missing",
            current_tag=None,
            latest_release=None,
            latest_release_published_at=None,
            release_status="release-unavailable",
            release_commits_behind=None,
            fetch_status="skipped",
            error="path does not exist",
            source=project.get("source"),
            category=category,
            default_branch=metadata.get("default_branch"),
            private=metadata.get("private"),
            stars=metadata.get("stars"),
            description=metadata.get("description"),
            html_url=metadata.get("html_url"),
        )

    fetch_status = "skipped"
    if not no_fetch:
        fetch = run_git(path, ["fetch", "--all", "--tags", "--prune"])
        fetch_status = "ok" if fetch.returncode == 0 else f"fail:{fetch.returncode}"

    branch = git_output(path, ["symbolic-ref", "--short", "HEAD"])
    if not branch:
        branch = git_output(path, ["rev-parse", "--short", "HEAD"]) or "DETACHED"

    upstream = resolve_upstream(path, branch)
    ahead = None
    behind = None
    if upstream:
        counts = git_output(path, ["rev-list", "--left-right", "--count", f"HEAD...{upstream}"])
        if counts:
            ahead, behind = parse_ahead_behind(counts)

    status_output = git_output(path, ["status", "--porcelain"]) or ""
    dirty_summary = parse_git_porcelain(status_output)
    dirty = bool(status_output)
    branch_status = classify_branch_status(ahead, behind, dirty)

    current_tag = git_output(path, ["describe", "--tags", "--abbrev=0"])
    latest_tag, published_at = latest_release(repo, include_prereleases) if repo else (None, None)
    release_commits_behind = count_commits_behind_ref(path, latest_tag) if latest_tag else None
    release_status = classify_release_status(current_tag, latest_tag, release_commits_behind)

    return ProjectStatus(
        kind="git",
        path=path,
        repo=repo,
        name=name,
        visibility=visibility,
        branch=branch,
        upstream=upstream,
        ahead=ahead,
        behind=behind,
        dirty=dirty,
        branch_status=branch_status,
        current_tag=current_tag,
        latest_release=latest_tag,
        latest_release_published_at=published_at,
        release_status=release_status,
        release_commits_behind=release_commits_behind,
        fetch_status=fetch_status,
        source=project.get("source"),
        category=category,
        default_branch=metadata.get("default_branch"),
        private=metadata.get("private"),
        stars=metadata.get("stars"),
        description=metadata.get("description"),
        html_url=metadata.get("html_url"),
        **dirty_summary,
    )


def check_entry(entry: dict[str, Any], no_fetch: bool, include_prereleases: bool) -> ProjectStatus:
    if entry.get("type") in {"npm", "npm-package"} or entry.get("ecosystem") == "npm":
        return check_npm_package(entry)
    return check_project(entry, no_fetch, include_prereleases)


def load_watchlist(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    projects = data.get("projects")
    if not isinstance(projects, list):
        raise ValueError(f"{path} must contain a projects list")
    packages = data.get("packages", [])
    if not isinstance(packages, list):
        raise ValueError(f"{path} packages must be a list")
    return [*projects, *packages]


def default_scan_roots() -> list[Path]:
    env_value = os.environ.get("GITHUB_PROJECT_SCAN_ROOTS")
    if env_value:
        return [Path(item).expanduser() for item in env_value.split(os.pathsep) if item.strip()]

    home = Path.home()
    roots = [
        home / "projects",
        home / ".cctb",
        home / ".codex" / "skills",
        home / ".codex" / "plugins",
        home / ".codex" / "vendor_imports",
        home / ".agents" / "skills",
        home / ".claude" / "skills",
        home / ".claude" / "plugins",
        home / ".hermes",
        home / ".xhsv2",
    ]
    return [path for path in roots if path.exists()]


def discover_git_repositories(
    roots: list[Path] | None = None,
    max_depth: int = 7,
    limit: int = 500,
) -> list[Path]:
    search_roots = roots or default_scan_roots()
    discovered: list[Path] = []
    seen: set[Path] = set()

    def walk(path: Path, depth: int) -> None:
        if len(discovered) >= limit:
            return
        try:
            resolved = path.resolve()
        except OSError:
            return
        if resolved in seen:
            return
        seen.add(resolved)

        if (path / ".git").exists():
            discovered.append(path)
            return
        if depth >= max_depth:
            return

        try:
            entries = list(os.scandir(path))
        except OSError:
            return

        for entry in entries:
            if entry.name in SKIP_SCAN_DIRS:
                continue
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            walk(Path(entry.path), depth + 1)

    for root in search_roots:
        if root.exists():
            walk(root.expanduser(), 0)

    return discovered


def project_entry_from_git_path(path: Path) -> dict[str, Any]:
    path_str = str(path)
    repo = infer_repo_from_git_remote(path_str) or ""
    return {
        "name": repo.split("/")[-1] if repo else path.name,
        "repo": repo,
        "visibility": "unknown",
        "path": path_str,
        "source": "scan",
        "category": infer_category(path_str),
    }


def merge_project_entries(
    primary: list[dict[str, Any]],
    discovered: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged = list(primary)
    seen_paths = {
        str(Path(item["path"]).expanduser())
        for item in merged
        if item.get("path") and not str(item.get("path")).startswith("npm:")
    }
    seen_repos = {item.get("repo") for item in merged if item.get("repo")}

    for item in discovered:
        path_value = str(Path(item["path"]).expanduser())
        repo = item.get("repo")
        if path_value in seen_paths:
            continue
        if repo and repo in seen_repos:
            continue
        merged.append(item)
        seen_paths.add(path_value)
        if repo:
            seen_repos.add(repo)
    return merged


def load_inventory(
    watchlist_path: Path,
    include_discovered: bool = False,
    scan_roots: list[Path] | None = None,
) -> list[dict[str, Any]]:
    entries = load_watchlist(watchlist_path)
    if not include_discovered:
        return entries
    discovered = [project_entry_from_git_path(path) for path in discover_git_repositories(scan_roots)]
    return merge_project_entries(entries, discovered)


def normalize_github_repository(item: dict[str, Any]) -> dict[str, Any]:
    owner = item.get("owner") or {}
    return {
        "name": item.get("name"),
        "full_name": item.get("full_name"),
        "owner": owner.get("login"),
        "description": item.get("description"),
        "visibility": item.get("visibility") or ("private" if item.get("private") else "public"),
        "private": item.get("private"),
        "stars": item.get("stargazers_count"),
        "forks": item.get("forks_count"),
        "default_branch": item.get("default_branch"),
        "updated_at": item.get("updated_at"),
        "pushed_at": item.get("pushed_at"),
        "html_url": item.get("html_url"),
        "clone_url": item.get("clone_url"),
        "archived": item.get("archived"),
    }


def search_github_repositories(query: str, limit: int = 20) -> list[dict[str, Any]]:
    query = query.strip()
    if not query:
        return []

    limit = max(1, min(limit, 50))
    endpoint = f"search/repositories?q={quote_plus(query)}&per_page={limit}"
    result = run_gh(["api", endpoint], timeout=12)
    payload: dict[str, Any] | None = None
    if result.returncode == 0:
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            payload = None

    if payload is None:
        request = Request(
            f"https://api.github.com/search/repositories?q={quote_plus(query)}&per_page={limit}",
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "github-project-monitor",
            },
        )
        try:
            with urlopen(request, timeout=12) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception:
            return []

    return [normalize_github_repository(item) for item in payload.get("items", [])]


def default_install_root() -> Path:
    return Path(os.environ.get("GITHUB_STORE_INSTALL_ROOT", "~/projects")).expanduser()


def ensure_git_worktree(path: str) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        raise ActionError(f"path does not exist: {resolved}")
    if resolved == Path.home().resolve() or resolved.parent == resolved:
        raise ActionError(f"refusing unsafe path: {resolved}")
    result = run_git(str(resolved), ["rev-parse", "--is-inside-work-tree"])
    if result.returncode != 0:
        raise ActionError(f"not a git worktree: {resolved}")
    top = git_output(str(resolved), ["rev-parse", "--show-toplevel"])
    return Path(top).resolve() if top else resolved


def require_clean_worktree(path: Path) -> None:
    status_output = git_output(str(path), ["status", "--porcelain"]) or ""
    if status_output:
        summary = parse_git_porcelain(status_output)
        raise ActionError(
            "worktree is dirty: "
            f"{summary['dirty_files']} changed, {summary['untracked_files']} untracked"
        )


def install_repository(repo: str, install_root: str | None = None) -> dict[str, Any]:
    if not is_github_repo_slug(repo):
        raise ActionError(f"invalid GitHub repo: {repo}")

    root = Path(install_root).expanduser() if install_root else default_install_root()
    root.mkdir(parents=True, exist_ok=True)
    destination = root / repo.split("/")[-1]
    if destination.exists():
        raise ActionError(f"destination already exists: {destination}")

    if shutil.which("gh"):
        result = run_gh(["repo", "clone", repo, str(destination)], timeout=180)
    else:
        result = subprocess.run(
            ["git", "clone", f"https://github.com/{repo}.git", str(destination)],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=180,
        )

    if result.returncode != 0:
        raise ActionError((result.stderr or result.stdout or "clone failed").strip())

    return {
        "ok": True,
        "action": "install",
        "repo": repo,
        "path": str(destination),
        "message": f"installed {repo} to {destination}",
    }


def update_project_to_commit(path: str) -> dict[str, Any]:
    worktree = ensure_git_worktree(path)
    require_clean_worktree(worktree)

    fetch = run_git(str(worktree), ["fetch", "--all", "--tags", "--prune"])
    if fetch.returncode != 0:
        raise ActionError((fetch.stderr or fetch.stdout or "git fetch failed").strip())

    pull = run_git(str(worktree), ["pull", "--ff-only"])
    if pull.returncode != 0:
        raise ActionError((pull.stderr or pull.stdout or "git pull --ff-only failed").strip())

    return {
        "ok": True,
        "action": "updateCommit",
        "path": str(worktree),
        "message": pull.stdout.strip() or f"updated {worktree}",
    }


def update_project_to_release(
    path: str,
    repo: str | None = None,
    include_prereleases: bool = False,
) -> dict[str, Any]:
    worktree = ensure_git_worktree(path)
    require_clean_worktree(worktree)

    inferred_repo = repo or infer_repo_from_git_remote(str(worktree))
    if not is_github_repo_slug(inferred_repo):
        raise ActionError(f"cannot infer GitHub repo for {worktree}")

    fetch = run_git(str(worktree), ["fetch", "--all", "--tags", "--prune"])
    if fetch.returncode != 0:
        raise ActionError((fetch.stderr or fetch.stdout or "git fetch failed").strip())

    latest_tag, _published_at = latest_release(str(inferred_repo), include_prereleases)
    if not latest_tag:
        raise ActionError(f"no GitHub release found for {inferred_repo}")

    checkout = run_git(str(worktree), ["checkout", "--detach", latest_tag])
    if checkout.returncode != 0:
        raise ActionError((checkout.stderr or checkout.stdout or "git checkout failed").strip())

    return {
        "ok": True,
        "action": "updateRelease",
        "repo": inferred_repo,
        "path": str(worktree),
        "tag": latest_tag,
        "message": f"checked out {inferred_repo} at {latest_tag}",
    }


def trash_project(path: str, allow_dirty: bool = False) -> dict[str, Any]:
    worktree = ensure_git_worktree(path)
    if not allow_dirty:
        require_clean_worktree(worktree)

    trash_root = Path.home() / ".local" / "share" / "github-project-monitor" / "trash"
    trash_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    destination = trash_root / f"{worktree.name}-{stamp}"
    shutil.move(str(worktree), str(destination))

    return {
        "ok": True,
        "action": "uninstall",
        "path": str(worktree),
        "trashedTo": str(destination),
        "message": f"moved {worktree} to {destination}",
    }


def perform_action(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    if action == "install":
        return install_repository(str(payload.get("repo") or ""), payload.get("installRoot"))
    if action == "updateCommit":
        return update_project_to_commit(str(payload.get("path") or ""))
    if action == "updateRelease":
        return update_project_to_release(
            str(payload.get("path") or ""),
            payload.get("repo"),
            bool(payload.get("includePrereleases")),
        )
    if action == "uninstall":
        return trash_project(str(payload.get("path") or ""), bool(payload.get("allowDirty")))
    raise ActionError(f"unknown action: {action}")


def render_markdown(statuses: list[ProjectStatus]) -> str:
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    behind = [item for item in statuses if item.branch_status in {"behind", "behind+dirty"}]
    release_behind = [
        item for item in statuses if item.release_status in {"release-behind", "package-behind"}
    ]
    dirty = [item for item in statuses if item.dirty]

    lines = [
        f"# GitHub Watch Report - {now}",
        "",
        "## Summary",
        "",
        f"- Projects checked: {len(statuses)}",
        f"- Branch behind: {len(behind)}",
        f"- Release/package behind: {len(release_behind)}",
        f"- Dirty: {len(dirty)}",
        "",
        "## Projects",
        "",
        "| Name | Kind | Branch status | Ahead | Behind | Current tag | Latest release | Release status | Dirty | Path |",
        "|---|---|---|---:|---:|---|---|---|---:|---|",
    ]

    for item in sorted(statuses, key=lambda row: (row.branch_status == "sync", row.name)):
        lines.append(
            "| {name} | {kind} | {branch_status} | {ahead} | {behind} | {current_tag} | {latest_release} | "
            "{release_status} | {dirty} | `{path}` |".format(
                name=item.name,
                kind=item.kind,
                branch_status=item.branch_status,
                ahead=item.ahead if item.ahead is not None else "NA",
                behind=item.behind if item.behind is not None else "NA",
                current_tag=item.current_tag or "NA",
                latest_release=item.latest_release or "NA",
                release_status=item.release_status,
                dirty="yes" if item.dirty else "no",
                path=item.path,
            )
        )

    lines.extend(
        [
            "",
            "## Safe Branch Update Candidates",
            "",
        ]
    )
    clean_behind = [item for item in behind if not item.dirty and item.upstream]
    if clean_behind:
        for item in clean_behind:
            lines.append(f"- `git -C {item.path} pull --ff-only`")
    else:
        lines.append("- None")

    return "\n".join(lines) + "\n"


def command_check(args: argparse.Namespace) -> int:
    projects = load_watchlist(Path(args.watchlist))
    if args.only:
        needle = args.only.lower()
        projects = [
            project
            for project in projects
            if needle in project.get("path", "").lower()
            or needle in project.get("repo", "").lower()
            or needle in project.get("package", "").lower()
            or needle in project.get("name", "").lower()
        ]

    statuses = [
        check_entry(project, args.no_fetch, args.include_prereleases)
        for project in projects
    ]

    if args.format == "json":
        output = json.dumps([asdict(item) for item in statuses], ensure_ascii=False, indent=2)
    else:
        output = render_markdown(statuses)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output, encoding="utf-8")
    print(output)
    return 0


def command_list(args: argparse.Namespace) -> int:
    projects = load_watchlist(Path(args.watchlist))
    for project in projects:
        target = project.get("repo") or project.get("package")
        print(f"{project.get('name') or target}\t{target}\t{project.get('path')}")
    return 0


def command_web(args: argparse.Namespace) -> int:
    from github_watch_web import serve

    serve(host=args.host, port=args.port, watchlist_path=Path(args.watchlist))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Monitor local GitHub projects.")
    parser.add_argument(
        "--watchlist",
        default=str(DEFAULT_WATCHLIST),
        help="Path to watchlist JSON.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("check", help="Check branch and release status.")
    check.add_argument("--format", choices=["markdown", "json"], default="markdown")
    check.add_argument("--output", help="Write report to a file.")
    check.add_argument("--no-fetch", action="store_true", help="Do not fetch remotes/tags first.")
    check.add_argument("--include-prereleases", action="store_true")
    check.add_argument("--only", help="Filter by name, repo, or path substring.")
    check.set_defaults(func=command_check)

    list_cmd = subparsers.add_parser("list", help="List watched projects.")
    list_cmd.set_defaults(func=command_list)

    web = subparsers.add_parser("web", help="Start the local web dashboard.")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8765)
    web.set_defaults(func=command_web)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
