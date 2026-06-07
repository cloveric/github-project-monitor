# GitHub Project Monitor

**一个本地优先的 GitHub Store：用于发现、安装、审计、更新，并安全移除你电脑上的 GitHub 项目。**

[English](README.md)

GitHub Project Monitor 会把分散在本机各处的 GitHub checkout 整理成一个产品级清单。它特别适合开发者和 agent-heavy 工作流，因为 GitHub 项目不一定只是应用程序，也可能是 Codex skill、Claude skill、插件、MCP server、本地 workspace，或者需要按 release tag 更新的工具。

## 为什么需要它

现代开发机上的 GitHub 项目来源很多：

- `~/projects` 里的手动 clone
- `.codex`、`.claude`、`.agents` 里的 agent skills
- marketplace plugins 和 vendored catalogs
- 通过 npm 或 `npx` 启动的 MCP packages
- `.cctb` 里的本地 agent workspaces
- 需要按 tag 更新，而不是直接拉分支的 release-oriented tools

GitHub Project Monitor 用一个本地 dashboard 回答这些问题：

- 这台机器上装了哪些 GitHub 项目？
- 哪些是 public/private、dirty、ahead、behind 或 missing？
- 哪些工具落后于最新 GitHub Release 或 npm package version？
- 我能不能搜索 GitHub、安装 repo、更新它，或者安全地移入 trash？

## 亮点

- **GitHub Store Web GUI** - 在一个 dashboard 里搜索 GitHub repo、安装到本地、查看已安装项目，并执行安全操作。
- **Agent 辅助安装** - 安装 repo 时默认用 Codex，也可以选择 Claude Code 或纯 Git clone。
- **全面本地扫描** - 扫描常见的 project、skill、plugin、MCP、agent、workspace 根目录。
- **已安装项目审计** - 展示 branch、upstream、ahead/behind、dirty files、untracked files、visibility、category、release status 和本地路径。
- **两种更新方式** - 可以按 branch commit 执行 `git pull --ff-only`，也可以按最新 GitHub Release tag 更新。
- **更新计划** - 先预览安全的 branch/release 更新和被 dirty 状态阻塞的项目，再在写入模式里确认执行。
- **更新后步骤** - 在本地 watchlist 里为项目配置依赖安装、构建、重启或 smoke test 命令。
- **快照与趋势** - 保存扫描历史，展示最近 snapshot，并记住项目上次变脏时间。
- **多实例视图** - 同一个 GitHub repo 在 Codex、Claude、插件、skill、workspace 多处安装时聚合展示，但不隐藏重复实例。
- **本机服务提示** - 有 service 配置时显示 restart 命令，并尽量展示匹配到的监听端口。
- **安全卸载** - 不直接删除，而是把干净的 Git worktree 移入本地 trash 目录。
- **watchlist + discovery** - `watchlist.local.json` 记录稳定的个人机器路径，Web GUI 同时能发现还没写进 watchlist 的项目。
- **MCP/npm 感知** - 支持监控 `@playwright/mcp` 这类 npm package，以及 floating `latest` 配置。
- **Markdown 和 JSON 报告** - 同一套本地状态引擎既能生成人类可读报告，也能输出自动化 JSON。

## Web GUI

启动本地 GitHub Store：

```bash
python3 github_watch.py web
```

打开：

```text
http://127.0.0.1:8765
```

Dashboard 包含：

- **Store Search** - 优先通过已登录的 `gh api` 搜索 GitHub，失败时回退到 public GitHub API，然后可用 Codex、Claude Code 或纯 Git 安装。
- **Installed Projects** - 按 all、behind、release、dirty、clean 过滤本地项目。
- **Local Scanner** - 扫描作为 app、skill、plugin、MCP server、workspace 安装的项目。
- **Update Plan** - 执行前先审查可安全更新的项目和被阻塞的项目。
- **Instances and History** - 查看重复安装和最近扫描快照。
- **Actions** - 安装、按 commit 更新、按 release 更新、复制路径、移入 trash。

## 安全模型

这个工具是本地优先且保守的。

- 默认不会更新或移除 dirty Git worktree。
- 分支更新执行 `git fetch --all --prune --no-tags`，然后执行 `git pull --ff-only`。
- Release 更新会读取最新 GitHub Release，只 fetch 目标 tag，然后以 detached HEAD 方式 checkout 到该 tag。
- Web GUI 的动作接口要求本地 Host、JSON request，以及当前 server 生成的 action token，才允许安装、更新或移入 trash。
- 卸载会把 worktree 移入：

```text
~/.local/share/github-project-monitor/trash/
```

Agent 辅助安装会在 clone 后调用选中的本地 CLI，并把日志写入：

```text
~/.local/share/github-project-monitor/install-logs/
```

如果 clone 成功但 agent 辅助安装失败，半安装目录会保留到：

```text
~/.local/share/github-project-monitor/partial/
```

有些工具更新后仍然需要自己的 install、build、restart 或 post-update 步骤。这个 app 的目标是先让你看清本机真实状态，再决定下一步。

## 依赖

- Python 3.10+
- `git`
- GitHub CLI：`gh`
- 如果要检查 npm/MCP packages，需要 `npm`

使用 private repo 或 release check 前，请先登录 GitHub CLI：

```bash
gh auth login
gh auth status
```

## 快速开始

clone 本仓库后，创建你的个人 watchlist：

```bash
cp watchlist.json watchlist.local.json
python3 github_watch.py list
```

编辑 `watchlist.local.json`，填入真实本地路径。应用会优先使用 `watchlist.local.json`；如果它不存在，则回退到 `watchlist.json`。

检查 watchlist：

```bash
python3 github_watch.py check
```

不 fetch remote 的快速检查：

```bash
python3 github_watch.py check --no-fetch
```

写入 Markdown 报告：

```bash
python3 github_watch.py check --output reports/latest.md
```

写入 JSON：

```bash
python3 github_watch.py check --format json --output reports/latest.json
```

## Watchlist 格式

GitHub repositories 放在 `projects`：

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

npm packages，包括通过 npm 启动的 MCP servers，放在 `packages`：

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

如果某个工具故意通过 `npx ...@latest` 启动，可以使用 `configuredVersion: "latest"`。如果你希望监控 pinned version 是否落后，请写入精确版本号。

`postUpdate` 和 `service` 都是可选的本机字段。机器相关命令建议放在 `watchlist.local.json`，不要放进公开示例 watchlist。

## 本地扫描根目录

开启 full local scan 时，Web GUI 默认扫描这些常见 GitHub 安装位置：

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

用 `GITHUB_PROJECT_SCAN_ROOTS` 覆盖扫描根目录：

```bash
GITHUB_PROJECT_SCAN_ROOTS="$HOME/projects:$HOME/.codex/skills" python3 github_watch.py web
```

用 `GITHUB_STORE_INSTALL_ROOT` 覆盖默认安装目录：

```bash
GITHUB_STORE_INSTALL_ROOT="$HOME/projects" python3 github_watch.py web
```

## CLI 参考

列出 watched entries：

```bash
python3 github_watch.py list
```

检查 watchlist 里的所有项目：

```bash
python3 github_watch.py check
```

按 name、repo、package 或 path 过滤：

```bash
python3 github_watch.py check --only mcp
python3 github_watch.py check --only hyperframes
```

选择最新 GitHub Release 时包含 prereleases：

```bash
python3 github_watch.py check --include-prereleases
```

使用另一个 watchlist：

```bash
python3 github_watch.py --watchlist ~/my-watchlist.json check
```

## Public Repo Hygiene

不要把个人机器状态提交到公开仓库：

- 真实机器路径放入 `watchlist.local.json`。
- 生成的报告放在 `reports/`。
- 截图或 Playwright traces 放在 `output/` 或 `.playwright-cli/`。

这些路径已经被 `.gitignore` 忽略。

## 开发

运行测试：

```bash
python3 -m unittest discover -s tests
```

检查 Python 语法：

```bash
python3 -m py_compile github_watch.py github_watch_web.py
```

检查前端 JavaScript 语法：

```bash
node --check web/assets/app.js
```

## License

MIT
