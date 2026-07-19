import { API } from "./api.js";
import { icon } from "./icons.js";
import { t } from "./i18n.js";
import { basename, escapeHtml, fileMap, formatDate, jobName, progressForJob, statusGroup, statusLabelKey } from "./utils.js";

// Dashboard job-card view layer: pure string builders for job cards, batch
// groups, and bounded card collections. State, polling, and events live in
// dashboard.js; this module only turns job data into HTML.

export const MAX_RENDERED_JOBS = 100;

export function compareJobs(a, b) {
  return String(b.updated_at || "").localeCompare(String(a.updated_at || ""));
}

export function groupedBatches(jobs) {
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

export function renderJobCollection(jobs, projectNames = new Map()) {
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
    const indexA = normalizedBatchIndex(a.batch_index);
    const indexB = normalizedBatchIndex(b.batch_index);
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

function normalizedBatchIndex(value) {
  const index = Number(value);
  return Number.isFinite(index) ? index : Number.MAX_SAFE_INTEGER;
}

export function renderJobCard(job, projectNames = new Map()) {
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
  const canDelete = ["done", "failed"].includes(group);
  const deleteControl = canDelete
    ? `
      <button
        class="job-card-delete"
        type="button"
        data-delete-job="${escapeHtml(name)}"
        aria-label="${t("dashboard.delete_job")}"
        title="${t("dashboard.delete_job")}"
      >
        ${icon("trash")}
      </button>`
    : "";
  return `
    <article class="card job-card ${canDelete ? "has-delete" : ""}">
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
