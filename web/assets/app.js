const state = {
  projects: [],
  summary: {},
  filter: "all",
  loading: false,
};

const rows = document.querySelector("#projectRows");
const refreshButton = document.querySelector("#refreshButton");
const searchInput = document.querySelector("#searchInput");
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
  return "neutral";
}

function branchLine(project) {
  if (project.kind === "npm") {
    return `${escapeHtml(project.source || "npm")} <span class="meta-text">${escapeHtml(project.package)}</span>`;
  }
  const upstream = project.upstream || "no upstream";
  return `${escapeHtml(project.branch)} <span class="meta-text">${escapeHtml(upstream)}</span>`;
}

function commitLine(project) {
  if (project.kind === "npm") {
    return `<strong>${escapeHtml(project.configured_version || "latest")}</strong> configured <span class="meta-text">npm package</span>`;
  }
  const ahead = project.ahead ?? "NA";
  const behind = project.behind ?? "NA";
  return `<strong>${ahead}</strong> ahead <span class="meta-text"><strong>${behind}</strong> behind</span>`;
}

function releaseLine(project) {
  const current = project.current_tag || "NA";
  const latest = project.latest_release || "NA";
  return `${escapeHtml(current)} <span class="meta-text">latest ${escapeHtml(latest)}</span>`;
}

function matchesSearch(project, query) {
  if (!query) return true;
  const text = `${project.name} ${project.repo} ${project.package || ""} ${project.path}`.toLowerCase();
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
      project.release_status !== "release-behind"
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
    rows.innerHTML = `<tr><td colspan="7" class="empty-row">Checking projects</td></tr>`;
    return;
  }

  if (!filtered.length) {
    rows.innerHTML = `<tr><td colspan="7" class="empty-row">No projects</td></tr>`;
    return;
  }

  rows.innerHTML = filtered
    .map((project) => {
      const projectUrl =
        project.kind === "npm"
          ? `https://www.npmjs.com/package/${project.package}`
          : `https://github.com/${project.repo}`;
      const targetLabel = project.kind === "npm" ? project.package : project.repo;
      return `
        <tr>
          <td>
            <span class="project-name">${escapeHtml(project.name)}</span>
            <a class="repo-name" href="${escapeHtml(projectUrl)}" target="_blank" rel="noreferrer">${escapeHtml(targetLabel)}</a>
          </td>
          <td>${branchLine(project)}</td>
          <td>${commitLine(project)}</td>
          <td>${releaseLine(project)}</td>
          <td>
            <span class="pill ${statusClass(project.branch_status)}">${escapeHtml(project.branch_status)}</span>
            <span class="meta-text"></span>
            <span class="pill ${statusClass(project.release_status)}">${escapeHtml(project.release_status)}</span>
          </td>
          <td><span class="path-text">${escapeHtml(project.path)}</span></td>
          <td>
            <button class="copy-button" type="button" data-path="${escapeHtml(project.path)}" title="Copy path">
              <i data-lucide="copy"></i>
            </button>
          </td>
        </tr>
      `;
    })
    .join("");
  createIcons();
}

function createIcons() {
  if (window.lucide) {
    window.lucide.createIcons();
  }
}

function showToast(message) {
  toast.textContent = message;
  toast.classList.add("visible");
  window.setTimeout(() => toast.classList.remove("visible"), 1800);
}

async function refresh() {
  state.loading = true;
  refreshButton.disabled = true;
  renderRows();

  const params = new URLSearchParams({
    noFetch: fastToggle.checked ? "1" : "0",
    includePrereleases: preToggle.checked ? "1" : "0",
  });

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
  }
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
  const button = event.target.closest("[data-path]");
  if (!button) return;
  await navigator.clipboard.writeText(button.dataset.path);
  showToast("Path copied");
});

refreshButton.addEventListener("click", refresh);
searchInput.addEventListener("input", renderRows);
preToggle.addEventListener("change", refresh);
fastToggle.addEventListener("change", refresh);

window.addEventListener("load", () => {
  createIcons();
  refresh();
});
