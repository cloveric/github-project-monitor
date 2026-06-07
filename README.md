# GitHub Project Monitor

**A local-first GitHub Store for discovering, installing, auditing, updating, and safely removing the GitHub projects on your machine.**

[中文说明](README.zh-CN.md)

GitHub Project Monitor turns scattered local checkouts into a product-grade
inventory. It is built for developers and agent-heavy workflows where GitHub
projects are not only apps: they may be Codex skills, Claude skills, plugins,
MCP servers, local workspaces, or release-pinned tools.

## Why It Exists

Modern developer machines accumulate GitHub projects through many paths:

- cloned repositories under `~/projects`
- agent skills under `.codex`, `.claude`, or `.agents`
- marketplace plugins and vendored catalogs
- MCP packages launched through npm or `npx`
- local agent workspaces under `.cctb`
- release-oriented tools that should be updated by tag, not blindly by branch

GitHub Project Monitor gives you one local dashboard to answer:

- What GitHub projects are installed here?
- Which ones are public, private, dirty, ahead, behind, or missing?
- Which tools are behind their latest GitHub Release or npm package version?
- Can I search GitHub, install a repo, update it, or move it to trash safely?

## Highlights

- **GitHub Store Web GUI** - search GitHub repositories, install them locally,
  inspect installed projects, and run safe actions from one dashboard.
- **Agent-assisted installs** - choose Codex by default, Claude Code, or a plain
  Git-only clone when installing a repository.
- **Comprehensive local scan** - discovers Git worktrees across common project,
  skill, plugin, MCP, agent, and workspace roots.
- **Installed project audit** - reports branch, upstream, ahead/behind counts,
  dirty files, untracked files, visibility, category, release status, and local
  path.
- **Two update modes** - update by branch commit with `git pull --ff-only`, or
  update by latest GitHub Release tag.
- **Update plan** - preview safe branch/release updates, see blocked dirty
  projects, then run the confirmed plan from write mode.
- **Post-update steps** - attach project-specific commands such as dependency
  install, build, restart, or smoke tests in your local watchlist.
- **Snapshots and trends** - save scan history, surface recent snapshots, and
  remember when a project was last dirty.
- **Multi-instance view** - group the same GitHub repository across Codex,
  Claude, plugins, skills, and workspaces without hiding duplicate installs.
- **Local service hints** - show configured restart commands and detected
  listening ports when a project has service metadata.
- **Safe uninstall** - moves clean Git worktrees to a local trash directory
  instead of deleting them directly.
- **Watchlist plus discovery** - keep stable machine-specific entries in
  `watchlist.local.json`, while the Web GUI can also discover projects that are
  not on the watchlist yet.
- **MCP/npm awareness** - monitors npm package entries such as `@playwright/mcp`
  and floating `latest` configurations.
- **Markdown and JSON reports** - generate human-readable reports or automation
  output from the same local status engine.

## Web GUI

Start the local GitHub Store:

```bash
python3 github_watch.py web
```

Open:

```text
http://127.0.0.1:8765
```

The dashboard includes:

- **Store Search** - authenticated GitHub search through `gh api`, with a public
  GitHub API fallback, then install with Codex, Claude Code, or Git only.
- **Installed Projects** - filter by all, behind, release, dirty, or clean.
- **Local Scanner** - include local projects installed as apps, skills, plugins,
  MCP servers, and workspaces.
- **Update Plan** - review safe updates and blocked projects before running
  them.
- **Instances and History** - inspect duplicate installs and recent scan
  snapshots.
- **Actions** - install, update by commit, update by release, copy path, and move
  to trash.

## Safety Model

The tool is intentionally local-first and conservative.

- Dirty Git worktrees are not updated or moved to trash by default.
- Branch updates run `git fetch --all --prune --no-tags`, then
  `git pull --ff-only`.
- Release updates resolve the latest GitHub Release, fetch only that tag, then
  check out the tag in detached HEAD mode.
- The Web GUI action endpoint requires a local Host header, JSON requests, and a
  per-server action token before it can install, update, or move projects.
- Agent-assisted installs run the selected local CLI inside the cloned project
  and write logs under:

```text
~/.local/share/github-project-monitor/install-logs/
```
- If cloning succeeds but agent-assisted setup fails, the partial checkout is
  preserved under:

```text
~/.local/share/github-project-monitor/partial/
```
- Uninstall moves the worktree to:

```text
~/.local/share/github-project-monitor/trash/
```

Some tools still need their own install, build, restart, or post-update steps.
This app makes the local state visible before you take those steps.

## Requirements

- Python 3.10+
- `git`
- GitHub CLI: `gh`
- `npm` if you want npm/MCP package checks

Authenticate GitHub CLI before using private repositories or release checks:

```bash
gh auth login
gh auth status
```

## Quick Start

Clone this repository, then create your personal watchlist:

```bash
cp watchlist.json watchlist.local.json
python3 github_watch.py list
```

Edit `watchlist.local.json` with your real local paths. The app automatically
uses `watchlist.local.json` when it exists, otherwise it falls back to
`watchlist.json`.

Run a full watchlist check:

```bash
python3 github_watch.py check
```

Run a faster check without fetching remotes first:

```bash
python3 github_watch.py check --no-fetch
```

Write a Markdown report:

```bash
python3 github_watch.py check --output reports/latest.md
```

Write JSON for automation:

```bash
python3 github_watch.py check --format json --output reports/latest.json
```

## Watchlist Format

GitHub repositories go in `projects`:

```json
{
  "name": "hyperframes",
  "repo": "heygen-com/hyperframes",
  "visibility": "public",
  "path": "/Users/me/projects/hyperframes",
  "postUpdate": [
    {"name": "Install dependencies", "command": "bun install"},
    {"name": "Build", "command": "bun run build"}
  ],
  "service": {
    "name": "HyperFrames",
    "match": "/Users/me/projects/hyperframes",
    "restart": "bun run dev",
    "log": "logs/dev.log"
  }
}
```

npm packages, including npm-launched MCP servers, go in `packages`:

```json
{
  "type": "npm",
  "name": "playwright-mcp",
  "package": "@playwright/mcp",
  "visibility": "public",
  "configuredVersion": "latest",
  "source": "Claude mcpServers.playwright",
  "path": "npm:@playwright/mcp@latest"
}
```

Use `configuredVersion: "latest"` for tools intentionally launched through
`npx ...@latest`. Use an exact version if you want the monitor to report when a
package is behind.

`postUpdate` and `service` are optional local-only fields. Keep machine-specific
commands in `watchlist.local.json`, not in the public sample watchlist.

## Local Discovery Roots

By default, the Web GUI scans common GitHub install locations when full local
scan is enabled:

- `~/projects`
- `~/.cctb`
- `~/.codex/skills`
- `~/.codex/plugins`
- `~/.codex/vendor_imports`
- `~/.agents/skills`
- `~/.claude/skills`
- `~/.claude/plugins`
- `~/.hermes`
- `~/.xhsv2`

Override the scan roots with `GITHUB_PROJECT_SCAN_ROOTS`:

```bash
GITHUB_PROJECT_SCAN_ROOTS="$HOME/projects:$HOME/.codex/skills" python3 github_watch.py web
```

Override the install root with `GITHUB_STORE_INSTALL_ROOT`:

```bash
GITHUB_STORE_INSTALL_ROOT="$HOME/projects" python3 github_watch.py web
```

## CLI Reference

List watched entries:

```bash
python3 github_watch.py list
```

Check everything in the watchlist:

```bash
python3 github_watch.py check
```

Filter by name, repo, package, or path:

```bash
python3 github_watch.py check --only mcp
python3 github_watch.py check --only hyperframes
```

Include prereleases when selecting the latest GitHub Release:

```bash
python3 github_watch.py check --include-prereleases
```

Use a different watchlist:

```bash
python3 github_watch.py --watchlist ~/my-watchlist.json check
```

## Public Repo Hygiene

Keep personal machine state out of public commits:

- Put real machine paths in `watchlist.local.json`.
- Keep generated reports under `reports/`.
- Keep screenshots or Playwright traces under `output/` or `.playwright-cli/`.

Those paths are ignored by `.gitignore`.

## Development

Run tests:

```bash
python3 -m unittest discover -s tests
```

Check Python syntax:

```bash
python3 -m py_compile github_watch.py github_watch_web.py
```

Check frontend JavaScript syntax:

```bash
node --check web/assets/app.js
```

## License

MIT
