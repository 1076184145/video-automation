import { API } from "./api.js";
import { bindQueuePanel, renderQueuePanel } from "./automation.js";
import { eventHub } from "./event-hub.js";
import { errorHintHtml } from "./error-hints.js";
import { t } from "./i18n.js";
import { setButtonLoading, showToast } from "./toast.js";
import { basename, escapeHtml, fileMap, formatDate, jobName, progressForJob, statusGroup, statusLabelKey } from "./utils.js";

let filter = "all";
let search = "";
let lastJobsKey = "";
let searchTimer = null;
let lastQueueKey = "";
export const MAX_RENDERED_JOBS = 100;

export async function renderDashboard(_match, { signal } = {}) {
  const app = document.getElementById("app");
  let eventUnsubscribers = [];
  let jobs = [];
  let projects = [];
  let queue = { paused: false, items: [] };
  lastJobsKey = "";
  app.innerHTML = pageShell();
  const unbindQueue = bindQueuePanel(app, loadQueue);
  bindControls(() => updateJobs(jobs));
  const unbindDelete = bindCompletedJobDelete(async (button, name) => {
    if (!window.confirm(t("dashboard.delete_completed_confirm"))) return;
    setButtonLoading(button, true);
    try {
      await API.deleteJob(name);
      jobs = jobs.filter((job) => jobName(job) !== name);
      lastJobsKey = "";
      updateJobs(jobs);
      showToast(t("dashboard.delete_completed_success"), "success");
    } catch (error) {
      setButtonLoading(button, false);
      showToast(`${t("dashboard.delete_completed_failed")} ${error.message || t("common.error")}`, "error");
    }
  });

  async function load() {
    try {
      const [nextJobs, projectPayload] = await Promise.all([
        API.getJobs({ signal }),
        API.getProjects({ signal }).catch(() => ({ items: projects })),
      ]);
      jobs = nextJobs;
      projects = projectPayload.items || projects;
      updateJobs(jobs, projects);
    } catch (error) {
      const target = document.getElementById("dashboard-jobs");
      target.innerHTML = `<div class="error">${errorHintHtml(error.message || t("common.error"))} <button class="button" id="retry">${t("common.retry")}</button></div>`;
      document.getElementById("retry")?.addEventListener("click", load);
    }
  }

  async function loadQueue() {
    try {
      queue = await API.getQueue({ signal });
      updateQueue(queue);
    } catch (error) {
      const target = document.getElementById("queue-panel");
      if (target && !target.innerHTML.trim()) {
        target.innerHTML = `<div class="error">${escapeHtml(error.message || t("common.error"))}</div>`;
      }
    }
  }

  async function loadHealth() {
    try {
      updateHealth(await getHealthWithTimeout(2500));
    } catch {
      updateHealth(null);
    }
  }

  function startEvents() {
    if (document.visibilityState !== "visible") return;
    if (!eventUnsubscribers.length) {
      eventUnsubscribers = [
        eventHub.subscribe("hello", (payload) => {
        if (Array.isArray(payload.jobs)) {
          jobs = payload.jobs;
          updateJobs(jobs, projects);
        }
        }),
        eventHub.subscribe("job", (job) => {
        if (!job || !job.job_dir) return;
        jobs = mergeJob(jobs, job);
        updateJobs(jobs, projects);
        }),
      ];
    }
  }

  function stopEvents() {
    eventUnsubscribers.forEach((unsubscribe) => unsubscribe());
    eventUnsubscribers = [];
  }

  const handleVisibility = () => {
    if (document.visibilityState === "visible") {
      load();
      loadQueue();
      loadHealth();
      startEvents();
    } else {
      stopEvents();
    }
  };
  document.addEventListener("visibilitychange", handleVisibility);

  await load();
  await loadQueue();
  loadHealth();
  startEvents();
  const queueTimer = setInterval(() => {
    if (document.visibilityState === "visible") loadQueue();
  }, 2000);
  return () => {
    stopEvents();
    unbindDelete();
    unbindQueue();
    clearInterval(queueTimer);
    clearTimeout(searchTimer);
    document.removeEventListener("visibilitychange", handleVisibility);
  };
}

function mergeJob(jobs, nextJob) {
  const index = jobs.findIndex((job) => job.job_dir === nextJob.job_dir);
  if (index < 0) {
    return [nextJob, ...jobs].sort(compareJobs);
  }
  const merged = jobs.slice();
  merged[index] = {
    ...merged[index],
    ...nextJob,
    files: nextJob.files || merged[index].files
  };
  return merged.sort(compareJobs);
}

function compareJobs(a, b) {
  return String(b.updated_at || "").localeCompare(String(a.updated_at || ""));
}

async function getHealthWithTimeout(timeoutMs) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await API.getHealth({ signal: controller.signal });
  } finally {
    clearTimeout(timeout);
  }
}

function pageShell() {
  return `
    <section class="page-head">
      <div>
        <h1 class="page-title">${t("nav.dashboard")}</h1>
        <p class="page-subtitle">${t("app.subtitle")}</p>
      </div>
      <div class="toolbar">
        <input class="search" id="search" type="search" placeholder="${t("dashboard.search")}" value="${escapeHtml(search)}" />
        <a class="button primary" href="#/new">+ ${t("dashboard.new_job") || t("nav.new")}</a>
      </div>
    </section>
    <div class="pill-row">${["all", "processing", "review", "done", "failed"].map((item) => `
      <button class="pill ${filter === item ? "active" : ""}" data-filter="${item}">${t(`status.${item}`)}</button>
    `).join("")}</div>
    <div id="queue-panel"></div>
    <div id="health-banner"></div>
    <div id="dashboard-jobs"><div class="grid"><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div></div></div>
  `;
}

function updateQueue(queue) {
  const target = document.getElementById("queue-panel");
  if (!target) return;
  const key = JSON.stringify(queue);
  if (!shouldUpdateQueueForTest(lastQueueKey, key, Boolean(target.innerHTML.trim()))) return;
  lastQueueKey = key;
  target.innerHTML = renderQueuePanel(queue);
}

export function shouldUpdateQueueForTest(previousKey, nextKey, hasContent) {
  return previousKey !== nextKey || !hasContent;
}

function bindControls(update) {
  document.querySelectorAll("[data-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      filter = button.dataset.filter;
      updateFilterButtons();
      update();
    });
  });
  const input = document.getElementById("search");
  if (input) {
    input.addEventListener("input", () => {
      search = input.value;
      clearTimeout(searchTimer);
      searchTimer = setTimeout(update, 200);
    });
  }
}

function bindCompletedJobDelete(removeJob) {
  const target = document.getElementById("dashboard-jobs");
  if (!target) return () => {};
  const handleClick = (event) => {
    const button = event.target.closest("[data-delete-job]");
    if (!button || !target.contains(button)) return;
    event.preventDefault();
    event.stopPropagation();
    removeJob(button, button.dataset.deleteJob);
  };
  target.addEventListener("click", handleClick);
  return () => target.removeEventListener("click", handleClick);
}

function updateFilterButtons() {
  document.querySelectorAll("[data-filter]").forEach((button) => {
    button.classList.toggle("active", button.dataset.filter === filter);
  });
}

function updateJobs(jobs, projects = []) {
  const target = document.getElementById("dashboard-jobs");
  if (!target) return;
  const key = `${filter}|${search}|${projects.map((project) => `${project.id}|${project.name}`).join("\n")}|${jobs.map((job) => `${job.job_dir}|${job.batch_id || ""}|${job.status}|${job.updated_at}|${job.stage_progress ?? ""}|${job.project_id || ""}`).join("\n")}`;
  if (key === lastJobsKey) return;
  lastJobsKey = key;
  target.innerHTML = renderDashboardJobsForTest(jobs, { filter, search, projects });
}

function updateHealth(health) {
  const target = document.getElementById("health-banner");
  if (!target) return;
  if (!health || health.ok) {
    target.innerHTML = "";
    return;
  }
  const missingChecks = (health.checks || []).filter((check) => !check.exists && !check.optional);
  if (!missingChecks.length) {
    target.innerHTML = "";
    return;
  }
  const missing = missingChecks.map((check) => check.name).join(", ");
  target.innerHTML = `<div class="error">${t("health.missing")}: ${escapeHtml(missing || "unknown")} <a class="button" href="#/health">${t("nav.health")}</a></div>`;
}

export function renderDashboardJobsForTest(jobs, state = {}) {
  const currentFilter = state.filter || "all";
  const currentSearch = state.search || "";
  const visible = jobs.filter((job) => {
    const group = statusGroup(job.status);
    const text = `${job.source_path} ${job.job_dir}`.toLowerCase();
    return (currentFilter === "all" || currentFilter === group) && text.includes(currentSearch.toLowerCase());
  });
  if (!visible.length) {
    if (jobs.length && (currentFilter !== "all" || currentSearch)) {
      return `
        <div class="empty dashboard-no-results">
          <strong>${t("dashboard.no_matches_title")}</strong>
          <p>${t("dashboard.no_matches")}</p>
        </div>`;
    }
    return `
      <div class="empty onboarding-empty">
        <strong>${t("dashboard.empty_title")}</strong>
        <p>${t("dashboard.no_jobs")}</p>
        <div class="onboarding-steps" aria-label="${t("dashboard.empty_title")}">
          <a href="#/health"><span>1</span>${t("dashboard.empty_step_health")}</a>
          <a href="#/new"><span>2</span>${t("dashboard.empty_step_new")}</a>
          <span><span>3</span>${t("dashboard.empty_step_review")}</span>
        </div>
        <a class="button primary" href="#/new">+ ${t("dashboard.new_job")}</a>
      </div>`;
  }
  const projectNames = new Map((state.projects || []).map((project) => [project.id, project.name]));
  if (currentFilter !== "all" || currentSearch) {
    return `<div class="grid">${visible.map((job) => renderJobCard(job, projectNames)).join("")}</div>`;
  }

  const batchGroups = groupedBatches(visible);
  const activeBatchIds = new Set([...batchGroups.entries()]
    .filter(([, batchJobs]) => batchJobs.some((job) => statusGroup(job.status) !== "done"))
    .map(([batchId]) => batchId));
  const actionable = visible
    .filter((job) => ["review", "failed"].includes(statusGroup(job.status)))
    .sort(compareActionableJobs);
  const processing = visible
    .filter((job) => statusGroup(job.status) === "processing")
    .sort(compareJobs);
  const completed = visible
    .filter((job) => statusGroup(job.status) === "done" && !activeBatchIds.has(job.batch_id))
    .sort(compareJobs);
  const actionableCount = visible.filter((job) => statusGroup(job.status) !== "done").length;
  const actionableContent = actionableCount
    ? renderJobCollection(actionable, projectNames)
    : `
      <div class="dashboard-clear-state">
        <div>
          <strong>${t("dashboard.clear_title")}</strong>
          <p>${t("dashboard.clear_note")}</p>
        </div>
        <a class="button primary" href="#/new">+ ${t("dashboard.new_job")}</a>
      </div>`;
  const history = completed.length
    ? `
      <details class="dashboard-history">
        <summary>
          <span>
            <strong>${t("dashboard.history")}</strong>
            <small>${t("dashboard.history_note")}</small>
          </span>
          <span class="dashboard-history-count">${completed.length}</span>
        </summary>
        ${renderJobCollection(completed, projectNames)}
      </details>`
    : "";
  const processingSection = processing.length
    ? `
      <section class="dashboard-processing">
        <div class="section-heading">
          <div>
            <h2>${t("dashboard.processing_title")}</h2>
            <p>${t("dashboard.processing_note")}</p>
          </div>
          <span class="badge optional">${processing.length}</span>
        </div>
        ${renderJobCollection(processing, projectNames)}
      </section>`
    : "";
  return `
    <section class="dashboard-actionable">
      <div class="section-heading">
        <div>
          <h2>${t("dashboard.actionable")}</h2>
          <p>${t("dashboard.actionable_note")}</p>
        </div>
        <span class="badge optional">${actionableCount}</span>
      </div>
      ${actionableContent}
    </section>
    ${processingSection}
    ${history}`;
}

function groupedBatches(jobs) {
  const batches = new Map();
  jobs.forEach((job) => {
    const batchId = String(job.batch_id || "").trim();
    if (!batchId) return;
    const batchJobs = batches.get(batchId) || [];
    batchJobs.push(job);
    batches.set(batchId, batchJobs);
  });
  for (const [batchId, batchJobs] of batches) {
    if (batchJobs.length < 2) batches.delete(batchId);
  }
  return batches;
}

function renderJobCollection(jobs, projectNames = new Map()) {
  const visibleJobs = jobs.slice(0, MAX_RENDERED_JOBS);
  const batches = groupedBatches(visibleJobs);
  const renderedBatches = new Set();
  const entries = [];
  visibleJobs.forEach((job) => {
    const batchId = String(job.batch_id || "").trim();
    const batchJobs = batches.get(batchId);
    if (!batchJobs) {
      entries.push(renderJobCard(job, projectNames));
      return;
    }
    if (renderedBatches.has(batchId)) return;
    renderedBatches.add(batchId);
    entries.push(renderBatch(batchId, batchJobs, projectNames));
  });
  const remainder = jobs.length - visibleJobs.length;
  return `<div class="dashboard-job-groups">${entries.join("")}</div>${remainder > 0 ? `<p class="muted dashboard-window-note">${t("dashboard.window_note").replace("{count}", String(remainder))}</p>` : ""}`;
}

function renderBatch(batchId, jobs, projectNames = new Map()) {
  const ordered = jobs.slice().sort((a, b) => {
    const indexA = Number(a.batch_index) || Number.MAX_SAFE_INTEGER;
    const indexB = Number(b.batch_index) || Number.MAX_SAFE_INTEGER;
    return indexA - indexB || compareJobs(a, b);
  });
  const progress = Math.round(ordered.reduce((total, job) => total + progressForJob(job), 0) / ordered.length);
  const counts = new Map();
  ordered.forEach((job) => {
    const group = statusGroup(job.status);
    counts.set(group, (counts.get(group) || 0) + 1);
  });
  const statusSummary = ["review", "failed", "processing", "done"]
    .filter((group) => counts.has(group))
    .map((group) => `${t(`status.${group}`)} ${counts.get(group)}`)
    .join(" · ");
  return `
    <details class="dashboard-batch" data-batch-id="${escapeHtml(batchId)}">
      <summary>
        <span class="dashboard-batch-heading">
          <strong>${t("dashboard.batch_title")}</strong>
          <small>${ordered.length} ${t("dashboard.batch_items")} · ${statusSummary}</small>
        </span>
        <span class="dashboard-batch-progress">
          <span class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${progress}" aria-label="${t("dashboard.progress")}"><span style="width:${progress}%"></span></span>
          <strong>${progress}%</strong>
        </span>
      </summary>
      <div class="grid">${ordered.map((job) => renderJobCard(job, projectNames)).join("")}</div>
    </details>`;
}

function renderJobCard(job, projectNames = new Map()) {
  const group = statusGroup(job.status);
  const name = jobName(job);
  const sourceName = basename(job.source_path);
  const files = fileMap(job);
  const thumb = files.has("thumbnail.jpg") ? API.jobFileUrl(name, "thumbnail.jpg") : "";
  const progress = progressForJob(job);
  const projectName = projectNames.get(job.project_id) || t("projects.unassigned");
  const actionKey = group === "review"
    ? "dashboard.action_review"
    : group === "failed"
      ? "dashboard.action_fix"
      : group === "done"
        ? "dashboard.action_view"
        : "dashboard.action_progress";
  const deleteControl = group === "done"
    ? `
      <button
        class="job-card-delete"
        type="button"
        data-delete-job="${escapeHtml(name)}"
        aria-label="${t("dashboard.delete_completed")}"
        title="${t("dashboard.delete_completed")}"
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M3 6h18M8 6V4h8v2m-9 0 1 14h8l1-14M10 10v6m4-6v6"></path>
        </svg>
      </button>`
    : "";
  return `
    <article class="card job-card ${group === "done" ? "has-delete" : ""}">
      <a class="job-card-link" href="#/jobs/${encodeURIComponent(name)}">
        <div>
          <h2 class="job-title">${escapeHtml(sourceName || name)} <span class="badge ${group}">${t(statusLabelKey(job.status))}</span></h2>
          <div class="meta">
            <div class="job-project">${escapeHtml(projectName)}</div>
            <div>${t("common.created")}: ${escapeHtml(formatDate(job.created_at))}</div>
          </div>
          ${group === "processing" ? `
            <div class="job-progress-row">
              <div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${progress}" aria-label="${t("dashboard.progress")}"><span style="width:${progress}%"></span></div>
              <strong>${progress}%</strong>
            </div>` : ""}
          <span class="job-next-action">${t(actionKey)} <span aria-hidden="true">→</span></span>
        </div>
        <div class="thumb">${thumb ? `<img src="${thumb}" alt="" loading="lazy" />` : ""}</div>
      </a>
      ${deleteControl}
    </article>
  `;
}

function compareActionableJobs(a, b) {
  const priority = { review: 0, failed: 1, processing: 2 };
  const groupA = statusGroup(a.status);
  const groupB = statusGroup(b.status);
  const priorityDiff = (priority[groupA] ?? 3) - (priority[groupB] ?? 3);
  return priorityDiff || compareJobs(a, b);
}
