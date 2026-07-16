import { API } from "./api.js";
import { renderCovers } from "./cover-panel.js";
import { renderClips } from "./clip-editor.js";
import { renderDownloadsSection } from "./download-section.js";
import { renderJobDetailTabs } from "./detail-tabs.js";
import { renderTimelineLegend, setPreviewOrientation } from "./detail-layout.js";
import { renderEnhancements } from "./enhancement-panel.js";
import { errorHintHtml } from "./error-hints.js";
import { t } from "./i18n.js";
import {
  renderJobActions,
  renderJobError,
  renderReviewActions,
  renderSourceWarning,
} from "./job-actions.js";
import { renderLiveProgress, renderStage, updateLiveStatus } from "./job-status.js";
import { renderTranscript } from "./transcript-editor.js";
import { renderRevisionHistory } from "./revision-history.js";
import {
  STAGES,
  basename,
  escapeHtml,
  formatDate,
  formatTime,
  statusGroup,
  statusLabelKey,
} from "./utils.js";

export function renderJobDetailShell() {
  return `
    <div id="section-head"></div>
    <div id="section-review"></div>
    <div id="section-source-warning"></div>
    <section class="panel live-progress" id="section-progress"></section>
    ${renderJobDetailTabs()}
    <div class="job-workspace">
      <div
        class="job-workspace-panel"
        id="job-workspace-review"
        role="tabpanel"
        aria-labelledby="job-workspace-tab-review"
        data-job-workspace-panel="review"
      >
        <section class="panel">
          <h2>${t("job.preview")}</h2>
          <div id="section-preview"></div>
        </section>
        <section class="panel timeline-wrap">
          <div class="section-head compact">
            <h2>${t("job.timeline")}</h2>
            <div class="timeline-controls">
              <span id="timeline-window" class="timeline-window"></span>
              <button class="button compact-button" id="timeline-reset" type="button">${t("timeline.reset")}</button>
            </div>
          </div>
          ${renderTimelineLegend()}
          <canvas class="timeline" role="img" aria-label="${t("timeline.aria")}"></canvas>
        </section>
        <section class="detail-grid resizable" id="detail-split">
          <div class="panel">
            <h2>${t("job.transcript")}</h2>
            <div id="section-transcript"></div>
          </div>
          <button class="detail-resizer" id="detail-resizer" type="button" aria-label="${t("layout.resize_panels")}" title="${t("layout.resize_panels")}"></button>
          <div class="panel">
            <h2>${t("job.clips")}</h2>
            <div id="section-clips"></div>
          </div>
        </section>
      </div>
      <div
        class="job-workspace-panel"
        id="job-workspace-enhance"
        role="tabpanel"
        aria-labelledby="job-workspace-tab-enhance"
        data-job-workspace-panel="enhance"
        hidden
      >
        <section class="panel cover-panel">
          <h2>${t("cover.title")}</h2>
          <div id="section-covers"><div class="loading">${t("common.loading")}</div></div>
        </section>
        <section class="panel enhancements-panel">
          <h2>${t("enhance.title")}</h2>
          <div id="section-enhancements"><div class="loading">${t("common.loading")}</div></div>
        </section>
      </div>
      <div
        class="job-workspace-panel"
        id="job-workspace-export"
        role="tabpanel"
        aria-labelledby="job-workspace-tab-export"
        data-job-workspace-panel="export"
        hidden
      >
        <section class="panel">
          <h2>${t("job.downloads")}</h2>
          <div class="downloads" id="section-downloads"></div>
        </section>
      </div>
      <div
        class="job-workspace-panel"
        id="job-workspace-advanced"
        role="tabpanel"
        aria-labelledby="job-workspace-tab-advanced"
        data-job-workspace-panel="advanced"
        hidden
      >
        <div id="section-actions"></div>
        <section class="panel performance-panel">
          <h2>${t("job.performance")}</h2>
          <div id="section-stage-timings"></div>
        </section>
        <section class="panel revision-panel">
          <h2>${t("revisions.title")}</h2>
          <div id="section-revisions"></div>
        </section>
        <section class="panel pipeline-panel">
          <details class="debug-details">
            <summary>${t("job.pipeline_debug")}</summary>
            <div class="pipeline" id="section-pipeline"></div>
          </details>
        </section>
        <section class="panel" id="section-meta"></section>
      </div>
    </div>
  `;
}

export function selectPreviewName(files) {
  if (files.has("web_preview.mp4")) return "web_preview.mp4";
  if (files.has("review.mp4")) return "review.mp4";
  if (files.has("final.mp4")) return "final.mp4";
  return "";
}

export function normalizeJobDetailPayload(payload = {}) {
  return {
    ...payload,
    manifest: objectOrEmpty(payload.manifest),
    cuts: objectOrEmpty(payload.cuts),
    transcript: objectOrEmpty(payload.transcript),
    stageTimings: objectOrEmpty(payload.stageTimings),
    revisions: Array.isArray(payload.revisions) ? payload.revisions : [],
  };
}

function objectOrEmpty(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

export function updateJobDetailView(
  job,
  files,
  payload,
  {
    isEditingClips = false,
    isEditingTranscript = false,
    bindPreview,
  } = {},
) {
  payload = normalizeJobDetailPayload(payload);
  const { manifest = {}, cuts = {}, transcript = {} } = payload;
  const safeHtml = (id, html) => {
    const el = document.getElementById(id);
    if (el && el.innerHTML !== html) el.innerHTML = html;
  };
  const safeRenderHtml = (id, render) => {
    try {
      safeHtml(id, render());
    } catch (error) {
      console.error(`Render failed for ${id}`, error);
      safeHtml(id, `<div class="error">${errorHintHtml(error.message || String(error))}</div>`);
    }
  };

  safeHtml("section-head", `
    <section class="page-head">
      <div>
        <p class="page-subtitle"><a href="#/">${t("nav.dashboard")}</a> / ${t("job.detail")}</p>
        <h1 class="page-title">${escapeHtml(manifest.source_name || basename(job.source_path))}</h1>
      </div>
      <span class="badge ${statusGroup(job.status)}">${t(statusLabelKey(job.status))}</span>
    </section>
  `);

  safeHtml("section-review", job.status === "needs_review" ? renderReviewActions(payload.quality) : "");
  safeHtml("section-source-warning", renderSourceWarning(payload.corrupt));
  safeHtml("section-actions", renderJobActions());
  safeHtml("section-progress", renderLiveProgress(job));
  safeRenderHtml("section-stage-timings", () => renderStageTimings(payload.stageTimings));
  safeRenderHtml("section-revisions", () => renderRevisionHistory(payload.revisions));
  safeHtml("section-pipeline", STAGES.map((stage) => renderStage(stage, job, files)).join(""));

  const preview = selectPreviewName(files);
  const previewFile = preview ? files.get(preview) : null;
  const previewVersion = previewFile ? `${previewFile.size_bytes || 0}-${previewFile.modified_at || 0}` : "";
  const previewUrl = preview ? API.jobFileUrl(basename(job.job_dir), preview, false, previewVersion) : "";

  const previewContainer = document.getElementById("section-preview");
  if (previewContainer) {
    try {
      if (!preview) {
        if (previewContainer.innerHTML !== `<div class="empty">${t("job.no_preview")}</div>`) {
          previewContainer.innerHTML = `<div class="empty">${t("job.no_preview")}</div>`;
        }
      } else {
        let video = previewContainer.querySelector("video");
        if (!video) {
          previewContainer.innerHTML = `
            <div class="custom-video-player" data-video-player>
              <video class="video-preview" src="${previewUrl}" controls preload="metadata"></video>
            </div>
            <p class="preview-source">${t("job.playing_file")}: ${escapeHtml(preview)}</p>
          `;
          video = previewContainer.querySelector("video");
          setPreviewOrientation(video);
          bindPreview?.(video);
        } else if (video.getAttribute("src") !== previewUrl) {
          video.setAttribute("src", previewUrl);
          video.load();
          setPreviewOrientation(video);
          bindPreview?.(video);
        } else {
          setPreviewOrientation(video);
          bindPreview?.(video);
        }
        const sourceLabel = previewContainer.querySelector(".preview-source");
        if (sourceLabel) sourceLabel.textContent = `${t("job.playing_file")}: ${preview}`;
      }
    } catch (error) {
      console.error("Render failed for section-preview", error);
      previewContainer.innerHTML = `<div class="error">${errorHintHtml(error.message || String(error))}</div>`;
    }
  }

  if (!isEditingTranscript) {
    safeRenderHtml("section-transcript", () => renderTranscript(transcript));
  }

  if (!isEditingClips) {
    safeRenderHtml("section-clips", () => renderClips(cuts, payload.feedback));
  }

  safeRenderHtml("section-covers", () => renderCovers(basename(job.job_dir), files, payload.cover, manifest, cuts, transcript, payload.health));
  safeRenderHtml("section-enhancements", () => renderEnhancements(basename(job.job_dir), files, payload));
  safeRenderHtml("section-downloads", () => renderDownloadsSection(basename(job.job_dir), files));

  safeHtml("section-meta", `
    <div class="meta">${t("common.created")}: ${escapeHtml(formatDate(job.created_at))} · ${t("common.duration")}: ${formatTime(manifest.duration_seconds || cuts.duration_seconds || 0)}</div>
    ${job.error ? renderJobError(job) : ""}
  `);

  updateLiveStatus(job);
}

export function renderStageTimings(stageTimings) {
  const rows = Array.isArray(stageTimings?.stages)
    ? stageTimings.stages
        .filter((item) => item?.status === "complete" && Number.isFinite(Number(item.duration_seconds)))
        .sort((a, b) => Number(b.duration_seconds) - Number(a.duration_seconds))
        .slice(0, 6)
    : [];
  if (!rows.length) {
    return `<div class="empty">${t("job.performance_empty")}</div>`;
  }
  const total = Number(stageTimings.total_duration_seconds);
  const totalHtml = Number.isFinite(total) && total > 0
    ? `<span>${t("job.performance_total")}: <strong>${formatTime(total)}</strong></span>`
    : "";
  const timingBreakdown = (item) => {
    const wait = Number(item.resource_wait_seconds);
    const execution = Number(item.execution_seconds);
    if (!Number.isFinite(wait) && !Number.isFinite(execution)) return "";
    const parts = [];
    if (Number.isFinite(wait)) parts.push(`${t("job.performance_wait")} ${formatTime(wait)}`);
    if (Number.isFinite(execution)) parts.push(`${t("job.performance_execution")} ${formatTime(execution)}`);
    return `<small class="stage-timing-breakdown">${parts.join(" · ")}</small>`;
  };
  return `
    <div class="stage-timings">
      <div class="stage-timings-head">
        <p class="muted">${t("job.performance_note")}</p>
        ${totalHtml}
      </div>
      <div class="stage-timing-list">
        ${rows.map((item) => `
          <div class="stage-timing-row">
            <span>${escapeHtml(t(`stage.${item.stage}`))}</span>
            <span class="stage-timing-values">
              <strong>${formatTime(item.duration_seconds)}</strong>
              ${timingBreakdown(item)}
            </span>
          </div>
        `).join("")}
      </div>
    </div>
  `;
}
