import { API, isAbortError } from "./api.js";
import { bindQueuePanel, renderQueuePanel } from "./automation.js";
import { confirmAction } from "./confirm-dialog.js";
import { eventHub } from "./event-hub.js";
import { errorHintHtml } from "./error-hints.js";
import { t } from "./i18n.js";
import { compareJobs, groupedBatches, MAX_RENDERED_JOBS, renderJobCard, renderJobCollection } from "./job-card.js";
import { showToast } from "./toast.js";
import { emptyState, errorState, skeletonGrid } from "./ui-states.js";
import { escapeHtml, jobName, statusGroup } from "./utils.js";

// Dashboard controller: owns job/queue/health state, polling, SSE updates,
// and section composition. Card markup lives in job-card.js; shared loading /
// empty / error states live in ui-states.js.

export { MAX_RENDERED_JOBS };

let filter = "all";
let search = "";
let lastJobsKey = "";
let lastQueueKey = "";

export async function renderDashboard(_match, { signal } = {}) {
  const app = document.getElementById("app");
  let eventUnsubscribers = [];
  let jobs = [];
  let projects = [];
  let queue = { paused: false, items: [] };
  let disposed = false;
  let jobsLoadVersion = 0;
  let queueLoadVersion = 0;
  let healthLoadVersion = 0;
  let jobsMutationVersion = 0;
  let queueTimer = null;
  let jobsTimer = null;
  const deletedJobNames = new Set();
  const isActive = () => !disposed && !signal?.aborted;
  lastJobsKey = "";
  app.innerHTML = pageShell();
  const unbindQueue = bindQueuePanel(app, loadQueue);
  const unbindControls = bindControls(() => updateJobs(jobs, projects));
  const unbindJobList = bindJobListActions(load);
  const unbindDelete = bindJobDelete(async (button, name) => {
    const confirmed = await confirmAction(t("dashboard.delete_job_confirm"), {
      title: t("dashboard.delete_job"),
      confirmLabel: t("common.delete"),
      cancelLabel: t("common.cancel"),
    });
    if (!confirmed || !isActive()) return;
    const removedJob = jobs.find((job) => jobName(job) === name) || null;
    deletedJobNames.add(name);
    jobs = withoutDeletedJobsForTest(jobs, deletedJobNames);
    jobsMutationVersion += 1;
    lastJobsKey = "";
    updateJobs(jobs, projects);
    try {
      await API.deleteJob(name);
      if (isActive()) showToast(t("dashboard.delete_job_success"), "success");
    } catch (error) {
      if (!isActive()) return;
      deletedJobNames.delete(name);
      if (removedJob) jobs = mergeJob(jobs, removedJob);
      jobsMutationVersion += 1;
      lastJobsKey = "";
      updateJobs(jobs, projects);
      showToast(`${t("dashboard.delete_completed_failed")} ${deleteJobErrorMessageForTest(error)}`, "error");
      load();
    }
  });

  async function load() {
    const version = ++jobsLoadVersion;
    const mutationVersion = jobsMutationVersion;
    try {
      const [nextJobs, projectPayload] = await Promise.all([
        API.getJobs({ signal }),
        API.getProjects({ signal }).catch((error) => {
          if (isAbortError(error, signal)) throw error;
          return { items: projects };
        }),
      ]);
      if (!isActive() || version !== jobsLoadVersion) return;
      const preserveMissing = mutationVersion !== jobsMutationVersion;
      jobs = withoutDeletedJobsForTest(
        mergeJobSnapshotsForTest(jobs, Array.isArray(nextJobs) ? nextJobs : [], { preserveMissing }),
        deletedJobNames,
      );
      projects = projectPayload.items || projects;
      updateJobs(jobs, projects);
    } catch (error) {
      if (!isActive() || version !== jobsLoadVersion || isAbortError(error, signal)) return;
      const target = document.getElementById("dashboard-jobs");
      if (target) target.innerHTML = errorState(errorHintHtml(error.message || t("common.error")), { retryLabel: t("common.retry"), trustedHtml: true });
    }
  }

  async function loadQueue() {
    const version = ++queueLoadVersion;
    try {
      const nextQueue = await API.getQueue({ signal });
      if (!isActive() || version !== queueLoadVersion) return;
      queue = nextQueue;
      updateQueue(queue);
    } catch (error) {
      if (!isActive() || version !== queueLoadVersion || isAbortError(error, signal)) return;
      const target = document.getElementById("queue-panel");
      if (target && !target.innerHTML.trim()) {
        target.innerHTML = `<div class="error">${escapeHtml(error.message || t("common.error"))}</div>`;
      }
    }
  }

  async function loadHealth() {
    const version = ++healthLoadVersion;
    try {
      const health = await API.getHealth({ signal, timeout: 2500, retries: 0 });
      if (!isActive() || version !== healthLoadVersion) return;
      updateHealth(health);
    } catch (error) {
      if (!isActive() || version !== healthLoadVersion || isAbortError(error, signal)) return;
      updateHealth(null);
    }
  }

  function startEvents() {
    if (!isActive() || document.visibilityState !== "visible") return;
    if (!eventUnsubscribers.length) {
      eventUnsubscribers = [
        eventHub.subscribe("hello", (payload) => {
          if (!isActive()) return;
          if (Array.isArray(payload.jobs)) {
            jobsMutationVersion += 1;
            jobs = withoutDeletedJobsForTest(mergeJobSnapshotsForTest(jobs, payload.jobs), deletedJobNames);
            updateJobs(jobs, projects);
          }
        }),
        eventHub.subscribe("job", (job) => {
          if (!isActive() || !job || !job.job_dir) return;
          if (deletedJobNames.has(jobName(job))) return;
          jobsMutationVersion += 1;
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
  if (!isActive()) return cleanupDashboard;
  await loadQueue();
  if (!isActive()) return cleanupDashboard;
  loadHealth();
  startEvents();
  queueTimer = setInterval(() => {
    if (document.visibilityState === "visible") loadQueue();
  }, 2000);
  jobsTimer = setInterval(() => {
    if (document.visibilityState === "visible") load();
  }, 15000);
  return cleanupDashboard;

  function cleanupDashboard() {
    if (disposed) return;
    disposed = true;
    jobsLoadVersion += 1;
    queueLoadVersion += 1;
    healthLoadVersion += 1;
    stopEvents();
    unbindDelete();
    unbindQueue();
    unbindControls();
    unbindJobList();
    clearInterval(queueTimer);
    clearInterval(jobsTimer);
    document.removeEventListener("visibilitychange", handleVisibility);
  }
}

export function withoutDeletedJobsForTest(jobs, deletedJobNames) {
  const hidden = deletedJobNames instanceof Set ? deletedJobNames : new Set(deletedJobNames || []);
  return jobs.filter((job) => !hidden.has(jobName(job)));
}

export function deleteJobErrorMessageForTest(error) {
  const code = String(error?.payload?.code || "");
  if (code === "job_files_in_use") return t("dashboard.delete_job_in_use");
  if (code === "job_delete_failed") return t("dashboard.delete_job_backend_failed");
  if (String(error?.message || "") === "Failed to fetch") return t("dashboard.delete_job_network_failed");
  return error?.message || t("common.error");
}

function mergeJob(jobs, nextJob) {
  const index = jobs.findIndex((job) => job.job_dir === nextJob.job_dir);
  if (index < 0) {
    return [nextJob, ...jobs].sort(compareJobs);
  }
  const currentVersion = Number(jobs[index].state_version || 0);
  const nextVersion = Number(nextJob.state_version || 0);
  if (currentVersion > 0 && (nextVersion === 0 || currentVersion > nextVersion)) return jobs;
  const merged = jobs.slice();
  merged[index] = {
    ...merged[index],
    ...nextJob,
    files: nextJob.files || merged[index].files
  };
  return merged.sort(compareJobs);
}

export function mergeJobSnapshotsForTest(currentJobs, nextJobs, { preserveMissing = false } = {}) {
  const currentByName = new Map((currentJobs || []).map((job) => [jobName(job), job]));
  const merged = (nextJobs || []).map((job) => {
    const current = currentByName.get(jobName(job));
    if (!current) return job;
    const currentVersion = Number(current.state_version || 0);
    const nextVersion = Number(job.state_version || 0);
    if (currentVersion > 0 && (nextVersion === 0 || currentVersion > nextVersion)) return current;
    return { ...current, ...job, files: job.files || current.files };
  });
  if (preserveMissing) {
    const nextNames = new Set(merged.map((job) => jobName(job)));
    for (const job of currentJobs || []) {
      if (!nextNames.has(jobName(job))) merged.push(job);
    }
  }
  return merged.sort(compareJobs);
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
    <div id="dashboard-jobs">${skeletonGrid(4)}</div>
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
  let searchTimer = null;
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
  return () => clearTimeout(searchTimer);
}

function bindJobListActions(reload) {
  const target = document.getElementById("dashboard-jobs");
  if (!target) return () => {};
  const handleClick = (event) => {
    if (event.target.closest("[data-retry]")) reload();
  };
  target.addEventListener("click", handleClick);
  return () => target.removeEventListener("click", handleClick);
}

function bindJobDelete(removeJob) {
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
  const key = `${filter}|${search}|${projects.map((project) => `${project.id}|${project.name}`).join("\n")}|${jobs.map((job) => `${job.job_dir}|${job.batch_id || ""}|${job.status}|${job.updated_at}|${job.state_version || 0}|${job.stage_progress ?? ""}|${job.project_id || ""}|${(job.files || []).length}`).join("\n")}`;
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
      return emptyState({
        title: t("dashboard.no_matches_title"),
        body: t("dashboard.no_matches"),
        className: "dashboard-no-results"
      });
    }
    return emptyState({
      title: t("dashboard.empty_title"),
      body: t("dashboard.no_jobs"),
      className: "onboarding-empty",
      contentHtml: `
        <div class="onboarding-steps" aria-label="${t("dashboard.empty_title")}">
          <a href="#/health"><span>1</span>${t("dashboard.empty_step_health")}</a>
          <a href="#/new"><span>2</span>${t("dashboard.empty_step_new")}</a>
          <span><span>3</span>${t("dashboard.empty_step_review")}</span>
        </div>`,
      actionHtml: `<a class="button primary" href="#/new">+ ${t("dashboard.new_job")}</a>`
    });
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

function compareActionableJobs(a, b) {
  const priority = { review: 0, failed: 1, processing: 2 };
  const groupA = statusGroup(a.status);
  const groupB = statusGroup(b.status);
  const priorityDiff = (priority[groupA] ?? 3) - (priority[groupB] ?? 3);
  return priorityDiff || compareJobs(a, b);
}
