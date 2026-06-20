import { API } from "./api.js";
import { t } from "./i18n.js";
import { escapeHtml } from "./utils.js";

export function renderDownloadsSection(jobName, files) {
  const translatedVideos = Array.from(files.keys()).filter((name) => /^final_translated_[a-z]+\.mp4$/i.test(name)).sort();
  const translatedAss = Array.from(files.keys()).filter((name) => /^subtitles_translated_[a-z]+.*\.ass$/i.test(name)).sort();
  const translatedText = Array.from(files.keys()).filter((name) => /^transcript_translated_[a-z]+\.(json|txt|srt)$/i.test(name)).sort();
  const coverCandidates = Array.from(files.keys()).filter((name) => /^cover_(9x16|16x9)_\d+\.jpg$/i.test(name));
  const segmentFiles = Array.from(files.keys()).filter((name) => name.startsWith("segments/"));
  const projectExportFiles = Array.from(files.keys()).filter((name) => name.startsWith("project_exports/"));
  const groups = [
    ["download.group_video", ["final.mp4", "highlight.mp4", ...translatedVideos, "review.mp4"]],
    ["download.group_subtitles", [...translatedAss, "subtitles_clipped.ass", "subtitles.ass"]],
    ["download.group_covers", ["cover_vertical.jpg", "cover_landscape.jpg", ...coverCandidates]],
    ["download.group_project", [...projectExportFiles, ...segmentFiles, "publish_package.json", "project_export_manifest.json"]],
    ["download.group_audio", ["audio_hq.flac"]],
  ];
  const advanced = [
    "web_preview.mp4",
    "web_preview.json",
    "thumbnail.jpg",
    ...coverCandidates,
    ...segmentFiles,
    ...projectExportFiles,
    ...translatedText,
    "cover_manifest.json",
    "segments_manifest.json",
    "metadata.json",
    "highlights.json",
    "highlight_cut.json",
    "highlight_render_preview.json",
    "highlight_render_status.json",
    "publish_package.json",
    "project_export_manifest.json",
    "waveform.json",
    "corrupt.json",
    "cuts.json",
    "cuts.md",
    "transcript.json",
    "transcript.srt",
    "crop_plan.json",
    "uvr_plan.json",
    "platform_export_plan.json",
    "bgm_mix_plan.json",
    "webhook_plan.json",
    "render_preview.json",
    "final_render_preview.json",
  ];
  const renderedGroups = groups.map(([titleKey, names]) => renderDownloadGroup(jobName, files, titleKey, names)).filter(Boolean).join("");
  const groupedNames = new Set(groups.flatMap(([, names]) => names));
  const advancedLinks = advanced.filter((name) => files.has(name) && !groupedNames.has(name)).map((name) => link(jobName, files, name)).join("");
  if (!renderedGroups && !advancedLinks) return `<div class="empty">${t("common.empty")}</div>`;
  return `
    <div class="download-group">
      ${renderedGroups}
      ${advancedLinks ? `<details class="download-advanced"><summary>${t("download.group_debug")}</summary><div class="downloads">${advancedLinks}</div></details>` : ""}
    </div>
  `;
}

function renderDownloadGroup(jobName, files, titleKey, names) {
  const available = names.filter((name) => files.has(name));
  if (!available.length) return "";
  const visible = available.slice(0, 6);
  const overflow = available.slice(6);
  return `
    <section class="download-intent-group">
      <h3>${t(titleKey)}</h3>
      <div class="download-primary">${visible.map((name) => link(jobName, files, name, name === "final.mp4")).join("")}</div>
      ${overflow.length ? `
        <details class="download-more">
          <summary>${t("common.show_all_count").replace("{count}", String(available.length))}</summary>
          <div class="download-primary">${overflow.map((name) => link(jobName, files, name)).join("")}</div>
        </details>
      ` : ""}
    </section>
  `;
}

function link(jobName, files, name, primary = false) {
  const info = files.get(name);
  const localPath = info?.path || "";
  return `<span class="download-segment">
    <a class="button download-link ${fileKind(name)} ${primary ? "primary" : ""}" download href="${API.jobFileUrl(jobName, name, true)}">${fileIcon(name)} ${t("common.download")} ${escapeHtml(name)}</a>
    ${localPath ? `<button class="button copy-path-button" type="button" data-copy-path="${escapeHtml(localPath)}" title="${t("common.copy_path")}" aria-label="${t("common.copy_path")}">${t("common.copy")}</button>` : ""}
  </span>`;
}

export function fileKind(name) {
  if (/\.(mp4|mov|mkv|webm)$/i.test(name)) return "file-video";
  if (/\.(jpg|jpeg|png|webp)$/i.test(name)) return "file-image";
  if (/\.(ass|srt)$/i.test(name)) return "file-subtitle";
  if (/\.(wav|flac|mp3|m4a)$/i.test(name)) return "file-audio";
  return "file-data";
}

export function fileIcon(name) {
  if (/\.(mp4|mov|mkv|webm)$/i.test(name)) return t("file.video");
  if (/\.(jpg|jpeg|png|webp)$/i.test(name)) return t("cover.image");
  if (/\.(ass|srt)$/i.test(name)) return t("file.subtitle");
  if (/\.(wav|flac|mp3|m4a)$/i.test(name)) return t("file.audio");
  return t("file.data");
}
