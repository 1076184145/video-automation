import { API } from "./api.js";
import { t } from "./i18n.js";
import {
  settingDisplayValue,
  settingEnvLabel,
  settingKeyLabel,
  settingOptionLabel,
  settingRecommendation,
} from "./settings-schema.js";
import { escapeHtml } from "./utils.js";

const groups = [
  ["directories", "settings.directories"],
  ["paths", "settings.paths"],
  ["whisper", "settings.whisper"],
  ["detection", "settings.detection"],
  ["subtitles", "settings.subtitles"],
  ["exports", "settings.exports"],
  ["crop", "settings.crop"],
  ["optional_modules", "settings.optional_modules"],
  ["covers", "settings.covers"],
  ["api", "settings.api"]
];

const SETTINGS_OPEN_GROUPS_KEY = "videoAutomationOpenSettingsGroupsV2";

const editableGroups = [
  {
    title: "settings.edit_whisper",
    fields: [
      { env: "WHISPER_BACKEND", path: ["whisper", "backend"], type: "select", options: ["funasr-whisper", "faster-whisper", "funasr", "cli"] },
      { env: "WHISPER_MODEL", path: ["whisper", "model"] },
      { env: "WHISPER_LANGUAGE", path: ["whisper", "language"], type: "select", options: ["auto", "zh", "en", "ja", "ko"] },
      { env: "FASTER_WHISPER_DEVICE", path: ["whisper", "faster_whisper_device"], type: "select", options: ["cuda", "cpu", "auto"] },
      { env: "FASTER_WHISPER_COMPUTE_TYPE", path: ["whisper", "faster_whisper_compute_type"], type: "select", options: ["int8_float16", "float16", "int8", "float32"] },
      { env: "FASTER_WHISPER_BATCH_SIZE", path: ["whisper", "faster_whisper_batch_size"], type: "number", min: 1, step: 1 },
      { env: "WHISPER_WORD_TIMESTAMPS", path: ["whisper", "word_timestamps"], type: "checkbox" },
      { env: "WHISPER_VAD_FILTER", path: ["whisper", "vad_filter"], type: "checkbox" },
      { env: "WHISPER_INITIAL_PROMPT", path: ["whisper", "initial_prompt"] },
      { env: "TRANSCRIBE_AUDIO_FILTER", path: ["whisper", "transcribe_audio_filter"] }
    ]
  },
  {
    title: "settings.edit_detection",
    fields: [
      { env: "SILENCE_THRESHOLD_DB", path: ["detection", "silence_threshold_db"], type: "number", step: 0.5 },
      { env: "SILENCE_MIN_LENGTH_SECONDS", path: ["detection", "silence_min_length"], type: "number", min: 0, step: 0.1 },
      { env: "SILENCE_MIN_GAP_SECONDS", path: ["detection", "silence_min_gap"], type: "number", min: 0, step: 0.1 },
      { env: "CUT_MIN_CLIP_SECONDS", path: ["detection", "cut_min_clip_seconds"], type: "number", min: 0, step: 0.1 },
      { env: "CUT_MERGE_GAP_SECONDS", path: ["detection", "cut_merge_gap_seconds"], type: "number", min: 0, step: 0.1 },
      { env: "SCENE_THRESHOLD", path: ["detection", "scene_threshold"], type: "number", min: 0, max: 1, step: 0.05 },
      { env: "SOURCE_INTEGRITY_SCAN_ENABLED", path: ["detection", "source_integrity_scan_enabled"], type: "checkbox" }
    ]
  },
  {
    title: "settings.edit_subtitles",
    fields: [
      { env: "ASS_PRESET", path: ["subtitles", "preset"] },
      { env: "ASS_FONT_NAME", path: ["subtitles", "font_name"] },
      { env: "ASS_FONT_SIZE", path: ["subtitles", "font_size"], type: "number", min: 8, step: 1 },
      { env: "ASS_VERTICAL_FONT_SIZE", path: ["subtitles", "vertical_font_size"], type: "number", min: 0, step: 1 },
      { env: "ASS_MAX_LINES", path: ["subtitles", "max_lines"], type: "number", min: 1, step: 1 },
      { env: "ASS_MARGIN_V", path: ["subtitles", "margin_v"], type: "number", min: 0, step: 1 },
      { env: "ASS_OUTLINE", path: ["subtitles", "outline"], type: "number", min: 0, step: 0.5 },
      { env: "ASS_SHADOW", path: ["subtitles", "shadow"], type: "number", min: 0, step: 0.5 },
      { env: "SUBTITLE_MIN_DURATION_SECONDS", path: ["subtitles", "min_duration_seconds"], type: "number", min: 0, step: 0.1 },
      { env: "SUBTITLE_CENSOR_REPLACEMENT", path: ["subtitles", "censor_replacement"] }
    ]
  },
  {
    title: "settings.edit_exports",
    fields: [
      { env: "RENDER_VIDEO_ENCODER", path: ["exports", "render_video_encoder"], type: "select", options: ["libx264", "h264_nvenc"] },
      { env: "RENDER_OUTPUT_FPS", path: ["exports", "render_output_fps"], type: "number", min: 0, step: 1 },
      { env: "RENDER_X264_PRESET", path: ["exports", "render_x264_preset"], type: "select", options: ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow"] },
      { env: "RENDER_X264_CRF", path: ["exports", "render_x264_crf"], type: "number", min: 0, max: 35, step: 1 },
      { env: "RENDER_NVENC_CQ", path: ["exports", "render_nvenc_cq"], type: "number", min: 1, step: 1 },
      { env: "RENDER_NVENC_PREVIEW_CQ", path: ["exports", "render_nvenc_preview_cq"], type: "number", min: 1, step: 1 },
      { env: "WEB_PREVIEW_ENABLED", path: ["exports", "web_preview_enabled"], type: "checkbox" },
      { env: "WEB_PREVIEW_MAX_WIDTH", path: ["exports", "web_preview_max_width"], type: "number", min: 320, step: 1 },
      { env: "WEB_PREVIEW_MAX_HEIGHT", path: ["exports", "web_preview_max_height"], type: "number", min: 320, step: 1 },
      { env: "WEB_PREVIEW_FPS", path: ["exports", "web_preview_fps"], type: "number", min: 1, step: 1 },
      { env: "WEB_PREVIEW_VIDEO_BITRATE", path: ["exports", "web_preview_video_bitrate"] },
      { env: "BGM_VOLUME", path: ["exports", "bgm_volume"], type: "number", min: 0, step: 0.01 },
      { env: "SOURCE_AUDIO_VOLUME", path: ["exports", "source_audio_volume"], type: "number", min: 0, step: 0.01 }
    ]
  },
  {
    title: "settings.edit_ai",
    note: "settings.edit_ai_note",
    fields: [
      { env: "LLM_PROVIDER", path: ["optional_modules", "llm_provider"], type: "select", options: ["openai", "openai-compatible", "google"] },
      { env: "LLM_MODEL", path: ["optional_modules", "llm_model"], placeholder: "settings.placeholder.llm_model" },
      { env: "COVER_PROVIDER", path: ["covers", "provider"], type: "select", options: ["openai", "openai-compatible", "openrouter", "google", "disabled"] },
      { env: "COVER_BASE_URL", path: ["covers", "base_url"] },
      { env: "COVER_MODEL", path: ["covers", "model"] },
      { env: "COVER_COUNT", path: ["covers", "count"], type: "number", min: 1, max: 5, step: 1 },
      { env: "COVER_QUALITY", path: ["covers", "quality"], type: "select", options: ["low", "medium", "high", "auto"] },
      { env: "COVER_OUTPUT_FORMAT", path: ["covers", "output_format"], type: "select", options: ["jpeg", "png", "webp"] },
      { env: "COVER_API_KEY", secret: true, configuredPath: ["covers", "cover_api_key_configured"] },
      { env: "OPENAI_API_KEY", secret: true, configuredPath: ["covers", "openai_api_key_configured"] },
      { env: "GOOGLE_API_KEY", secret: true, configuredPath: ["optional_modules", "google_api_key_configured"] },
      { env: "GOOGLE_BASE_URL", path: ["optional_modules", "google_base_url"] },
      { env: "COVER_HTTP_REFERER", path: ["covers", "http_referer"] },
      { env: "COVER_APP_TITLE", path: ["covers", "app_title"] }
    ]
  },
  {
    title: "settings.edit_modules",
    fields: [
      { env: "API_BATCH_LIMIT", path: ["api", "batch_limit"], type: "number", min: 1, step: 1 },
      { env: "RECORDING_UPLOAD_MAX_BYTES", path: ["api", "recording_upload_max_bytes"], type: "number", min: 0, step: 1048576 },
      { env: "NATIVE_WAVEFORM_ENABLED", path: ["optional_modules", "native_waveform_enabled"], type: "checkbox" },
      { env: "NATIVE_CUTS_ENABLED", path: ["optional_modules", "native_cuts_enabled"], type: "checkbox" },
      { env: "HIGH_QUALITY_AUDIO_ENABLED", path: ["optional_modules", "high_quality_audio_enabled"], type: "checkbox" }
    ]
  }
];

export async function renderSettings(routeValue = "") {
  const message = normalizeSettingsMessage(routeValue);
  const app = document.getElementById("app");
  const loadingTimer = setTimeout(() => {
    app.innerHTML = `<div class="loading">${t("common.loading")}</div>`;
  }, 150);
  try {
    const payload = await API.getHealth();
    clearTimeout(loadingTimer);
    renderSettingsPage(payload, message);
  } catch (error) {
    clearTimeout(loadingTimer);
    app.innerHTML = `<div class="error">${t("common.error")} ${escapeHtml(error.message)} <button class="button" id="retry-settings">${t("common.retry")}</button></div>`;
    document.getElementById("retry-settings")?.addEventListener("click", () => renderSettings());
  }
}

export function normalizeSettingsMessage(value) {
  return typeof value === "string" ? value : "";
}

function renderSettingsPage(payload, message = "") {
  const settings = payload.settings || {};
  const checks = payload.checks || [];
  const recommendedUpdates = recommendedSettingsUpdates(editableGroups, settings, checks);
  const recommendedCount = Object.keys(recommendedUpdates).length;
  const app = document.getElementById("app");
  app.innerHTML = `
    <section class="page-head">
      <div>
        <h1 class="page-title">${t("settings.title")}</h1>
        <p class="page-subtitle">${t("settings.note")}</p>
      </div>
    </section>
    <form class="panel settings-editor" id="settings-editor" novalidate>
      <div class="panel-head">
        <div>
          <h2>${t("settings.edit_title")}</h2>
          <p>${t("settings.edit_note")}</p>
        </div>
        <button class="button primary" type="submit" id="save-settings">${t("settings.save")}</button>
      </div>
      <div id="settings-message" class="${message ? "notice" : "settings-message"}">${message ? escapeHtml(message) : ""}</div>
      <section class="settings-quick-setup">
        <div>
          <h3>${t("settings.quick_title")}</h3>
          <p>${recommendedCount
            ? t("settings.quick_note").replace("{count}", String(recommendedCount))
            : t("settings.quick_ready")}</p>
        </div>
        <button class="button" id="apply-settings-recommendations" type="button" ${recommendedCount ? "" : "disabled"}>
          ${t("settings.quick_apply").replace("{count}", String(recommendedCount))}
        </button>
      </section>
      <div class="settings-edit-grid">
        ${editableGroups.map((group, index) => renderEditableGroup(group, settings, checks, index)).join("")}
      </div>
    </form>
    ${renderSettingsSnapshot(settings, checks)}
  `;
  bindSettingsEditor();
}

export function renderEditableGroup(group, settings, checks, index = 0) {
  const savedOpenGroups = readOpenSettingsGroups();
  const open = savedOpenGroups.has(group.title);
  return `<details class="settings-edit-group" data-settings-group="${escapeHtml(group.title)}"${open ? " open" : ""}>
    <summary class="settings-edit-summary">
      <span>
        <strong>${t(group.title)}</strong>
        <small>${formatItemCount(group.fields.length)}</small>
      </span>
      <span class="settings-disclosure" aria-hidden="true"></span>
    </summary>
    <div class="settings-edit-body">
      ${group.note ? `<p class="settings-edit-note">${t(group.note)}</p>` : ""}
      ${group.fields.map((field) => renderEditableField(field, settings, checks)).join("")}
    </div>
  </details>`;
}

function renderEditableField(field, settings, checks) {
  const value = field.secret ? "" : settingValue(settings, field.path);
  const configured = field.configuredPath ? Boolean(settingValue(settings, field.configuredPath)) : false;
  const recommendationGroup = field.path?.[0] || field.configuredPath?.[0] || "";
  const recommendationKey = field.path?.[1] || field.configuredPath?.[1] || field.env.toLowerCase();
  const recommendation = settingRecommendation({
    env: field.env,
    group: recommendationGroup,
    key: recommendationKey,
    value: field.secret ? configured : value,
    checks,
  });
  const original = field.type === "checkbox" ? String(Boolean(value)) : String(value ?? "");
  const recommended = recommendation.recommended;
  const recommendedAttribute = !field.secret && recommended != null && !recommendation.matches
    ? ` data-recommended="${escapeHtml(JSON.stringify(recommended))}"`
    : "";
  const common = `data-env="${escapeHtml(field.env)}" data-original="${escapeHtml(original)}"${field.secret ? " data-secret=\"1\"" : ""}${recommendedAttribute}`;
  const label = `<span class="settings-edit-label"><span>${escapeHtml(settingEnvLabel(field.env))}</span><small>${escapeHtml(field.env)}</small></span>`;
  const recommendationHtml = renderRecommendation(recommendation);
  if (field.type === "checkbox") {
    return `<label class="settings-edit-field checkbox-field">
      ${label}
      <input type="checkbox" ${common} ${value ? "checked" : ""}>
      ${recommendationHtml}
    </label>`;
  }
  if (field.type === "select") {
    const options = field.options.includes(String(value)) || !String(value)
      ? field.options
      : [String(value), ...field.options];
    return `<label class="settings-edit-field">
      ${label}
      <select ${common}>
        ${options.map((option) => `<option value="${escapeHtml(option)}" ${String(option) === String(value) ? "selected" : ""}>${escapeHtml(settingOptionLabel(field.env, option))}</option>`).join("")}
      </select>
      ${recommendationHtml}
    </label>`;
  }
  const type = field.secret ? "password" : field.type === "number" ? "number" : "text";
  const placeholder = field.secret
    ? (configured ? t("settings.secret_keep") : t("settings.secret_empty"))
    : field.placeholder
      ? t(field.placeholder)
      : "";
  return `<label class="settings-edit-field">
    ${label}
    <input ${common} type="${type}" value="${escapeHtml(value)}" placeholder="${escapeHtml(placeholder)}"${field.min !== undefined ? ` min="${field.min}"` : ""}${field.max !== undefined ? ` max="${field.max}"` : ""}${field.step !== undefined ? ` step="${field.step}"` : ""}>
    ${recommendationHtml}
  </label>`;
}

function bindSettingsEditor() {
  const form = document.getElementById("settings-editor");
  const message = document.getElementById("settings-message");
  const saveButton = document.getElementById("save-settings");
  const applyRecommendations = document.getElementById("apply-settings-recommendations");
  bindSettingsDisclosure(form);
  applyRecommendations?.addEventListener("click", () => {
    let applied = 0;
    for (const input of form?.querySelectorAll("[data-recommended]") || []) {
      let recommended;
      try {
        recommended = JSON.parse(input.dataset.recommended);
      } catch {
        continue;
      }
      if (input.type === "checkbox") input.checked = Boolean(recommended);
      else input.value = String(recommended ?? "");
      input.dispatchEvent(new Event("change", { bubbles: true }));
      applied += 1;
    }
    setSettingsMessage(
      message,
      t("settings.quick_applied").replace("{count}", String(applied)),
    );
    applyRecommendations.disabled = true;
  });
  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const invalid = firstInvalidChangedSettingsControl(form.querySelectorAll("[data-env]"));
    if (invalid) {
      const group = invalid.closest?.("[data-settings-group]");
      if (group) group.open = true;
      requestAnimationFrame(() => {
        invalid.focus?.({ preventScroll: false });
        invalid.reportValidity?.();
      });
      return;
    }
    const updates = collectUpdates(form);
    if (!Object.keys(updates).length) {
      setSettingsMessage(message, t("settings.no_changes"));
      return;
    }
    setButtonLoading(saveButton, true);
    try {
      const payload = await API.updateSettings({ env: updates });
      renderSettingsPage(payload, t("settings.saved"));
    } catch (error) {
      setSettingsMessage(message, `${t("settings.save_failed")} ${error.message}`, true);
    } finally {
      setButtonLoading(saveButton, false);
    }
  });
}

function bindSettingsDisclosure(form) {
  form?.querySelectorAll("[data-settings-group]").forEach((details) => {
    details.addEventListener("toggle", () => {
      const open = [...form.querySelectorAll("[data-settings-group][open]")]
        .map((item) => item.dataset.settingsGroup)
        .filter(Boolean);
      localStorage.setItem(SETTINGS_OPEN_GROUPS_KEY, JSON.stringify(open));
    });
  });
}

function collectUpdates(form) {
  const updates = {};
  for (const input of form.querySelectorAll("[data-env]")) {
    const env = input.dataset.env;
    const isSecret = input.dataset.secret === "1";
    const value = settingsControlValue(input);
    if (isSecret && !value) continue;
    const original = input.dataset.original ?? "";
    if (String(value) !== original) {
      updates[env] = value;
    }
  }
  return updates;
}

export function firstInvalidChangedSettingsControl(controls) {
  for (const input of controls || []) {
    const value = settingsControlValue(input);
    const isSecret = input.dataset?.secret === "1";
    if (isSecret && !value) continue;
    if (String(value) === String(input.dataset?.original ?? "")) continue;
    if (typeof input.checkValidity === "function" && !input.checkValidity()) return input;
  }
  return null;
}

function settingsControlValue(input) {
  if (input.type === "checkbox") return input.checked ? "true" : "false";
  return String(input.value ?? "").trim();
}

function setSettingsMessage(target, text, error = false) {
  if (!target) return;
  target.className = text ? `notice${error ? " warning" : ""}` : "settings-message";
  target.textContent = text || "";
}

function setButtonLoading(button, loading) {
  if (!button) return;
  button.disabled = loading;
  button.classList.toggle("is-loading", loading);
}

function settingValue(settings, path = []) {
  let value = settings;
  for (const key of path) {
    if (value == null || typeof value !== "object") return "";
    value = value[key];
  }
  return value ?? "";
}

export function recommendedSettingsUpdates(fieldGroups, settings, checks) {
  const updates = {};
  for (const group of fieldGroups || []) {
    for (const field of group.fields || []) {
      if (field.secret) continue;
      const value = settingValue(settings, field.path);
      const recommendation = settingRecommendation({
        env: field.env,
        group: field.path?.[0] || "",
        key: field.path?.[1] || field.env.toLowerCase(),
        value,
        checks,
      });
      if (recommendation.recommended != null && !recommendation.matches) {
        updates[field.env] = recommendation.recommended;
      }
    }
  }
  return updates;
}

function renderGroup(titleKey, values, checks) {
  const group = groups.find(([, key]) => key === titleKey)?.[0] || "";
  return `<section class="panel">
    <h2>${t(titleKey)}</h2>
    ${Object.entries(values).map(([key, value]) => {
      const recommendation = settingRecommendation({ group, key, value, checks });
      return `
        <div class="kv">
          <span>${escapeHtml(settingKeyLabel(group, key))}</span>
          <span>${escapeHtml(settingDisplayValue(group, key, value))}</span>
          ${renderRecommendation(recommendation)}
        </div>
      `;
    }).join("") || `<div class="empty">${t("common.empty")}</div>`}
  </section>`;
}

export function renderSettingsSnapshot(settings, checks) {
  const count = groups.reduce(
    (total, [name]) => total + Object.keys(settings[name] || {}).length,
    0,
  );
  return `<details class="settings-snapshot">
    <summary class="settings-snapshot-summary">
      <span>
        <strong>${t("settings.snapshot_title")}</strong>
        <small>${t("settings.snapshot_note")}</small>
      </span>
      <span class="settings-snapshot-count">${formatItemCount(count)}</span>
      <span class="settings-disclosure" aria-hidden="true"></span>
    </summary>
    <div class="settings-grid">
      ${groups.map(([name, key]) => renderGroup(key, settings[name] || {}, checks)).join("")}
    </div>
  </details>`;
}

function renderRecommendation(recommendation) {
  const state = recommendation.matches ? "" : " is-different";
  return `<small class="settings-recommendation${state}">${escapeHtml(recommendation.text)}</small>`;
}

function readOpenSettingsGroups() {
  try {
    const value = JSON.parse(localStorage.getItem(SETTINGS_OPEN_GROUPS_KEY) || "[]");
    return new Set(Array.isArray(value) ? value : []);
  } catch {
    return new Set();
  }
}

function formatItemCount(count) {
  return t("settings.item_count").replace("{count}", String(count));
}
