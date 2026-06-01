const state = {
  projects: [],
  summary: {},
  searchResults: [],
  filter: "all",
  loading: false,
  searching: false,
};

const rows = document.querySelector("#projectRows");
const searchRows = document.querySelector("#searchRows");
const refreshButton = document.querySelector("#refreshButton");
const searchInput = document.querySelector("#searchInput");
const githubSearchForm = document.querySelector("#githubSearchForm");
const githubSearchInput = document.querySelector("#githubSearchInput");
const searchButton = document.querySelector("#searchButton");
const installRootInput = document.querySelector("#installRootInput");
const scanToggle = document.querySelector("#scanToggle");
const fastToggle = document.querySelector("#fastToggle");
const preToggle = document.querySelector("#preToggle");
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
    rows.innerHTML = `<tr><td colspan="6" class="empty-row">Checking local projects</td></tr>`;
    return;
  }

  if (!filtered.length) {
    rows.innerHTML = `<tr><td colspan="6" class="empty-row">No installed projects match this view</td></tr>`;
    return;
  }

  rows.innerHTML = filtered
    .map((project) => {
      const projectUrl =
        project.kind === "npm"
          ? `https://www.npmjs.com/package/${project.package}`
          : project.html_url || `https://github.com/${project.repo}`;
      const targetLabel = project.kind === "npm" ? project.package : project.repo || project.name;
      const disabled = project.kind === "npm" || project.branch === "missing" ? "disabled" : "";
      return `
        <tr>
          <td>
            <span class="project-name">${escapeHtml(project.name)}</span>
            <a class="repo-name" href="${escapeHtml(projectUrl)}" target="_blank" rel="noreferrer">${escapeHtml(targetLabel)}</a>
          </td>
          <td>${localSignal(project)}</td>
          <td>${branchLine(project)}<span class="meta-text">${commitLine(project)}</span></td>
          <td>${releaseLine(project)}<span class="meta-text">${escapeHtml(project.release_status)}</span></td>
          <td><span class="path-text">${escapeHtml(project.path)}</span></td>
          <td>
            <div class="row-actions">
              <button class="tool-button" type="button" data-local-action="copy" data-path="${escapeHtml(project.path)}" title="Copy path">
                <i data-lucide="copy"></i>
              </button>
              <button class="tool-button" type="button" ${disabled} data-local-action="updateCommit" data-path="${escapeHtml(project.path)}" title="Update to branch commit">
                <i data-lucide="git-pull-request-arrow"></i>
              </button>
              <button class="tool-button" type="button" ${disabled} data-local-action="updateRelease" data-path="${escapeHtml(project.path)}" data-repo="${escapeHtml(project.repo || "")}" title="Update to latest release">
                <i data-lucide="tag"></i>
              </button>
              <button class="tool-button danger-action" type="button" ${disabled} data-local-action="uninstall" data-path="${escapeHtml(project.path)}" title="Move to trash">
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
            <button class="icon-button compact-action ${installed ? "secondary" : "primary"}" type="button" data-store-action="install" data-repo="${escapeHtml(repo.full_name)}" ${installed ? "disabled" : ""}>
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

function showToast(message) {
  toast.textContent = message;
  toast.classList.add("visible");
  window.setTimeout(() => toast.classList.remove("visible"), 2200);
}

async function loadConfig() {
  const response = await fetch("/api/config");
  if (!response.ok) return;
  const config = await response.json();
  installRootInput.value = config.installRoot || "";
  document.querySelector("#scanRoots").textContent = (config.scanRoots || []).join(" · ");
}

async function refresh() {
  state.loading = true;
  refreshButton.disabled = true;
  renderRows();

  const params = new URLSearchParams({
    noFetch: fastToggle.checked ? "1" : "0",
    includePrereleases: preToggle.checked ? "1" : "0",
    discover: scanToggle.checked ? "1" : "0",
  });
  const installRoot = installRootInput.value.trim();
  if (installRoot) {
    params.append("extraRoot", installRoot);
  }

  try {
    const response = await fetch(`/api/check?${params.toString()}`);
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const payload = await response.json();
    state.projects = payload.projects || [];
    state.summary = payload.summary || {};
    document.querySelector("#lastChecked").textContent = new Date().toLocaleString();
  } catch (error) {
    showToast(`Check failed: ${error.message}`);
  } finally {
    state.loading = false;
    refreshButton.disabled = false;
    renderSummary();
    renderRows();
    renderSearchRows();
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
  const response = await fetch("/api/action", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
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
    });
    await refresh();
  } catch (error) {
    showToast(`Install failed: ${error.message}`);
  } finally {
    button.disabled = false;
  }
});

refreshButton.addEventListener("click", refresh);
githubSearchForm.addEventListener("submit", searchGithub);
searchInput.addEventListener("input", renderRows);
preToggle.addEventListener("change", refresh);
fastToggle.addEventListener("change", refresh);
scanToggle.addEventListener("change", refresh);

window.addEventListener("load", async () => {
  createIcons();
  await loadConfig();
  await refresh();
});
