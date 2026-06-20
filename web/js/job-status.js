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
  const current = !complete && ((job.current_stage || stageForStatus(job.status)) === stage);
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
  const percent = typeof job.stage_progress === "number" ? Math.round(job.stage_progress) : null;
  const message = job.stage_message || (job.current_stage ? `${job.current_stage} / ${job.status}` : job.status);
  const started = job.stage_started_at ? `${t("job.stage_started")}: ${escapeHtml(formatDate(job.stage_started_at))}` : "";
  return `
    <div class="live-progress-head">
      <div>
        <h2>${t("job.current_progress")}</h2>
        <p id="stage-progress-text" class="page-subtitle">${escapeHtml(message)}${started ? ` · ${started}` : ""}</p>
      </div>
      <span id="stage-progress-percent" class="badge ${statusGroup(job.status)}">${percent === null ? t(statusLabelKey(job.status)) : `${percent}%`}</span>
    </div>
    <div class="progress stage-progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percent ?? 0}" aria-label="${t("job.current_progress")}"><span id="stage-progress-fill" style="width: ${percent === null ? 0 : percent}%"></span></div>
  `;
}

export function deriveLiveProgress(job) {
  if (typeof job.stage_progress === "number" && job.stage_progress >= 100) return;
  if (job.current_stage !== "transcribe" || !job.stage_started_at || !job.stage_estimate_seconds) return;
  const started = Date.parse(job.stage_started_at);
  if (!Number.isFinite(started)) return;
  const elapsed = Math.max(0, (Date.now() - started) / 1000);
  const estimate = Number(job.stage_estimate_seconds);
  if (!Number.isFinite(estimate) || estimate <= 0) return;
  job.stage_progress = Math.min(95, Math.max(0, elapsed / estimate * 100));
  job.stage_message = t("job.whisper_progress").replace("{elapsed}", String(Math.round(elapsed)));
}

export function updateLiveStatus(job) {
  const badge = document.querySelector(".page-head .badge");
  if (badge) {
    badge.className = `badge ${statusGroup(job.status)}`;
    badge.textContent = t(statusLabelKey(job.status));
  }
  const percent = typeof job.stage_progress === "number" ? Math.round(job.stage_progress) : null;
  const message = job.stage_message || (job.current_stage ? `${job.current_stage} / ${job.status}` : job.status);
  const started = job.stage_started_at ? `${t("job.stage_started")}: ${formatDate(job.stage_started_at)}` : "";
  const text = document.getElementById("stage-progress-text");
  if (text) {
    text.textContent = `${message}${started ? ` · ${started}` : ""}`;
  }
  const percentBadge = document.getElementById("stage-progress-percent");
  if (percentBadge) {
    percentBadge.className = `badge ${statusGroup(job.status)}`;
    percentBadge.textContent = percent === null ? t(statusLabelKey(job.status)) : `${percent}%`;
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
