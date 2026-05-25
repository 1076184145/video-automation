import { API } from "./api.js";
import { t } from "./i18n.js";
import { basename, escapeHtml, fileMap, formatDate, jobName, progressForJob, statusGroup, statusLabelKey } from "./utils.js";

let filter = "all";
let search = "";
let lastJobsKey = "";
let searchTimer = null;

export async function renderDashboard() {
  const app = document.getElementById("app");
  let timer = null;
  let healthTimer = null;
  let jobs = [];
  lastJobsKey = "";
  app.innerHTML = pageShell();
  bindControls(() => updateJobs(jobs));

  async function load() {
    try {
      jobs = await API.getJobs();
      updateJobs(jobs);
    } catch (error) {
      const target = document.getElementById("dashboard-jobs");
      target.innerHTML = `<div class="error">${t("common.error")} <button class="button" id="retry">${t("common.retry")}</button></div>`;
      document.getElementById("retry")?.addEventListener("click", load);
    }
  }

  async function loadHealth() {
    try {
      updateHealth(await getHealthWithTimeout(2500));
    } catch {
      updateHealth(null);
    }
  }

  function startPolling() {
    if (document.visibilityState !== "visible") return;
    if (!timer) timer = setInterval(load, 3000);
    if (!healthTimer) healthTimer = setInterval(loadHealth, 60000);
  }

  function stopPolling() {
    if (timer) clearInterval(timer);
    if (healthTimer) clearInterval(healthTimer);
    timer = null;
    healthTimer = null;
  }

  const handleVisibility = () => {
    if (document.visibilityState === "visible") {
      load();
      loadHealth();
      startPolling();
    } else {
      stopPolling();
    }
  };
  document.addEventListener("visibilitychange", handleVisibility);

  await load();
  loadHealth();
  startPolling();
  return () => {
    stopPolling();
    clearTimeout(searchTimer);
    document.removeEventListener("visibilitychange", handleVisibility);
  };
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
        <h1 class="page-title">${t("app.title")}</h1>
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
    <div id="health-banner"></div>
    <div id="dashboard-jobs"><div class="grid"><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div></div></div>
  `;
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

function updateFilterButtons() {
  document.querySelectorAll("[data-filter]").forEach((button) => {
    button.classList.toggle("active", button.dataset.filter === filter);
  });
}

function updateJobs(jobs) {
  const target = document.getElementById("dashboard-jobs");
  if (!target) return;
  const key = `${filter}|${search}|${jobs.map((job) => `${job.job_dir}|${job.status}|${job.updated_at}|${job.stage_progress ?? ""}`).join("\n")}`;
  if (key === lastJobsKey) return;
  lastJobsKey = key;
  target.innerHTML = renderJobs(jobs);
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

function renderJobs(jobs) {
  const visible = jobs.filter((job) => {
    const group = statusGroup(job.status);
    const text = `${job.source_path} ${job.job_dir}`.toLowerCase();
    return (filter === "all" || filter === group) && text.includes(search.toLowerCase());
  });
  if (!visible.length) return `<div class="empty">${t("dashboard.no_jobs")} <a class="button primary" href="#/new">+ ${t("dashboard.new_job")}</a></div>`;
  return `<div class="grid">${visible.map(renderJobCard).join("")}</div>`;
}

function renderJobCard(job) {
  const group = statusGroup(job.status);
  const name = jobName(job);
  const sourceName = basename(job.source_path);
  const files = fileMap(job);
  const thumb = files.has("thumbnail.jpg") ? API.jobFileUrl(name, "thumbnail.jpg") : "";
  const progress = progressForJob(job);
  return `
    <a class="card job-card" href="#/jobs/${encodeURIComponent(name)}">
      <div>
        <h2 class="job-title">${escapeHtml(sourceName || name)} <span class="badge ${group}">${t(statusLabelKey(job.status))}</span></h2>
        <div class="meta">
          <div>${t("common.created")}: ${escapeHtml(formatDate(job.created_at))}</div>
          <div>${t("common.stage")}: <code>${escapeHtml(job.status)}</code></div>
        </div>
        <div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${progress}" aria-label="${t("dashboard.progress")}"><span style="width:${progress}%"></span></div>
      </div>
      <div class="thumb">${thumb ? `<img src="${thumb}" alt="" loading="lazy" />` : ""}</div>
    </a>
  `;
}
