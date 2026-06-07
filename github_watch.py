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
INSTALL_AGENTS = {
    "codex": "Codex",
    "claude": "Claude Code",
    "direct": "Git only",
}
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
DEFAULT_GIT_TIMEOUT = 60
STATE_ROOT = Path.home() / ".local" / "share" / "github-project-monitor"


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
    diagnostic: str | None = None
    post_update_steps: list[dict[str, str]] | None = None
    post_update_last_status: str | None = None
    last_dirty_seen_at: str | None = None
    previous_branch_status: str | None = None
    previous_release_status: str | None = None
    status_trend: str | None = None
    service_name: str | None = None
    service_running: bool | None = None
    service_ports: list[int] | None = None
    service_restart: str | None = None
    service_log: str | None = None


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


def run_git(
    path: str,
    args: list[str],
    check: bool = False,
    timeout: int = DEFAULT_GIT_TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    command = ["git", "-C", path, *args]
    env = os.environ.copy()
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    try:
        return subprocess.run(
            command,
            check=check,
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
        return subprocess.CompletedProcess(command, 127, stdout="", stderr="git not found")


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


def current_listening_services() -> list[dict[str, Any]]:
    ps_result = subprocess.run(
        ["ps", "-axo", "pid=,command="],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    commands: dict[int, str] = {}
    if ps_result.returncode == 0:
        for line in ps_result.stdout.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            pid_text, _, command = stripped.partition(" ")
            if pid_text.isdigit():
                commands[int(pid_text)] = command.strip()

    lsof_result = subprocess.run(
        ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    by_pid: dict[int, dict[str, Any]] = {}
    if lsof_result.returncode != 0:
        return []
    for line in lsof_result.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2 or not parts[1].isdigit():
            continue
        pid = int(parts[1])
        match = re.search(r":(\d+)\s+\(LISTEN\)", line)
        if not match:
            continue
        item = by_pid.setdefault(
            pid,
            {
                "pid": pid,
                "command": commands.get(pid, parts[0]),
                "ports": [],
            },
        )
        item["ports"].append(int(match.group(1)))
    return list(by_pid.values())


def data_root() -> Path:
    root = Path(os.environ.get("GITHUB_STORE_STATE_ROOT", str(STATE_ROOT))).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root


def history_file() -> Path:
    root = data_root() / "history"
    root.mkdir(parents=True, exist_ok=True)
    return root / "snapshots.jsonl"


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


def normalize_post_update_steps(value: Any) -> list[dict[str, str]]:
    if not value:
        return []
    raw_steps = value if isinstance(value, list) else [value]
    steps: list[dict[str, str]] = []
    for index, item in enumerate(raw_steps, start=1):
        if isinstance(item, str):
            command = item.strip()
            if command:
                steps.append({"name": f"Step {index}", "command": command})
            continue
        if isinstance(item, dict):
            command = str(item.get("command") or "").strip()
            if not command:
                continue
            steps.append(
                {
                    "name": str(item.get("name") or f"Step {index}"),
                    "command": command,
                }
            )
    return steps


def run_post_update_steps(
    steps: list[dict[str, str]],
    cwd: Path,
    timeout: int = 600,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    env = os.environ.copy()
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    for step in steps:
        command = step["command"]
        try:
            result = subprocess.run(
                command,
                cwd=str(cwd),
                shell=True,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                timeout=timeout,
            )
            status = "ok" if result.returncode == 0 else "fail"
            results.append(
                {
                    "name": step.get("name") or command,
                    "command": command,
                    "status": status,
                    "returncode": result.returncode,
                    "stdout": text_tail(result.stdout, 2000),
                    "stderr": text_tail(result.stderr, 2000),
                }
            )
            if result.returncode != 0:
                break
        except subprocess.TimeoutExpired:
            results.append(
                {
                    "name": step.get("name") or command,
                    "command": command,
                    "status": "timeout",
                    "returncode": 124,
                    "stdout": "",
                    "stderr": f"timed out after {timeout}s",
                }
            )
            break
    return results


def diagnostic_for_status(item: ProjectStatus) -> str | None:
    if item.error:
        return item.error
    if item.branch_status == "missing":
        return "Local path does not exist."
    if item.branch_status == "invalid":
        return "Local path exists but is not a git worktree."
    if item.fetch_status.startswith("fail:"):
        return f"Git fetch failed with {item.fetch_status}."
    if item.kind == "npm" and item.release_status == "version-unavailable":
        return f"npm version lookup failed for {item.package or item.repo}."
    if item.release_status == "release-unavailable":
        if item.visibility == "private":
            return "No release found, or GitHub CLI cannot access this private repository."
        return "No GitHub release is available for this repository."
    if item.release_status == "release-unknown-local-tag":
        return "Latest release exists, but the local checkout is not currently on a release tag."
    if item.branch_status in {"behind", "behind+dirty"}:
        return f"Local branch is {item.behind or 0} commits behind upstream."
    if item.release_status in {"release-behind", "package-behind"}:
        return "Installed version is behind the latest release or package version."
    return None


def status_severity(item: ProjectStatus) -> int:
    if item.branch_status in {"error", "invalid", "missing"}:
        return 4
    if item.branch_status in {"behind+dirty"}:
        return 3
    if item.branch_status in {"behind"} or item.release_status in {"release-behind", "package-behind"}:
        return 2
    if item.branch_status in {"dirty", "ahead+dirty"}:
        return 1
    return 0


def build_update_plan(statuses: list[ProjectStatus]) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for item in statuses:
        if item.kind == "npm":
            continue
        needs_branch_update = item.branch_status in {"behind", "behind+dirty"}
        needs_release_update = item.release_status == "release-behind"
        if not needs_branch_update and not needs_release_update:
            continue
        if item.dirty:
            blocked.append(
                {
                    "name": item.name,
                    "repo": item.repo,
                    "path": item.path,
                    "reason": "dirty worktree blocks automatic update",
                }
            )
            continue
        if item.branch_status in {"missing", "invalid", "error"}:
            blocked.append(
                {
                    "name": item.name,
                    "repo": item.repo,
                    "path": item.path,
                    "reason": item.error or item.branch_status,
                }
            )
            continue
        if needs_branch_update and item.upstream:
            actions.append(
                {
                    "action": "updateCommit",
                    "name": item.name,
                    "repo": item.repo,
                    "path": item.path,
                    "label": f"Fast-forward {item.name}",
                    "reason": f"{item.behind or 0} commits behind {item.upstream}",
                    "postUpdateSteps": item.post_update_steps or [],
                }
            )
            continue
        if needs_release_update:
            actions.append(
                {
                    "action": "updateRelease",
                    "name": item.name,
                    "repo": item.repo,
                    "path": item.path,
                    "label": f"Checkout latest release for {item.name}",
                    "reason": f"{item.current_tag or 'local'} -> {item.latest_release}",
                    "postUpdateSteps": item.post_update_steps or [],
                }
            )
    return {
        "actions": actions,
        "blocked": blocked,
        "summary": {
            "actions": len(actions),
            "blocked": len(blocked),
            "postUpdateSteps": sum(len(item.get("postUpdateSteps") or []) for item in actions),
        },
    }


def save_history_snapshot(
    statuses: list[ProjectStatus],
    summary: dict[str, Any],
    path: Path | None = None,
    now: str | None = None,
) -> Path:
    target = path or history_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    timestamp = now or datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    payload = {
        "createdAt": timestamp,
        "summary": summary,
        "projects": [
            {
                "name": item.name,
                "repo": item.repo,
                "path": item.path,
                "branchStatus": item.branch_status,
                "releaseStatus": item.release_status,
                "dirty": item.dirty,
            }
            for item in statuses
        ],
    }
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return target


def load_history_snapshots(path: Path | None = None, limit: int = 20) -> list[dict[str, Any]]:
    source = path or history_file()
    if not source.exists():
        return []
    lines = source.read_text(encoding="utf-8", errors="replace").splitlines()
    snapshots: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        if not line.strip():
            continue
        try:
            snapshots.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return snapshots


def attach_history_insights(
    statuses: list[ProjectStatus],
    snapshots: list[dict[str, Any]],
) -> None:
    by_path = {item.path: item for item in statuses}
    for snapshot in snapshots:
        created_at = snapshot.get("createdAt")
        for row in snapshot.get("projects", []):
            status = by_path.get(row.get("path"))
            if not status:
                continue
            if row.get("dirty"):
                status.last_dirty_seen_at = created_at
            status.previous_branch_status = row.get("branchStatus")
            status.previous_release_status = row.get("releaseStatus")

    for status in statuses:
        previous = ProjectStatus(
            kind=status.kind,
            path=status.path,
            repo=status.repo,
            name=status.name,
            visibility=status.visibility,
            branch=status.previous_branch_status or status.branch_status,
            upstream=status.upstream,
            ahead=status.ahead,
            behind=status.behind,
            dirty=status.previous_branch_status in {"dirty", "behind+dirty", "ahead+dirty"},
            branch_status=status.previous_branch_status or status.branch_status,
            current_tag=status.current_tag,
            latest_release=status.latest_release,
            latest_release_published_at=status.latest_release_published_at,
            release_status=status.previous_release_status or status.release_status,
            release_commits_behind=status.release_commits_behind,
            fetch_status=status.fetch_status,
        )
        old_score = status_severity(previous)
        new_score = status_severity(status)
        if old_score > new_score:
            status.status_trend = "improved"
        elif old_score < new_score:
            status.status_trend = "worse"
        elif snapshots:
            status.status_trend = "unchanged"


def detect_project_service(
    project: dict[str, Any],
    listeners: list[dict[str, Any]],
) -> dict[str, Any]:
    service = project.get("service") if isinstance(project.get("service"), dict) else {}
    name = str(service.get("name") or project.get("name") or Path(str(project.get("path") or "")).name)
    restart = service.get("restart")
    log = service.get("log")
    match = str(service.get("match") or project.get("path") or project.get("name") or "")
    matches = [
        listener
        for listener in listeners
        if match and match in str(listener.get("command") or "")
    ]
    ports = sorted({port for item in matches for port in item.get("ports", [])})
    if not service and not matches:
        return {
            "name": None,
            "running": None,
            "ports": [],
            "restart": None,
            "log": None,
        }
    return {
        "name": name,
        "running": bool(matches),
        "ports": ports,
        "restart": restart,
        "log": log,
    }


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

    status = ProjectStatus(
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
        post_update_steps=normalize_post_update_steps(
            project.get("postUpdate") or project.get("post_update")
        ),
    )
    status.diagnostic = diagnostic_for_status(status)
    return status


def error_project_status(project: dict[str, Any], error: Exception | str) -> ProjectStatus:
    path = str(project.get("path") or "")
    package = project.get("package")
    repo = str(project.get("repo") or package or "")
    name = str(project.get("name") or repo or path or "unknown")
    kind = "npm" if project.get("type") in {"npm", "npm-package"} or package else "git"
    configured_version = (
        project.get("configuredVersion")
        or project.get("configured_version")
        or project.get("version")
    )
    status = ProjectStatus(
        kind=kind,
        path=path,
        repo=repo,
        name=name,
        visibility=str(project.get("visibility") or "unknown"),
        branch="error",
        upstream=None,
        ahead=None,
        behind=None,
        dirty=False,
        branch_status="error",
        current_tag=None,
        latest_release=None,
        latest_release_published_at=None,
        release_status="release-unavailable",
        release_commits_behind=None,
        fetch_status="error",
        error=str(error),
        package=package,
        configured_version=configured_version,
        source=project.get("source"),
        category=infer_category(path or str(project.get("source") or ""), project),
        post_update_steps=normalize_post_update_steps(
            project.get("postUpdate") or project.get("post_update")
        ),
    )
    status.diagnostic = diagnostic_for_status(status)
    return status


def invalid_git_status(project: dict[str, Any], message: str) -> ProjectStatus:
    path = str(project.get("path") or "")
    repo = str(project.get("repo") or infer_repo_from_git_remote(path) or "")
    name = str(project.get("name") or repo or Path(path).name or "unknown")
    status = ProjectStatus(
        kind="git",
        path=path,
        repo=repo,
        name=name,
        visibility=str(project.get("visibility") or "unknown"),
        branch="invalid",
        upstream=None,
        ahead=None,
        behind=None,
        dirty=False,
        branch_status="invalid",
        current_tag=None,
        latest_release=None,
        latest_release_published_at=None,
        release_status="release-unavailable",
        release_commits_behind=None,
        fetch_status="skipped",
        error=message,
        source=project.get("source"),
        category=infer_category(path, project),
        post_update_steps=normalize_post_update_steps(
            project.get("postUpdate") or project.get("post_update")
        ),
    )
    status.diagnostic = diagnostic_for_status(status)
    return status


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
        status = ProjectStatus(
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
            post_update_steps=normalize_post_update_steps(
                project.get("postUpdate") or project.get("post_update")
            ),
        )
        status.diagnostic = diagnostic_for_status(status)
        return status

    is_worktree = run_git(path, ["rev-parse", "--is-inside-work-tree"])
    if is_worktree.returncode != 0:
        return invalid_git_status(project, "not a git worktree")

    fetch_status = "skipped"
    if not no_fetch:
        fetch = run_git(path, ["fetch", "--all", "--prune", "--no-tags"], timeout=180)
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

    status = ProjectStatus(
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
        post_update_steps=normalize_post_update_steps(
            project.get("postUpdate") or project.get("post_update")
        ),
        **dirty_summary,
    )
    status.diagnostic = diagnostic_for_status(status)
    return status


def check_entry(entry: dict[str, Any], no_fetch: bool, include_prereleases: bool) -> ProjectStatus:
    if entry.get("type") in {"npm", "npm-package"} or entry.get("ecosystem") == "npm":
        return check_npm_package(entry)
    return check_project(entry, no_fetch, include_prereleases)


def check_entry_safe(entry: dict[str, Any], no_fetch: bool, include_prereleases: bool) -> ProjectStatus:
    try:
        return check_entry(entry, no_fetch, include_prereleases)
    except Exception as error:
        return error_project_status(entry, error)


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
    for item in discovered:
        path_value = str(Path(item["path"]).expanduser())
        if path_value in seen_paths:
            continue
        merged.append(item)
        seen_paths.add(path_value)
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


def normalize_install_agent(value: str | None) -> str:
    agent = (value or "codex").strip().lower()
    if not agent:
        agent = "codex"
    if agent in {"git", "none", "manual"}:
        agent = "direct"
    if agent not in INSTALL_AGENTS:
        raise ActionError(f"unsupported installer agent: {value}")
    return agent


def installer_prompt(repo: str, destination: Path) -> str:
    return (
        "You are helping a non-technical user install a GitHub project locally.\n"
        f"Repository: {repo}\n"
        f"Local path: {destination}\n\n"
        "Inspect the project, read its README/package files, install the normal dependencies, "
        "and run the lightest useful verification command if one is obvious. Keep changes inside "
        "this project directory. Do not publish, deploy, delete user files, or make unrelated "
        "changes. Finish with a short summary of what you installed, how to run it, and any "
        "remaining issue."
    )


def build_install_agent_command(
    agent: str,
    repo: str,
    destination: Path,
    summary_path: Path,
) -> tuple[list[str], Path]:
    prompt = installer_prompt(repo, destination)
    if agent == "codex":
        return (
            [
                "codex",
                "exec",
                "--cd",
                str(destination),
                "--skip-git-repo-check",
                "--dangerously-bypass-approvals-and-sandbox",
                "-o",
                str(summary_path),
                prompt,
            ],
            destination,
        )
    if agent == "claude":
        return (
            [
                "claude",
                "-p",
                "--permission-mode",
                "bypassPermissions",
                "--output-format",
                "text",
                "--add-dir",
                str(destination),
                prompt,
            ],
            destination,
        )
    raise ActionError(f"unsupported installer agent: {agent}")


def install_log_dir() -> Path:
    root = Path.home() / ".local" / "share" / "github-project-monitor" / "install-logs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def partial_install_dir() -> Path:
    root = Path.home() / ".local" / "share" / "github-project-monitor" / "partial"
    root.mkdir(parents=True, exist_ok=True)
    return root


def quarantine_partial_install(destination: Path) -> Path | None:
    if not destination.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    partial_root = partial_install_dir()
    partial_destination = partial_root / f"{destination.name}-{stamp}"
    counter = 2
    while partial_destination.exists():
        partial_destination = partial_root / f"{destination.name}-{stamp}-{counter}"
        counter += 1
    shutil.move(str(destination), str(partial_destination))
    return partial_destination


def text_tail(value: str, limit: int = 4000) -> str:
    value = value.strip()
    return value if len(value) <= limit else value[-limit:]


def run_install_agent(agent: str, repo: str, destination: Path) -> dict[str, str]:
    agent = normalize_install_agent(agent)
    if agent == "direct":
        return {"agent": agent, "summary": "cloned without an installer agent", "log": ""}
    if not shutil.which(agent):
        raise ActionError(f"{INSTALL_AGENTS[agent]} CLI not found")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_repo = repo.replace("/", "__")
    log_path = install_log_dir() / f"{stamp}-{safe_repo}-{agent}.log"
    summary_path = install_log_dir() / f"{stamp}-{safe_repo}-{agent}-summary.txt"
    command, cwd = build_install_agent_command(agent, repo, destination, summary_path)
    env = os.environ.copy()
    env.setdefault("NO_COLOR", "1")
    env.setdefault("TERM", "dumb")

    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            timeout=900,
        )
    except subprocess.TimeoutExpired as error:
        raise ActionError(f"{INSTALL_AGENTS[agent]} installer timed out after 900s") from error
    except FileNotFoundError as error:
        raise ActionError(f"{INSTALL_AGENTS[agent]} CLI not found") from error

    log_path.write_text(
        "\n".join(
            [
                f"$ {' '.join(command[:-1])} <prompt>",
                "",
                "## stdout",
                result.stdout,
                "",
                "## stderr",
                result.stderr,
            ]
        ),
        encoding="utf-8",
    )
    summary = ""
    if summary_path.exists():
        summary = summary_path.read_text(encoding="utf-8", errors="replace").strip()
    summary = summary or result.stdout.strip() or result.stderr.strip()

    if result.returncode != 0:
        raise ActionError(
            f"{INSTALL_AGENTS[agent]} installer failed after cloning to {destination}: "
            f"{text_tail(summary or 'no output')}. Log: {log_path}"
        )

    return {
        "agent": agent,
        "summary": text_tail(summary),
        "log": str(log_path),
    }


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


def primary_remote(path: str) -> str | None:
    output = git_output(path, ["remote"])
    remotes = [line.strip() for line in (output or "").splitlines() if line.strip()]
    if "origin" in remotes:
        return "origin"
    return remotes[0] if remotes else None


def fetch_remotes_without_tags(path: str) -> None:
    fetch = run_git(path, ["fetch", "--all", "--prune", "--no-tags"], timeout=180)
    if fetch.returncode != 0:
        raise ActionError((fetch.stderr or fetch.stdout or "git fetch failed").strip())


def fetch_tag(path: str, tag: str) -> None:
    remote = primary_remote(path)
    if not remote:
        if run_git(path, ["rev-parse", "--verify", f"{tag}^{{}}"]).returncode == 0:
            return
        raise ActionError(f"no git remote configured for fetching tag {tag}")

    fetch = run_git(
        path,
        ["fetch", "--force", remote, f"refs/tags/{tag}:refs/tags/{tag}"],
        timeout=180,
    )
    if fetch.returncode != 0:
        raise ActionError((fetch.stderr or fetch.stdout or f"git fetch tag {tag} failed").strip())


def require_clean_worktree(path: Path) -> None:
    status_output = git_output(str(path), ["status", "--porcelain"]) or ""
    if status_output:
        summary = parse_git_porcelain(status_output)
        raise ActionError(
            "worktree is dirty: "
            f"{summary['dirty_files']} changed, {summary['untracked_files']} untracked"
        )


def install_repository(
    repo: str,
    install_root: str | None = None,
    installer_agent: str | None = None,
) -> dict[str, Any]:
    if not is_github_repo_slug(repo):
        raise ActionError(f"invalid GitHub repo: {repo}")
    agent = normalize_install_agent(installer_agent)

    root = Path(install_root).expanduser() if install_root else default_install_root()
    resolved_root = root.resolve()
    if resolved_root == Path.home().resolve() or resolved_root.parent == resolved_root:
        raise ActionError(f"refusing unsafe install root: {resolved_root}")
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

    try:
        agent_result = run_install_agent(agent, repo, destination) if agent != "direct" else {
            "agent": "direct",
            "summary": "cloned without an installer agent",
            "log": "",
        }
    except ActionError as error:
        partial_destination = quarantine_partial_install(destination)
        partial_message = (
            f"; partial clone moved to {partial_destination}" if partial_destination else ""
        )
        raise ActionError(f"{error}{partial_message}") from error
    summary = agent_result.get("summary") or ""
    message = (
        f"installed {repo} to {destination} with {INSTALL_AGENTS[agent]}"
        if agent != "direct"
        else f"installed {repo} to {destination}"
    )

    return {
        "ok": True,
        "action": "install",
        "repo": repo,
        "path": str(destination),
        "installerAgent": agent_result["agent"],
        "agentSummary": summary,
        "agentLog": agent_result.get("log") or "",
        "message": message,
    }


def update_project_to_commit(path: str) -> dict[str, Any]:
    worktree = ensure_git_worktree(path)
    require_clean_worktree(worktree)
    fetch_remotes_without_tags(str(worktree))

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

    latest_tag, _published_at = latest_release(str(inferred_repo), include_prereleases)
    if not latest_tag:
        raise ActionError(f"no GitHub release found for {inferred_repo}")

    fetch_remotes_without_tags(str(worktree))
    fetch_tag(str(worktree), latest_tag)

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
        return install_repository(
            str(payload.get("repo") or ""),
            payload.get("installRoot"),
            payload.get("installerAgent"),
        )
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
        check_entry_safe(project, args.no_fetch, args.include_prereleases)
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
