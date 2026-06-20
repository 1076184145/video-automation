import { API } from "./api.js";
import { renderAiDisclosure } from "./ai-disclosure.js";
import { fileIcon } from "./download-section.js";
import { t } from "./i18n.js";
import { setButtonLoading, showToast } from "./toast.js";
import { escapeHtml, formatTime } from "./utils.js";
export function renderEnhancements(jobName, files, payload) {
  const settings = payload.health?.settings?.optional_modules || {};
  const llmConfigured = Boolean(settings.llm_model) && payload.health?.settings?.covers?.openai_api_key_configured !== false;
  return `
    <div class="enhancement-grid">
      ${renderSegmentsPanel(jobName, files, payload.segments)}
      ${renderMetadataPanel(payload.metadata, llmConfigured)}
      ${renderHighlightsPanel(payload.highlights, llmConfigured)}
      ${renderHighlightCutPanel(jobName, payload.highlightCut, payload.highlightRender, files)}
      ${renderSubtitleTranslationPanel(jobName, files, llmConfigured)}
      ${renderPublishPanel(jobName, files, payload.publishPackage)}
      ${renderProjectExportPanel(jobName, files, payload.projectExport)}
    </div>
    <div id="enhancement-message"></div>
  `;
}

function renderPlatformChecks(idPrefix = "enhance") {
  const defaults = idPrefix === "publish" ? new Set(["douyin", "bilibili"]) : new Set(["douyin"]);
  return ["douyin", "bilibili", "youtube_shorts"].map((platform) => `
    <label class="check compact-check"><input class="${idPrefix}-platform" type="checkbox" value="${platform}" ${defaults.has(platform) ? "checked" : ""} /> ${t(`platform.${platform}`)}</label>
  `).join("");
}

function renderSegmentsPanel(jobName, files, segments) {
  const platformHtml = (segments?.platforms || []).map((platform) => {
    const rows = (platform.segments || []).map((segment) => {
        const file = segment.file || "";
        const exists = files.has(file);
        return `<a class="mini-row ${exists ? "" : "disabled"}" ${exists ? `download href="${API.jobFileUrl(jobName, file, true)}"` : ""}>${escapeHtml(file)} · ${formatTime(segment.duration)}</a>`;
      }).join("");
    const label = `${t(`platform.${platform.name}`)} · ${platform.segment_count || 0}`;
    return platform.segments?.length > 6
      ? `<details class="enhancement-result-details"><summary>${label}</summary><div class="mini-list">${rows}</div></details>`
      : `<div class="mini-list"><strong>${label}</strong>${rows}</div>`;
  }).join("");
  return `
    <article class="enhancement-card">
      <h3>${t("enhance.segments")}</h3>
      <p class="muted">${t("enhance.segments_note")}</p>
      <div class="inline-options">${renderPlatformChecks("segment")}</div>
      <button class="button" id="generate-segments" type="button">${t("enhance.generate_segments")}</button>
      ${platformHtml || `<div class="empty">${t("enhance.no_segments")}</div>`}
    </article>
  `;
}

function renderMetadataPanel(metadata, llmConfigured) {
  const value = escapeHtml(JSON.stringify(metadata || metadataTemplate(), null, 2));
  return `
    <article class="enhancement-card">
      <h3>${t("enhance.metadata")}</h3>
      <p class="muted">${llmConfigured ? t("enhance.metadata_note") : t("enhance.llm_missing")}</p>
      ${llmConfigured ? renderAiDisclosure("text") : ""}
      <div class="field compact">
        <label for="metadata-platform">${t("enhance.platform")}</label>
        <select id="metadata-platform">
          ${["douyin", "bilibili", "youtube_shorts"].map((platform) => `<option value="${platform}">${t(`platform.${platform}`)}</option>`).join("")}
        </select>
      </div>
      <div class="button-row">
        <button class="button" id="generate-metadata" type="button" ${llmConfigured ? "" : "disabled"}>${t("enhance.generate_metadata")}</button>
        <button class="button primary" id="save-metadata" type="button">${t("common.save")}</button>
      </div>
      <textarea class="json-editor" id="metadata-json" spellcheck="false">${value}</textarea>
    </article>
  `;
}

function renderHighlightsPanel(highlights, llmConfigured) {
  const items = highlights?.highlights || [];
  return `
    <article class="enhancement-card">
      <h3>${t("enhance.highlights")}</h3>
      <p class="muted">${llmConfigured ? t("enhance.highlights_note") : t("enhance.llm_missing")}</p>
      ${llmConfigured ? renderAiDisclosure("text") : ""}
      <button class="button" id="generate-highlights" type="button" ${llmConfigured ? "" : "disabled"}>${t("enhance.generate_highlights")}</button>
      ${highlights?.summary ? `<p>${escapeHtml(highlights.summary)}</p>` : ""}
      ${items.length ? renderCompactMiniList(items, (item) => `
        <button class="mini-row" type="button" data-seek="${Number(item.start || 0)}">
          ${formatTime(item.start)}-${formatTime(item.end)} · ${escapeHtml(String(item.score ?? "-"))} · ${escapeHtml(item.reason || "")}
        </button>
      `) : `<div class="empty">${t("enhance.no_highlights")}</div>`}
    </article>
  `;
}

function renderHighlightCutPanel(jobName, highlightCut, highlightRender, files) {
  const clips = Array.isArray(highlightCut?.clips) ? highlightCut.clips : [];
  const renderStatus = highlightRender?.status || "";
  const hasOutput = files.has("highlight.mp4");
  const canRender = clips.length > 0 && renderStatus !== "rendering";
  const renderMessage = renderStatus === "rendering"
    ? `<div class="notice">${t("enhance.highlight_rendering")}</div>`
    : renderStatus === "done"
      ? `<div class="notice">${t("enhance.highlight_render_ready")}</div>`
      : renderStatus === "failed"
        ? `<div class="error">${t("enhance.highlight_render_failed")}${escapeHtml(highlightRender?.error || "")}</div>`
        : "";
  return `
    <article class="enhancement-card">
      <h3>${t("enhance.highlight_cut")}</h3>
      <p class="muted">${t("enhance.highlight_cut_note")}</p>
      <div class="inline-input compact-inline">
        <label class="field compact" for="highlight-cut-target">
          <span>${t("enhance.highlight_cut_target")}</span>
          <input id="highlight-cut-target" type="number" min="5" max="600" step="5" value="${escapeHtml(String(highlightCut?.target_seconds || 60))}" />
        </label>
        <button class="button" id="generate-highlight-cut" type="button">${t("enhance.generate_highlight_cut")}</button>
        <button class="button primary" id="render-highlight-cut" type="button" ${canRender ? "" : "disabled"}>${renderStatus === "rendering" ? t("common.loading") : t("enhance.render_highlight_cut")}</button>
      </div>
      ${renderMessage}
      ${hasOutput ? `<a class="button download-link file-video" download href="${API.jobFileUrl(jobName, "highlight.mp4", true)}">${fileIcon("highlight.mp4")} ${t("common.download")} highlight.mp4</a>` : ""}
      ${highlightCut?.status === "ready" ? `<p>${t("enhance.highlight_cut_summary")
        .replace("{duration}", formatTime(highlightCut.duration_seconds || 0))
        .replace("{count}", escapeHtml(String(highlightCut.selected_clip_count || clips.length)))}</p>` : ""}
      ${clips.length ? renderCompactMiniList(clips, (clip) => `
        <button class="mini-row" type="button" data-seek="${Number(clip.start || 0)}">
          #${escapeHtml(String(clip.selection_rank || ""))} · ${formatTime(clip.start)}-${formatTime(clip.end)} · ${escapeHtml(String(clip.final_score ?? "-"))} · ${escapeHtml((clip.semantic_reasons || []).join(" / ") || clip.reason || "")}
        </button>
      `) : `<div class="empty">${t("enhance.no_highlight_cut")}</div>`}
    </article>
  `;
}

function renderSubtitleTranslationPanel(jobName, files, llmConfigured) {
  const languages = ["zh", "en", "ko", "ja"];
  const renderableTargets = languages.filter((language) => files.has(`subtitles_translated_${language}_clipped.ass`));
  const translatedFiles = Array.from(files.keys())
    .filter((name) => /^(transcript|subtitles)_translated_[a-z]+.*\.(json|txt|srt|ass)$/i.test(name))
    .sort();
  const canRenderDefault = renderableTargets.includes("zh");
  return `
    <article class="enhancement-card">
      <h3>${t("enhance.subtitle_translation")}</h3>
      <p class="muted">${llmConfigured ? t("enhance.subtitle_translation_note") : t("enhance.llm_missing")}</p>
      ${llmConfigured ? renderAiDisclosure("text") : ""}
      <div class="field compact">
        <label for="subtitle-translation-target">${t("enhance.subtitle_translation_target")}</label>
        <select id="subtitle-translation-target">
          ${languages.map((language) => `<option value="${language}" ${language === "zh" ? "selected" : ""}>${t(`enhance.target_${language}`)}</option>`).join("")}
        </select>
      </div>
      <div class="inline-actions">
        <button class="button" id="translate-subtitles" type="button" ${llmConfigured ? "" : "disabled"}>${t("enhance.generate_subtitle_translation")}</button>
        <button class="button" id="render-translated-subtitles" type="button" data-renderable-targets="${escapeHtml(renderableTargets.join(","))}" ${canRenderDefault ? "" : "disabled"}>${t("enhance.subtitle_translation_render")}</button>
      </div>
      ${translatedFiles.length
        ? renderCompactMiniList(translatedFiles, (name) => `<a class="mini-row" download href="${API.jobFileUrl(jobName, name, true)}">${escapeHtml(name)}</a>`)
        : `<div class="empty">${t("enhance.no_subtitle_translation")}</div>`}
    </article>
  `;
}

function renderPublishPanel(jobName, files, publishPackage) {
  const platformCards = (publishPackage?.platforms || []).map((platform) => renderPublishPlatformCard(jobName, files, platform)).join("");
  const packageFiles = publishPackageFiles(publishPackage, files);
  return `
    <article class="enhancement-card">
      <h3>${t("enhance.publish_center")}</h3>
      <p class="muted">${t("enhance.publish_note")}</p>
      <div class="inline-options">${renderPlatformChecks("publish")}</div>
      <button class="button" id="generate-publish-package" type="button">${t("enhance.generate_publish_package")}</button>
      ${publishPackage ? `<div class="notice">${t("enhance.publish_ready")}</div>` : ""}
      ${platformCards || `<div class="empty">${t("enhance.no_publish_package")}</div>`}
      ${packageFiles.length
        ? renderCompactMiniList(packageFiles, (name) => `<a class="mini-row" download href="${API.jobFileUrl(jobName, name, true)}">${escapeHtml(name)}</a>`)
        : `<div class="empty">${t("common.empty")}</div>`}
    </article>
  `;
}

function renderPublishPlatformCard(jobName, files, platform) {
  const name = String(platform.name || "");
  const preview = platform.metadata_preview || {};
  const prefix = `publish-${name}`;
  const handoffFiles = (platform.handoff?.files || []).map((file) => file.relative_path || file.name).filter(Boolean);
  return `
    <details class="publish-platform-card">
      <summary class="publish-platform-head">
        <strong>${t(`platform.${name}`) || escapeHtml(name)}</strong>
        <span class="badge optional">${escapeHtml(platform.handoff?.mode || "manual_upload")}</span>
      </summary>
      <div class="publish-platform-body">
      <div class="publish-checks">
        ${(platform.checks || []).map((check) => `
          <span class="publish-check ${check.ok ? "ok" : "failed"}">${escapeHtml(check.name)} · ${escapeHtml(check.message || "")}</span>
        `).join("")}
      </div>
      ${publishCopyField(`${prefix}-title`, t("enhance.publish_title"), preview.title || "")}
      ${publishCopyField(`${prefix}-description`, t("enhance.publish_description"), preview.description || "")}
      ${publishCopyField(`${prefix}-hashtags`, t("enhance.publish_hashtags"), (preview.hashtags || []).join(" "))}
      ${publishCopyField(`${prefix}-tags`, t("enhance.publish_tags"), (preview.tags || []).join("\n"))}
      <div class="mini-list">
        ${handoffFiles.map((path) => {
          const exists = files.has(path);
          return `<a class="mini-row ${exists ? "" : "disabled"}" ${exists ? `download href="${API.jobFileUrl(jobName, path, true)}"` : ""}>${escapeHtml(path)}</a>`;
        }).join("")}
      </div>
      </div>
    </details>
  `;
}

function publishCopyField(id, label, value) {
  return `
    <label class="publish-copy-field">
      <span>${label}</span>
      <textarea id="${escapeHtml(id)}" readonly>${escapeHtml(value || "")}</textarea>
      <button class="button" type="button" data-copy-publish="${escapeHtml(id)}">${t("common.copy")}</button>
    </label>
  `;
}

function publishPackageFiles(publishPackage, files) {
  const result = ["publish_package.json", "metadata.json", "cover_vertical.jpg", "cover_landscape.jpg", "final.mp4", "review.mp4"].filter((name) => files.has(name));
  for (const platform of publishPackage?.platforms || []) {
    for (const file of platform.handoff?.files || []) {
      const path = file.relative_path || file.name;
      if (path && files.has(path)) result.push(path);
    }
  }
  return Array.from(new Set(result));
}

function renderProjectExportPanel(jobName, files, projectExport) {
  const exportFiles = projectExportFiles(projectExport);
  return `
    <article class="enhancement-card">
      <h3>${t("enhance.project_export")}</h3>
      <p class="muted">${t("enhance.project_export_note")}</p>
      <div class="inline-options">
        <label class="check compact-check"><input class="project-export-target" type="checkbox" value="premiere" checked /> ${t("enhance.project_premiere")}</label>
        <label class="check compact-check"><input class="project-export-target" type="checkbox" value="jianying" checked /> ${t("enhance.project_jianying")}</label>
      </div>
      <label class="check compact-check"><input id="project-export-include-clips" type="checkbox" /> ${t("enhance.project_include_clips")}</label>
      <button class="button" id="generate-project-export" type="button">${t("enhance.generate_project_export")}</button>
      ${projectExport ? `<div class="notice">${t("enhance.project_export_ready")}</div>` : ""}
      ${exportFiles.length ? renderCompactMiniList(exportFiles, (file) => {
          const path = file.relative_path || file;
          const exists = files.has(path);
          return `<a class="mini-row ${exists ? "" : "disabled"}" ${exists ? `download href="${API.jobFileUrl(jobName, path, true)}"` : ""}>${escapeHtml(path)}</a>`;
        }) : `<div class="empty">${t("enhance.no_project_export")}</div>`}
    </article>
  `;
}

function projectExportFiles(projectExport) {
  const result = [];
  const exports = projectExport?.exports || {};
  Object.values(exports).forEach((entry) => {
    if (!entry || typeof entry !== "object") return;
    (entry.files || []).forEach((file) => result.push(file));
    (entry.clips || []).forEach((file) => result.push(file));
  });
  const seen = new Set();
  return result.filter((file) => {
    const key = file?.relative_path || file?.name || "";
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function renderCompactMiniList(items, renderItem, limit = 6) {
  const visible = items.slice(0, limit);
  const overflow = items.slice(limit);
  return `
    <div class="mini-list">${visible.map(renderItem).join("")}</div>
    ${overflow.length ? `
      <details class="enhancement-result-details">
        <summary>${t("common.show_all_count").replace("{count}", String(items.length))}</summary>
        <div class="mini-list">${overflow.map(renderItem).join("")}</div>
      </details>
    ` : ""}
  `;
}

export function bindEnhancementActions(root, jobName, reload, seekPreview = () => {}) {
  const handler = async (event) => {
    const seekButton = event.target?.closest?.("[data-seek]");
    if (seekButton) {
      seekPreview(Number(seekButton.dataset.seek || 0));
      return;
    }
    const copyPublishButton = event.target?.closest?.("[data-copy-publish]");
    if (copyPublishButton) {
      const source = document.getElementById(copyPublishButton.dataset.copyPublish || "");
      if (source) {
        try {
          await navigator.clipboard.writeText(source.value || "");
          showToast(t("enhance.publish_copied"), "success");
        } catch (error) {
          showToast(`${t("common.copy_path_failed")}${error.message}`, "error");
        }
      }
      return;
    }
    if (event.target.id === "generate-segments") {
      await runEnhancement(event.target, () => API.generateSegments(jobName, { platforms: checkedPlatforms("segment") }), reload, "enhance.started");
    } else if (event.target.id === "generate-metadata") {
      await runEnhancement(event.target, () => API.generateMetadata(jobName, { platform: document.getElementById("metadata-platform")?.value || "douyin", force: true }), reload, "enhance.started");
    } else if (event.target.id === "save-metadata") {
      await runEnhancement(event.target, () => API.saveMetadata(jobName, parseMetadataEditor()), reload, "enhance.saved");
    } else if (event.target.id === "generate-highlights") {
      await runEnhancement(event.target, () => API.generateHighlights(jobName, { force: true }), reload, "enhance.started");
    } else if (event.target.id === "generate-highlight-cut") {
      await runEnhancement(event.target, () => API.generateHighlightCut(jobName, {
        target_seconds: Number(document.getElementById("highlight-cut-target")?.value || 60),
        force: true
      }), reload, "enhance.highlight_cut_ready");
    } else if (event.target.id === "render-highlight-cut") {
      await runEnhancement(event.target, () => API.renderHighlightCut(jobName, {
        target_seconds: Number(document.getElementById("highlight-cut-target")?.value || 60)
      }), reload, "enhance.highlight_render_started");
    } else if (event.target.id === "generate-publish-package") {
      await runEnhancement(event.target, () => API.generatePublishPackage(jobName, { platforms: checkedPlatforms("publish"), force: true }), reload, "enhance.started");
    } else if (event.target.id === "generate-project-export") {
      await runEnhancement(event.target, () => API.generateProjectExport(jobName, { targets: checkedProjectExportTargets(), include_clips: Boolean(document.getElementById("project-export-include-clips")?.checked), force: true }), reload, "enhance.started");
    } else if (event.target.id === "translate-subtitles") {
      await runEnhancement(event.target, () => API.translateSubtitles(jobName, {
        target_language: document.getElementById("subtitle-translation-target")?.value || "zh",
        force: true
      }), reload, "enhance.subtitle_translation_ready");
    } else if (event.target.id === "render-translated-subtitles") {
      await runEnhancement(event.target, () => API.renderTranslatedSubtitles(jobName, {
        target_language: document.getElementById("subtitle-translation-target")?.value || "zh"
      }), reload, "enhance.subtitle_translation_render_started");
    }
  };
  const changeHandler = (event) => {
    if (event.target?.id === "subtitle-translation-target") {
      updateSubtitleTranslationRenderButton();
    }
  };
  root.addEventListener("click", handler);
  root.addEventListener("change", changeHandler);
  updateSubtitleTranslationRenderButton();
  return () => {
    root.removeEventListener("click", handler);
    root.removeEventListener("change", changeHandler);
  };
}

function updateSubtitleTranslationRenderButton() {
  const button = document.getElementById("render-translated-subtitles");
  const select = document.getElementById("subtitle-translation-target");
  if (!button || !select) return;
  const targets = new Set(String(button.dataset.renderableTargets || "").split(",").filter(Boolean));
  button.disabled = !targets.has(select.value);
}

async function runEnhancement(button, action, reload, successKey) {
  setButtonLoading(button, true, t("common.loading"));
  try {
    await action();
    setEnhancementMessage(t(successKey));
    showToast(t(successKey), "success");
    await reload();
  } catch (error) {
    setEnhancementMessage(escapeHtml(error.message), true);
    showToast(error.message, "error");
  } finally {
    setButtonLoading(button, false);
  }
}

function checkedPlatforms(prefix) {
  return Array.from(document.querySelectorAll(`.${prefix}-platform`)).filter((input) => input.checked).map((input) => input.value);
}

function checkedProjectExportTargets() {
  return Array.from(document.querySelectorAll(".project-export-target")).filter((input) => input.checked).map((input) => input.value);
}

function metadataTemplate() {
  return {
    titles: [],
    descriptions: [],
    tags: [],
    hashtags: [],
    cover_titles: [],
    platform_notes: []
  };
}

function parseMetadataEditor() {
  try {
    return JSON.parse(document.getElementById("metadata-json")?.value || "{}");
  } catch (error) {
    throw new Error(`${t("enhance.invalid_json")} ${error.message}`);
  }
}

function setEnhancementMessage(message, isError = false) {
  const box = document.getElementById("enhancement-message");
  if (!box) return;
  box.innerHTML = `<div class="${isError ? "error" : "notice"}">${message}</div>`;
}
export const metadataTemplateForTest = metadataTemplate;
export const renderPlatformChecksForTest = renderPlatformChecks;
export const renderSegmentsPanelForTest = renderSegmentsPanel;
