export const STAGES = [
  "probe",
  "detect_corruption",
  "extract_audio",
  "transcribe",
  "detect_silence",
  "detect_freeze",
  "detect_scenes",
  "plan_cuts",
  "style_subtitles",
  "plan_crop",
  "plan_uvr",
  "plan_render",
  "render_review",
  "render_final",
  "render_web_preview"
];

export const STATUS_TO_STAGE = Object.freeze({
  probing: "probe",
  detecting_corruption: "detect_corruption",
  extracting_audio: "extract_audio",
  transcribing: "transcribe",
  detecting_silence: "detect_silence",
  detecting_freeze: "detect_freeze",
  detecting_scenes: "detect_scenes",
  planning_cuts: "plan_cuts",
  styling_subtitles: "style_subtitles",
  planning_crop: "plan_crop",
  planning_uvr: "plan_uvr",
  planning_render: "plan_render",
  rendering_review: "render_review",
  rendering_final: "render_final",
  rendering_web_preview: "render_web_preview"
});

export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

export function basename(path) {
  return String(path || "").split(/[\\/]/).pop() || path || "";
}

export function jobName(job) {
  return basename(job.job_dir);
}

export function statusGroup(status) {
  if (status === "failed" || status === "canceled") return "failed";
  if (status === "done") return "done";
  if (status === "needs_review") return "review";
  return "processing";
}

export function statusLabelKey(status) {
  if (status === "canceled") return "status.canceled";
  return `status.${statusGroup(status)}`;
}

export function formatTime(seconds) {
  const value = Math.max(0, Number(seconds) || 0);
  const total = Math.floor(value);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return `${m}:${String(s).padStart(2, "0")}`;
}

export function formatDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

export function progressForStatus(status) {
  if (status === "done" || status === "needs_review") return 100;
  if (status === "failed" || status === "canceled") return 100;
  const index = STAGES.indexOf(stageForStatus(status));
  return index >= 0 ? Math.round(((index + 1) / STAGES.length) * 100) : 18;
}

export function progressForJob(job) {
  if (["done", "needs_review", "failed", "canceled"].includes(job?.status)) return 100;
  const stage = job?.current_stage || stageForStatus(job?.status);
  const index = STAGES.indexOf(stage);
  return index >= 0 ? Math.round(((index + 1) / STAGES.length) * 100) : 0;
}

export function stageForStatus(status) {
  return STATUS_TO_STAGE[status] || "";
}

export function fileMap(job) {
  const map = new Map();
  for (const file of job.files || []) map.set(file.name, file);
  return map;
}

export function isTerminal(status) {
  return ["done", "needs_review", "failed", "canceled"].includes(status);
}
