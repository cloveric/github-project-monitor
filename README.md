# GitHub Project Monitor

Track local GitHub checkouts and release-oriented tools from a fixed watchlist.

GitHub Project Monitor is a small Python CLI and local web dashboard for people
who keep many open source projects, agent skills, plugins, and MCP servers
installed locally. It answers the practical questions that are easy to lose track
of:

- Which local clones are behind their upstream branch?
- Which projects are behind the latest GitHub Release?
- Which repositories have uncommitted local changes?
- Which npm-based MCP packages are pinned or floating on `latest`?

It uses a watchlist instead of rescanning your whole machine every time.

## Features

- Checks branch `ahead` / `behind` counts against each repo's upstream.
- Detects dirty worktrees so local edits are not overwritten by surprise.
- Reads the latest stable GitHub Release with `gh api`.
- Supports npm package entries for MCP servers such as `@playwright/mcp`.
- Produces Markdown or JSON reports.
- Includes a local Web GUI with filters for behind, release/package, dirty, and clean items.
- Keeps personal machine paths in `watchlist.local.json`, which is ignored by Git.

## Requirements

- Python 3.10+
- `git`
- GitHub CLI: `gh`
- `npm` if you want npm/MCP package checks

Authenticate GitHub CLI before running release checks:

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

Run a full check:

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

Start the Web GUI:

```bash
python3 github_watch.py web
```

Then open:

```text
http://127.0.0.1:8765
```

## Watchlist Format

GitHub repositories go in `projects`:

```json
{
  "name": "hyperframes",
  "repo": "heygen-com/hyperframes",
  "visibility": "public",
  "path": "/Users/me/projects/hyperframes"
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

## CLI Reference

List watched entries:

```bash
python3 github_watch.py list
```

Check everything:

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

## Update Policy

This tool monitors and reports. It does not automatically update projects.

That is intentional:

- Dirty repositories may contain work you do not want to overwrite.
- Branch updates and release updates are not always the same thing.
- Some tools need extra install, build, or restart steps after pulling.
- Release-oriented tools may be safer to update by tag instead of by branch head.

The Markdown report includes safe `git pull --ff-only` suggestions only for clean
branch-behind repositories.

## Public Repo Hygiene

Keep personal data out of public commits:

- Put your real machine paths in `watchlist.local.json`.
- Keep reports under `reports/`.
- Keep screenshots or Playwright traces under `output/` or `.playwright-cli/`.

Those paths are ignored by `.gitignore`.
