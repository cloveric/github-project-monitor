const state = {
  projects: [],
  summary: {},
  searchResults: [],
  filter: "all",
  loading: false,
  searching: false,
  actionToken: "",
  updatePlan: { actions: [], blocked: [], summary: {} },
  instanceGroups: [],
  history: { snapshots: [] },
  writeModeUntil: 0,
};

const rows = document.querySelector("#projectRows");
const searchRows = document.querySelector("#searchRows");
const refreshButton = document.querySelector("#refreshButton");
const searchInput = document.querySelector("#searchInput");
const githubSearchForm = document.querySelector("#githubSearchForm");
const githubSearchInput = document.querySelector("#githubSearchInput");
const searchButton = document.querySelector("#searchButton");
const installRootInput = document.querySelector("#installRootInput");
const installerAgentSelect = document.querySelector("#installerAgentSelect");
const reportLink = document.querySelector("#reportLink");
const scanToggle = document.querySelector("#scanToggle");
const fastToggle = document.querySelector("#fastToggle");
const preToggle = document.querySelector("#preToggle");
const postUpdateToggle = document.querySelector("#postUpdateToggle");
const writeToggle = document.querySelector("#writeToggle");
const writeModeStatus = document.querySelector("#writeModeStatus");
const runPlanButton = document.querySelector("#runPlanButton");
const planRows = document.querySelector("#planRows");
const instanceRows = document.querySelector("#instanceRows");
const historyRows = document.querySelector("#historyRows");
const toast = document.querySelector("#toast");

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function createIcons() {
  if (window.lucide) {
    window.lucide.createIcons();
  }
}

function statusClass(value) {
  if (
    value === "sync" ||
    value === "release-current" ||
    value === "package-current" ||
    value === "package-floating-latest"
  ) {
    return "sync";
  }
  if (value === "behind+dirty") return "behind-dirty";
  if (value === "behind" || value === "release-in-history") return "behind";
  if (value === "release-behind" || value === "package-behind") return "release-behind";
  if (value === "dirty") return "dirty";
  if (value === "missing") return "missing";
  return "neutral";
}

function visibilityClass(value) {
  return value === "private" ? "private" : "public";
}

function isReleaseCheckout(project) {
  return project.kind !== "npm" && !project.upstream && project.current_tag;
}

function isLatestReleaseCheckout(project) {
  return isReleaseCheckout(project) && project.latest_release && project.current_tag === project.latest_release;
}

function isWriteMode() {
  return state.writeModeUntil > Date.now();
}

function updateWriteMode() {
  if (!isWriteMode()) {
    state.writeModeUntil = 0;
    writeToggle.checked = false;
    writeModeStatus.textContent = "Read only";
  } else {
    const remaining = Math.ceil((state.writeModeUntil - Date.now()) / 60000);
    writeModeStatus.textContent = `Write ${remaining}m`;
  }
  renderRows();
  renderSearchRows();
  renderPlan();
}

function branchLine(project) {
  if (project.kind === "npm") {
    return `${escapeHtml(project.source || "npm")} <span class="meta-text">${escapeHtml(project.package)}</span>`;
  }
  if (isReleaseCheckout(project)) {
    const releaseLabel = isLatestReleaseCheckout(project)
      ? "latest release installed"
      : "release tag installed";
    return `${escapeHtml(project.current_tag)} <span class="meta-text">${releaseLabel}</span>`;
  }
  const upstream = project.upstream || "no upstream";
  return `${escapeHtml(project.branch)} <span class="meta-text">${escapeHtml(upstream)}</span>`;
}

function commitLine(project) {
  if (project.kind === "npm") {
      return `<strong>${escapeHtml(project.configured_version || "latest")}</strong> configured`;
  }
  if (isReleaseCheckout(project)) {
    if (isLatestReleaseCheckout(project)) {
      return `<strong>Up to date</strong> <span class="meta-text">latest release installed</span>`;
    }
    return `<strong>Release installed</strong> <span class="meta-text">branch comparison skipped</span>`;
  }
  if (project.ahead == null || project.behind == null) {
    return `<strong>No upstream comparison</strong> <span class="meta-text">counts unavailable</span>`;
  }
  const ahead = project.ahead ?? "NA";
  const behind = project.behind ?? "NA";
  return `<strong>${ahead}</strong> ahead <span class="meta-text"><strong>${behind}</strong> behind</span>`;
}

function formatDateTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function releaseLine(project) {
  const current = project.current_tag || "NA";
  const latest = project.latest_release || "NA";
  const published = formatDateTime(project.latest_release_published_at);
  const publishedLine = published
    ? `<span class="meta-text">published ${escapeHtml(published)}</span>`
    : "";
  return `${escapeHtml(current)} <span class="meta-text">latest ${escapeHtml(latest)}</span>${publishedLine}`;
}

function serviceLine(project) {
  if (!project.service_name) {
    return `<span class="meta-text">No service metadata</span>`;
  }
  const status = project.service_running ? "running" : "stopped";
  const ports = Array.isArray(project.service_ports) && project.service_ports.length
    ? `:${project.service_ports.join(", :")}`
    : "no ports";
  const restart = project.service_restart
    ? `<span class="meta-text">${escapeHtml(project.service_restart)}</span>`
    : "";
  return `<span class="pill ${project.service_running ? "sync" : "neutral"}">${escapeHtml(status)}</span><span class="meta-text">${escapeHtml(project.service_name)} · ${escapeHtml(ports)}</span>${restart}`;
}

function localSignal(project) {
  if (project.kind === "npm") {
    return `<span class="pill ${statusClass(project.release_status)}">${escapeHtml(project.release_status)}</span>`;
  }

  const dirtyText = project.dirty
    ? `${project.dirty_files || 0} changed, ${project.untracked_files || 0} untracked`
    : "clean";
  return `
    <span class="pill ${statusClass(project.branch_status)}">${escapeHtml(project.branch_status)}</span>
    <span class="pill ${visibilityClass(project.visibility)}">${escapeHtml(project.visibility || "unknown")}</span>
    <span class="meta-text">${escapeHtml(project.category || "project")} · ${escapeHtml(dirtyText)}</span>
  `;
}

function matchesSearch(project, query) {
  if (!query) return true;
  const text = [
    project.name,
    project.repo,
    project.package,
    project.path,
    project.category,
    project.visibility,
  ]
    .join(" ")
    .toLowerCase();
  return text.includes(query.toLowerCase());
}

function matchesFilter(project) {
  if (state.filter === "all") return true;
  if (state.filter === "behind") return project.branch_status.includes("behind");
  if (state.filter === "release") {
    return ["release-behind", "package-behind"].includes(project.release_status);
  }
  if (state.filter === "dirty") return project.dirty;
  if (state.filter === "clean") {
    return (
      !project.dirty &&
      project.branch_status === "sync" &&
      !["release-behind", "package-behind"].includes(project.release_status)
    );
  }
  return true;
}

function renderSummary() {
  document.querySelector("#metricProjects").textContent = state.summary.projects ?? 0;
  document.querySelector("#metricBranch").textContent = state.summary.branchBehind ?? 0;
  document.querySelector("#metricRelease").textContent = state.summary.releaseBehind ?? 0;
  document.querySelector("#metricDirty").textContent = state.summary.dirty ?? 0;
}

function renderRows() {
  const query = searchInput.value.trim();
  const filtered = state.projects.filter(
    (project) => matchesSearch(project, query) && matchesFilter(project),
  );

  if (state.loading) {
    rows.innerHTML = `<tr><td colspan="7" class="empty-row">Checking local projects</td></tr>`;
    return;
  }

  if (!filtered.length) {
    rows.innerHTML = `<tr><td colspan="7" class="empty-row">No installed projects match this view</td></tr>`;
    return;
  }

  rows.innerHTML = filtered
    .map((project) => {
      const projectUrl =
        project.kind === "npm"
          ? `https://www.npmjs.com/package/${project.package}`
          : project.html_url || `https://github.com/${project.repo}`;
      const targetLabel = project.kind === "npm" ? project.package : project.repo || project.name;
      const actionDisabled = !isWriteMode() || project.kind === "npm" || project.branch === "missing" ? "disabled" : "";
      const postSteps = Array.isArray(project.post_update_steps) && project.post_update_steps.length
        ? `<span class="meta-text">${project.post_update_steps.length} post-update steps</span>`
        : "";
      const diagnosis = project.diagnostic
        ? `<span class="meta-text">${escapeHtml(project.diagnostic)}</span>`
        : "";
      return `
        <tr>
          <td>
            <span class="project-name">${escapeHtml(project.name)}</span>
            <a class="repo-name" href="${escapeHtml(projectUrl)}" target="_blank" rel="noreferrer">${escapeHtml(targetLabel)}</a>
          </td>
          <td>${localSignal(project)}${diagnosis}</td>
          <td>${branchLine(project)}<span class="meta-text">${commitLine(project)}</span></td>
          <td>${releaseLine(project)}<span class="meta-text">${escapeHtml(project.release_status)}</span>${postSteps}</td>
          <td>${serviceLine(project)}</td>
          <td><span class="path-text">${escapeHtml(project.path)}</span></td>
          <td>
            <div class="row-actions">
              <button class="tool-button" type="button" data-local-action="copy" data-path="${escapeHtml(project.path)}" title="Copy path">
                <i data-lucide="copy"></i>
              </button>
              <button class="tool-button" type="button" ${actionDisabled} data-local-action="updateCommit" data-path="${escapeHtml(project.path)}" title="Update to branch commit">
                <i data-lucide="git-pull-request-arrow"></i>
              </button>
              <button class="tool-button" type="button" ${actionDisabled} data-local-action="updateRelease" data-path="${escapeHtml(project.path)}" data-repo="${escapeHtml(project.repo || "")}" title="Update to latest release">
                <i data-lucide="tag"></i>
              </button>
              <button class="tool-button danger-action" type="button" ${actionDisabled} data-local-action="uninstall" data-path="${escapeHtml(project.path)}" title="Move to trash">
                <i data-lucide="trash-2"></i>
              </button>
            </div>
          </td>
        </tr>
      `;
    })
    .join("");
  createIcons();
}

function renderSearchRows() {
  document.querySelector("#searchCount").textContent = `${state.searchResults.length} results`;

  if (state.searching) {
    searchRows.innerHTML = `<tr><td colspan="4" class="empty-row">Searching GitHub</td></tr>`;
    return;
  }

  if (!state.searchResults.length) {
    searchRows.innerHTML = `<tr><td colspan="4" class="empty-row">Search GitHub to install a project</td></tr>`;
    return;
  }

  const installedRepos = new Set(state.projects.map((project) => project.repo).filter(Boolean));
  searchRows.innerHTML = state.searchResults
    .map((repo) => {
      const installed = installedRepos.has(repo.full_name);
      const description = repo.description || "No description";
      return `
        <tr>
          <td>
            <span class="project-name">${escapeHtml(repo.full_name)}</span>
            <span class="repo-description">${escapeHtml(description)}</span>
          </td>
          <td>
            <span class="pill ${visibilityClass(repo.visibility)}">${escapeHtml(repo.visibility || "public")}</span>
            <span class="meta-text">${escapeHtml(repo.stars ?? 0)} stars · ${escapeHtml(repo.forks ?? 0)} forks</span>
          </td>
          <td>
            <span class="branch-name">${escapeHtml(repo.default_branch || "main")}</span>
            <span class="meta-text">${escapeHtml(repo.pushed_at || repo.updated_at || "")}</span>
          </td>
          <td>
            <button class="icon-button compact-action ${installed ? "secondary" : "primary"}" type="button" data-store-action="install" data-repo="${escapeHtml(repo.full_name)}" ${installed || !isWriteMode() ? "disabled" : ""}>
              <i data-lucide="${installed ? "check" : "download"}"></i>
              <span>${installed ? "Installed" : "Install"}</span>
            </button>
          </td>
        </tr>
      `;
    })
    .join("");
  createIcons();
}

function renderPlan() {
  const actions = state.updatePlan.actions || [];
  const blocked = state.updatePlan.blocked || [];
  document.querySelector("#planCount").textContent = `${actions.length} actions`;
  runPlanButton.disabled = !isWriteMode() || !actions.length;

  if (!actions.length && !blocked.length) {
    planRows.innerHTML = `<div class="empty-row compact-empty">No safe updates waiting</div>`;
    createIcons();
    return;
  }

  const actionRows = actions
    .map((item) => `
      <article class="stack-item">
        <div>
          <strong>${escapeHtml(item.label || item.name)}</strong>
          <span>${escapeHtml(item.reason || item.action)}</span>
          ${(item.postUpdateSteps || []).length ? `<span>${item.postUpdateSteps.length} post-update steps</span>` : ""}
        </div>
        <code>${escapeHtml(item.path)}</code>
      </article>
    `)
    .join("");
  const blockedRows = blocked
    .map((item) => `
      <article class="stack-item blocked">
        <div>
          <strong>${escapeHtml(item.name)}</strong>
          <span>${escapeHtml(item.reason)}</span>
        </div>
        <code>${escapeHtml(item.path)}</code>
      </article>
    `)
    .join("");
  planRows.innerHTML = `${actionRows}${blockedRows}`;
  createIcons();
}

function renderInstanceGroups() {
  const groups = state.instanceGroups || [];
  document.querySelector("#instanceCount").textContent = `${groups.length} groups`;
  if (!groups.length) {
    instanceRows.innerHTML = `<div class="empty-row compact-empty">No duplicate installations</div>`;
    return;
  }
  instanceRows.innerHTML = groups
    .map((group) => `
      <article class="stack-item">
        <div>
          <strong>${escapeHtml(group.repo)}</strong>
          <span>${group.instances} instances · ${group.dirty} dirty · ${group.branchBehind} branch behind · ${group.releaseBehind} release behind</span>
          <span>${escapeHtml((group.categories || []).join(", "))}</span>
        </div>
        <code>${escapeHtml((group.paths || []).join(" · "))}</code>
      </article>
    `)
    .join("");
}

function renderHistory() {
  const snapshots = (state.history && state.history.snapshots) || [];
  document.querySelector("#historyCount").textContent = `${snapshots.length} snapshots`;
  if (!snapshots.length) {
    historyRows.innerHTML = `<div class="empty-row compact-empty">No snapshots yet</div>`;
    return;
  }
  historyRows.innerHTML = snapshots
    .slice(-8)
    .reverse()
    .map((snapshot) => {
      const summary = snapshot.summary || {};
      return `
        <article class="history-card">
          <strong>${escapeHtml(formatDateTime(snapshot.createdAt) || snapshot.createdAt || "")}</strong>
          <span>${escapeHtml(summary.projects ?? 0)} projects</span>
          <span>${escapeHtml(summary.dirty ?? 0)} dirty</span>
          <span>${escapeHtml(summary.releaseBehind ?? 0)} release</span>
        </article>
      `;
    })
    .join("");
}

function showToast(message) {
  toast.textContent = message;
  toast.classList.add("visible");
  window.setTimeout(() => toast.classList.remove("visible"), 2200);
}

function buildCheckParams() {
  const params = new URLSearchParams({
    noFetch: fastToggle.checked ? "1" : "0",
    includePrereleases: preToggle.checked ? "1" : "0",
    discover: scanToggle.checked ? "1" : "0",
  });
  const installRoot = installRootInput.value.trim();
  if (installRoot) {
    params.append("extraRoot", installRoot);
  }
  return params;
}

function updateReportLink() {
  reportLink.href = `/api/report.md?${buildCheckParams().toString()}`;
}

async function loadConfig() {
  const response = await fetch("/api/config");
  if (!response.ok) return;
  const config = await response.json();
  state.actionToken = config.actionToken || "";
  installRootInput.value = config.installRoot || "";
  if (Array.isArray(config.installAgents) && config.installAgents.length) {
    installerAgentSelect.innerHTML = config.installAgents
      .map((agent) => `<option value="${escapeHtml(agent.id)}">${escapeHtml(agent.label)}</option>`)
      .join("");
  }
  installerAgentSelect.value = config.defaultInstallerAgent || "codex";
  document.querySelector("#scanRoots").textContent = (config.scanRoots || []).join(" · ");
  updateReportLink();
}

async function refresh() {
  state.loading = true;
  refreshButton.disabled = true;
  renderRows();

  const params = buildCheckParams();
  updateReportLink();

  try {
    const response = await fetch(`/api/check?${params.toString()}`);
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const payload = await response.json();
    state.projects = payload.projects || [];
    state.summary = payload.summary || {};
    state.updatePlan = payload.updatePlan || { actions: [], blocked: [], summary: {} };
    state.instanceGroups = payload.instanceGroups || [];
    state.history = payload.history || { snapshots: [] };
    document.querySelector("#lastChecked").textContent = new Date().toLocaleString();
  } catch (error) {
    showToast(`Check failed: ${error.message}`);
  } finally {
    state.loading = false;
    refreshButton.disabled = false;
    renderSummary();
    renderRows();
    renderSearchRows();
    renderPlan();
    renderInstanceGroups();
    renderHistory();
  }
}

async function searchGithub(event) {
  event.preventDefault();
  const query = githubSearchInput.value.trim();
  if (!query) {
    githubSearchInput.focus();
    return;
  }

  state.searching = true;
  searchButton.disabled = true;
  renderSearchRows();

  try {
    const params = new URLSearchParams({ q: query, limit: "20" });
    const response = await fetch(`/api/search?${params.toString()}`);
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const payload = await response.json();
    state.searchResults = payload.results || [];
  } catch (error) {
    showToast(`Search failed: ${error.message}`);
  } finally {
    state.searching = false;
    searchButton.disabled = false;
    renderSearchRows();
  }
}

async function postAction(payload) {
  if (!state.actionToken) {
    throw new Error("Missing action token; refresh the dashboard");
  }
  const response = await fetch("/api/action", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-GitHub-Watch-Token": state.actionToken,
    },
    body: JSON.stringify(payload),
  });
  const result = await response.json();
  if (!response.ok || !result.ok) {
    throw new Error(result.error || `HTTP ${response.status}`);
  }
  showToast(result.message || "Action complete");
  return result;
}

async function copyText(value) {
  try {
    await navigator.clipboard.writeText(value);
  } catch (_error) {
    const textarea = document.createElement("textarea");
    textarea.value = value;
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand("copy");
    textarea.remove();
  }
  showToast("Path copied");
}

document.querySelectorAll(".segment").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".segment").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    state.filter = button.dataset.filter;
    renderRows();
  });
});

rows.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-local-action]");
  if (!button || button.disabled) return;
  const action = button.dataset.localAction;
  const path = button.dataset.path;

  if (action === "copy") {
    await copyText(path);
    return;
  }

  if (action === "uninstall") {
    const confirmed = window.confirm(`Move this project to local trash?\n\n${path}`);
    if (!confirmed) return;
  }

  try {
    button.disabled = true;
    await postAction({
      action,
      path,
      repo: button.dataset.repo || undefined,
      includePrereleases: preToggle.checked,
      runPostUpdate: postUpdateToggle.checked,
    });
    await refresh();
  } catch (error) {
    showToast(`${action} failed: ${error.message}`);
  } finally {
    button.disabled = false;
  }
});

searchRows.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-store-action]");
  if (!button || button.disabled) return;
  const repo = button.dataset.repo;

  try {
    button.disabled = true;
    await postAction({
      action: "install",
      repo,
      installRoot: installRootInput.value.trim(),
      installerAgent: installerAgentSelect.value,
    });
    await refresh();
  } catch (error) {
    showToast(`Install failed: ${error.message}`);
  } finally {
    button.disabled = false;
  }
});

runPlanButton.addEventListener("click", async () => {
  const actions = state.updatePlan.actions || [];
  if (!actions.length || !isWriteMode()) return;
  const confirmed = window.confirm(`Run ${actions.length} planned update actions?`);
  if (!confirmed) return;

  try {
    runPlanButton.disabled = true;
    await postAction({
      action: "runPlan",
      actions,
      includePrereleases: preToggle.checked,
      runPostUpdate: postUpdateToggle.checked,
    });
    await refresh();
  } catch (error) {
    showToast(`Run plan failed: ${error.message}`);
  } finally {
    runPlanButton.disabled = !isWriteMode() || !actions.length;
  }
});

refreshButton.addEventListener("click", refresh);
githubSearchForm.addEventListener("submit", searchGithub);
searchInput.addEventListener("input", renderRows);
preToggle.addEventListener("change", refresh);
fastToggle.addEventListener("change", refresh);
scanToggle.addEventListener("change", refresh);
installRootInput.addEventListener("input", updateReportLink);
writeToggle.addEventListener("change", () => {
  state.writeModeUntil = writeToggle.checked ? Date.now() + 5 * 60 * 1000 : 0;
  updateWriteMode();
});

window.addEventListener("load", async () => {
  createIcons();
  await loadConfig();
  await refresh();
  window.setInterval(updateWriteMode, 30000);
});
