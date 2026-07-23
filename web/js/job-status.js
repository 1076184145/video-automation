import { t } from "./i18n.js";
import {
  escapeHtml,
  formatDate,
  stageForStatus,
  statusGroup,
  statusLabelKey,
} from "./utils.js";

export function renderStage(stage, job, files) {
  const complete = stageComplete(stage, job, files);
  const activeStages = Array.isArray(job.active_stages)
    ? job.active_stages.map((item) => String(item?.stage || ""))
    : [];
  const current = !complete && (
    activeStages.includes(stage) || (job.current_stage || stageForStatus(job.status)) === stage
  );
  const failed = job.status === "failed";
  return `<div class="stage ${failed ? "failed" : current ? "current" : complete ? "done" : ""}" title="${stage}">
    <div class="stage-dot">${failed ? "!" : complete ? "✓" : ""}</div>
    <div class="stage-label">${t(`stage.${stage}`)}</div>
  </div>`;
}

function stageComplete(stage, job, files) {
  if (job.status === "done") return true;
  const outputs = {
    probe: "manifest.json",
    detect_corruption: "corrupt.json",
    extract_audio: "audio.wav",
    transcribe: "transcript.json",
    detect_silence: "silence.json",
    detect_freeze: "freeze.json",
    detect_scenes: "scene.json",
    plan_cuts: "cuts.json",
    refine_cuts: "clip_refinement.json",
    style_subtitles: "subtitles.ass",
    plan_crop: "crop_plan.json",
    plan_uvr: "uvr_plan.json",
    plan_render: "render_preview.json",
    render_review: "review.mp4",
    render_final: "final.mp4",
  };
  return files.has(outputs[stage]);
}

export function renderLiveProgress(job) {
  if (["review", "done", "failed"].includes(statusGroup(job.status))) return "";
  const runtime = job.runtime && typeof job.runtime === "object" ? job.runtime : {};
  const canceling = Boolean(runtime.queue?.cancel_requested);
  const percent = runtime.stale || canceling || typeof job.stage_progress !== "number" ? null : Math.round(job.stage_progress);
  const message = canceling
    ? t("queue.canceling")
    : runtime.stale
    ? t("job.stale_task_message")
    : job.stage_message || (job.current_stage ? `${job.current_stage} / ${job.status}` : job.status);
  const started = !runtime.stale && job.stage_started_at ? `${t("job.stage_started")}: ${escapeHtml(formatDate(job.stage_started_at))}` : "";
  const runtimeActions = runtime.can_cancel || runtime.can_delete
    ? `
      <div class="live-progress-actions">
        ${runtime.stale ? `<span class="notice warning">${t("job.stale_task_detected")}</span>` : ""}
        ${runtime.can_cancel ? `<button class="button danger" id="cancel-job" type="button">${t("job.cancel_job")}</button>` : ""}
        ${runtime.can_delete ? `<button class="button danger" id="delete-stale-job" type="button">${t("job.delete_job")}</button>` : ""}
      </div>`
    : "";
  return `
    <div class="live-progress-head">
      <div>
        <h2>${t("job.current_progress")}</h2>
        <p id="stage-progress-text" class="page-subtitle">${escapeHtml(message)}${started ? ` · ${started}` : ""}</p>
      </div>
      <span id="stage-progress-percent" class="badge ${runtime.stale ? "failed" : statusGroup(job.status)}">${runtime.stale ? t("job.stale_task_label") : canceling ? t("queue.canceling") : percent === null ? t(statusLabelKey(job.status)) : `${percent}%`}</span>
    </div>
    ${runtime.stale ? "" : `<div class="progress stage-progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percent ?? 0}" aria-label="${t("job.current_progress")}"><span id="stage-progress-fill" style="width: ${percent === null ? 0 : percent}%"></span></div>`}
    ${runtimeActions}
  `;
}

export function deriveLiveProgress(job) {
  if (job.runtime?.stale) return;
  if (typeof job.stage_progress === "number" && job.stage_progress >= 100) return;
  if (job.current_stage !== "transcribe" || !job.stage_started_at || !job.stage_estimate_seconds) return;
  const started = Date.parse(job.stage_started_at);
  if (!Number.isFinite(started)) return;
  const elapsed = Math.max(0, (Date.now() - started) / 1000);
  const estimate = Number(job.stage_estimate_seconds);
  if (!Number.isFinite(estimate) || estimate <= 0) return;
  job.stage_progress = Math.min(95, Math.max(0, elapsed / estimate * 100));
  job.stage_message = t("job.transcription_progress").replace("{elapsed}", String(Math.round(elapsed)));
}

export function updateLiveStatus(job) {
  const runtime = job.runtime && typeof job.runtime === "object" ? job.runtime : {};
  const canceling = Boolean(runtime.queue?.cancel_requested);
  const badge = document.querySelector(".page-head .badge");
  if (badge) {
    badge.className = `badge ${runtime.stale ? "failed" : statusGroup(job.status)}`;
    badge.textContent = runtime.stale ? t("job.stale_task_label") : canceling ? t("queue.canceling") : t(statusLabelKey(job.status));
  }
  if (runtime.stale) {
    const text = document.getElementById("stage-progress-text");
    if (text) text.textContent = t("job.stale_task_message");
    const percentBadge = document.getElementById("stage-progress-percent");
    if (percentBadge) {
      percentBadge.className = "badge failed";
      percentBadge.textContent = t("job.stale_task_label");
    }
    return;
  }
  const percent = canceling || typeof job.stage_progress !== "number" ? null : Math.round(job.stage_progress);
  const message = canceling ? t("queue.canceling") : job.stage_message || (job.current_stage ? `${job.current_stage} / ${job.status}` : job.status);
  const started = job.stage_started_at ? `${t("job.stage_started")}: ${formatDate(job.stage_started_at)}` : "";
  const text = document.getElementById("stage-progress-text");
  if (text) {
    text.textContent = `${message}${started ? ` · ${started}` : ""}`;
  }
  const percentBadge = document.getElementById("stage-progress-percent");
  if (percentBadge) {
    percentBadge.className = `badge ${statusGroup(job.status)}`;
    percentBadge.textContent = canceling ? t("queue.canceling") : percent === null ? t(statusLabelKey(job.status)) : `${percent}%`;
  }
  const fill = document.getElementById("stage-progress-fill");
  if (fill && percent !== null) {
    fill.style.width = `${percent}%`;
  }
  const progressBar = document.querySelector(".stage-progress");
  if (progressBar) {
    progressBar.setAttribute("aria-valuenow", String(percent ?? 0));
  }
}
