import { API, isAbortError } from "./api.js";
import { t } from "./i18n.js";
import { setButtonLoading, showToast } from "./toast.js";
import { errorState, loadingState } from "./ui-states.js";
import { escapeHtml } from "./utils.js";

const INSTALLABLE_CHECKS = new Set(["ffmpeg_path", "ffprobe_path"]);
const TRANSCRIPTION_CHECKS = new Set(["faster_whisper", "ctranslate2_cuda"]);
const COVER_LOCAL_CHECKS = new Set([
  "pillow",
  "local_cover_transformers",
  "local_cover_diffusers",
  "local_cover_accelerate",
  "local_cover_bitsandbytes",
]);
const FASTER_WHISPER_INSTALL_COMMAND = "python -m pip install -r requirements-transcription-faster.txt";
const LOCAL_COVER_INSTALL_COMMAND = "python -m pip install -r requirements-local-ai.txt";
const HEALTH_CHECK_CONFIG = {
  root: { env: "VIDEO_AUTOMATION_ROOT" },
  input_recordings_dir: { env: "INPUT_RECORDINGS_DIR", editable: true },
  jobs_dir: { env: "JOBS_DIR", editable: true },
  logs_dir: { env: "LOGS_DIR", editable: true },
  ffmpeg_path: { env: "FFMPEG_PATH", editable: true },
  ffprobe_path: { env: "FFPROBE_PATH", editable: true },
  audiowaveform_path: { env: "AUDIOWAVEFORM_PATH", editable: true },
  whisper_bin: { env: "WHISPER_BIN", editable: true },
  faster_whisper: { env: "WHISPER_BACKEND", editable: true },
  funasr: { env: "WHISPER_BACKEND", editable: true },
  ctranslate2_cuda: { env: "FASTER_WHISPER_DEVICE", editable: true },
  h264_nvenc: { env: "RENDER_VIDEO_ENCODER", editable: true },
  demucs: { env: "DEMUCS_PATH", editable: true },
  cover_api_key: { env: "COVER_API_KEY", editable: true },
  llm_model: { env: "LLM_MODEL", editable: true },
  llm_openai_api_key: { env: "OPENAI_API_KEY", editable: true },
  llm_google_api_key: { env: "GOOGLE_API_KEY", editable: true },
  local_llm_model: { env: "LOCAL_LLM_MODEL_PATH", editable: true },
  local_llm_server: { env: "LOCAL_LLM_SERVER_PATH", editable: true },
  local_cover_model: { env: "LOCAL_COVER_MODEL_PATH", editable: true },
};
const HEALTH_CHECK_LABEL_KEYS = {
  root: "health.check.root",
  input_recordings_dir: "health.check.input_recordings_dir",
  jobs_dir: "health.check.jobs_dir",
  logs_dir: "health.check.logs_dir",
  ffmpeg_path: "health.check.ffmpeg",
  ffprobe_path: "health.check.ffprobe",
  audiowaveform_path: "health.check.audiowaveform",
  whisper_bin: "health.check.whisper",
  funasr: "health.check.funasr",
  torch: "health.check.torch",
  torch_cuda: "health.check.torch_cuda",
  faster_whisper: "health.check.faster_whisper",
  ctranslate2_cuda: "health.check.ctranslate2_cuda",
  h264_nvenc: "health.check.h264_nvenc",
  pillow: "health.check.pillow",
  cover_api_key: "health.check.cover_api_key",
  llm_model: "health.check.llm_model",
  llm_openai_api_key: "health.check.llm_api_key",
  llm_google_api_key: "health.check.llm_google_api_key",
  demucs: "health.check.demucs",
  local_llm_model: "health.check.local_llm_model",
  local_llm_server: "health.check.local_llm_server",
  local_cover_model: "health.check.local_cover_model",
  local_cover_transformers: "health.check.local_cover_transformers",
  local_cover_diffusers: "health.check.local_cover_diffusers",
  local_cover_accelerate: "health.check.local_cover_accelerate",
  local_cover_bitsandbytes: "health.check.local_cover_bitsandbytes",
};

export async function renderHealth(_match, { signal } = {}) {
  const app = document.getElementById("app");
  let events = null;
  let latestPayload = null;
  let disposed = false;
  let loadVersion = 0;
  const isActive = () => !disposed && !signal?.aborted;

  const loadingTimer = setTimeout(() => {
    if (isActive()) app.innerHTML = loadingState(t("common.loading"));
  }, 150);

  app.addEventListener("click", handleCopyAction);

  async function load() {
    const version = ++loadVersion;
    try {
      const payload = await API.getHealth({ signal });
      if (!isActive() || version !== loadVersion) return;
      latestPayload = payload;
      clearTimeout(loadingTimer);
      renderPayload(payload);
      bindRenderedActions();
      startEvents();
    } catch (error) {
      clearTimeout(loadingTimer);
      if (!isActive() || version !== loadVersion || isAbortError(error, signal)) return;
      app.innerHTML = errorState(`${t("common.error")} ${error.message}`, { retryLabel: t("common.retry") });
      app.querySelector("[data-retry]")?.addEventListener("click", load);
    }
  }

  function renderPayload(payload) {
    if (!isActive()) return;
    app.innerHTML = renderHealthPayloadForTest(payload);
  }

  function bindRenderedActions() {
    bindInstallButton();
    bindRecoveryButtons();
  }

  async function handleCopyAction(event) {
    const button = event.target?.closest?.("[data-copy-text]");
    if (!button || !app.contains(button)) return;
    const value = button.getAttribute("data-copy-text") || "";
    if (!value) return;
    if (!navigator.clipboard?.writeText) {
      showToast(`${t("health.copy_unavailable")} ${value}`, "info");
      return;
    }
    try {
      await navigator.clipboard.writeText(value);
      showToast(t(button.hasAttribute("data-install-command") ? "health.command_copied" : "health.path_copied"), "success");
    } catch {
      showToast(`${t("health.copy_unavailable")} ${value}`, "info");
    }
  }

  function bindInstallButton() {
    const buttons = document.querySelectorAll("#install-health-tools, #overview-install-tools");
    buttons.forEach((button) => {
      button.addEventListener("click", async () => {
        setButtonLoading(button, true, t("health.autofix_running"));
        try {
          const response = await API.installHealthTools({ install_ffmpeg: true });
          if (!isActive()) return;
          updateInstallState(response.tools_install || {});
          showToast(t("health.autofix_started"), "success");
        } catch (error) {
          if (!isActive()) return;
          showToast(`${t("health.autofix_failed")} ${error.message}`, "error");
          setButtonLoading(button, false);
        }
      });
    });
  }

  function bindRecoveryButtons() {
    const switchButtons = document.querySelectorAll("#switch-whisper-cli, #overview-switch-whisper-cli");
    switchButtons.forEach((switchButton) => {
      switchButton.addEventListener("click", async () => {
        setButtonLoading(switchButton, true, t("health.switching_backend"));
        try {
          const payload = await API.updateSettings({ env: { WHISPER_BACKEND: "cli" } });
          if (!isActive()) return;
          latestPayload = payload;
          renderPayload(payload);
          bindRenderedActions();
          showToast(t("health.switched_backend"), "success");
        } catch (error) {
          if (!isActive()) return;
          showToast(`${t("common.error")} ${error.message}`, "error");
          setButtonLoading(switchButton, false);
        }
      });
    });
    const coverSwitchButtons = document.querySelectorAll("#switch-cover-api, #overview-switch-cover-api");
    coverSwitchButtons.forEach((coverSwitchButton) => {
      coverSwitchButton.addEventListener("click", async () => {
        setButtonLoading(coverSwitchButton, true);
        try {
          const payload = await API.updateSettings({ env: { COVER_PROVIDER: "openai" } });
          if (!isActive()) return;
          latestPayload = payload;
          renderPayload(payload);
          bindRenderedActions();
          showToast(t("health.switched_cover_api"), "success");
        } catch (error) {
          if (!isActive()) return;
          showToast(`${t("common.error")} ${error.message}`, "error");
          setButtonLoading(coverSwitchButton, false);
        }
      });
    });
  }

  function startEvents() {
    if (events || !isActive()) return;
    events = API.openEvents();
    events.addEventListener("hello", (event) => {
      if (!isActive()) return;
      const payload = parseEventPayload(event);
      if (payload?.tools_install) updateInstallState(payload.tools_install);
    });
    events.addEventListener("tools_install", (event) => {
      if (!isActive()) return;
      updateInstallState(parseEventPayload(event));
    });
    events.addEventListener("health", (event) => {
      if (!isActive()) return;
      const payload = parseEventPayload(event);
      if (payload?.checks) {
        latestPayload = payload;
        renderPayload(payload);
        bindRenderedActions();
      }
    });
  }

  function updateInstallState(state) {
    if (!isActive() || !state || typeof state !== "object") return;
    if (latestPayload) latestPayload.tools_install = state;
    const target = document.getElementById("health-install-panel");
    if (latestPayload && target) {
      target.outerHTML = renderInstallPanel(latestPayload);
      bindRenderedActions();
    } else if (latestPayload && ["running", "done", "failed"].includes(String(state.status || ""))) {
      renderPayload(latestPayload);
      bindRenderedActions();
    }
    if (state.status === "done") {
      showToast(t("health.autofix_done"), "success");
      load();
    } else if (state.status === "failed") {
      showToast(t("health.autofix_failed"), "error");
    }
  }

  await load();
  return cleanupHealth;

  function cleanupHealth() {
    if (disposed) return;
    disposed = true;
    loadVersion += 1;
    clearTimeout(loadingTimer);
    app.removeEventListener("click", handleCopyAction);
    if (events) events.close();
  }
}

export function renderHealthPayloadForTest(payload = {}) {
  const checks = Array.isArray(payload.checks) ? payload.checks : [];
  const requiredChecks = checks.filter((check) => !isOptionalCheck(check));
  const requiredMissing = requiredChecks.filter((check) => !check.exists);
  const optionalMissing = checks.filter((check) => isOptionalCheck(check) && !check.exists);
  const warnings = Array.isArray(payload.warnings) ? payload.warnings : [];
  const canStart = requiredMissing.length === 0;
  const ready = canStart && warnings.length === 0;
  const readyCount = requiredChecks.length - requiredMissing.length;
  const title = ready
    ? t("health.overview_ready_title")
    : requiredMissing.length
      ? template(t("health.overview_missing_title"), { count: requiredMissing.length })
      : template(t("health.overview_warning_title"), { count: warnings.length });
  const note = ready
    ? t("health.overview_ready_note")
    : requiredMissing.length
      ? t("health.overview_missing_note")
      : t("health.overview_warning_note");
  const missingList = requiredMissing.length
    ? `
      <div class="health-missing-list">
        <strong>${t("health.missing_list_title")}</strong>
        <div>${requiredMissing.map((check) => `<span>${escapeHtml(healthCheckLabel(check.name))}</span>`).join("")}</div>
      </div>
    `
    : "";

  return `
    <section class="page-head health-page-head">
      <div>
        <h1 class="page-title">${t("health.title")}</h1>
        <p class="page-subtitle">${t("health.page_note")}</p>
      </div>
    </section>
    <section class="panel health-overview ${ready ? "ready" : "needs-attention"}">
      <div class="health-overview-icon" aria-hidden="true">${ready ? "✓" : "!"}</div>
      <div class="health-overview-copy">
        <span class="eyebrow">${ready ? t("health.ready") : requiredMissing.length ? t("health.missing") : t("health.attention")}</span>
        <h2>${escapeHtml(title)}</h2>
        <p>${escapeHtml(note)}</p>
        <div class="health-summary-badges">
          <span>${escapeHtml(template(t("health.core_ready"), { ready: readyCount, total: requiredChecks.length }))}</span>
          <span>${escapeHtml(template(t("health.optional_count"), { count: optionalMissing.length }))}</span>
        </div>
        ${missingList}
      </div>
      ${renderOverviewAction(payload, canStart)}
    </section>
    ${renderHealthWarnings(warnings)}
    ${renderStorageStatus(payload.storage || {})}
    ${renderInstallPanel(payload)}
    ${renderRecoveryPanel(payload)}
    ${renderHealthDetails(checks)}
  `;
}

function renderOverviewAction(payload, canStart) {
  if (canStart) {
    return `<a class="button primary health-start-action" href="#/new">${t("health.start_job")}</a>`;
  }
  const actions = [];
  const installableMissing = installableMissingChecks(payload);
  const state = payload.tools_install || {};
  const active = state.status === "running";
  if (installableMissing.length) {
    actions.push(`<button class="button primary" id="overview-install-tools" type="button" ${active ? "disabled" : ""}>${active ? t("health.autofix_running") : t("health.autofix_button")}</button>`);
  }
  const checks = Array.isArray(payload.checks) ? payload.checks : [];
  const missingTranscription = checks.some((check) => !check.exists && TRANSCRIPTION_CHECKS.has(check.name));
  const whisperCliReady = checks.some((check) => check.name === "whisper_bin" && check.exists);
  const backend = String(payload.settings?.whisper?.backend || "");
  if (missingTranscription) {
    if (whisperCliReady && backend !== "cli") {
      actions.push(`<button class="button primary" id="overview-switch-whisper-cli" type="button">${t("health.switch_to_cli")}</button>`);
    }
    actions.push(copyCommandButton(FASTER_WHISPER_INSTALL_COMMAND));
  }
  const missingCover = checks.some((check) => !check.exists && COVER_LOCAL_CHECKS.has(check.name));
  if (missingCover && String(payload.settings?.covers?.provider || "") === "local") {
    actions.push(copyCommandButton(LOCAL_COVER_INSTALL_COMMAND));
    const coverKeyReady = Boolean(payload.settings?.covers?.cover_api_key_configured || payload.settings?.covers?.openai_api_key_configured);
    actions.push(coverKeyReady
      ? `<button class="button" id="overview-switch-cover-api" type="button">${t("health.switch_to_api_cover")}</button>`
      : `<a class="button" href="#/settings">${t("health.configure_api_cover")}</a>`);
  }
  if (!actions.length) {
    actions.push(`<a class="button primary" href="#/settings">${t("health.fix_in_settings")}</a>`);
  }
  return `<div class="health-overview-actions">${actions.join("")}</div>`;
}

function copyCommandButton(command) {
  return `<button class="button" type="button" data-copy-text="${escapeHtml(command)}" data-install-command>${t("health.copy_install_command")}</button>`;
}

function renderHealthWarnings(warnings) {
  if (!warnings.length) return "";
  return `
    <section class="panel health-warning-panel">
      <div class="panel-head">
        <div>
          <h2>${t("health.warning_title")}</h2>
          <p>${t("health.warning_note")}</p>
        </div>
      </div>
      <div class="health-warning-list">
        ${warnings.map((warning) => {
          const code = String(warning?.code || "");
          const key = `health.warning.${code}`;
          const localized = t(key);
          const message = localized === key ? String(warning?.message || code) : localized;
          return `<div class="notice warning"><strong>${escapeHtml(message)}</strong></div>`;
        }).join("")}
      </div>
    </section>`;
}

function renderStorageStatus(storage) {
  if (!storage || storage.available !== true) return "";
  const free = formatBytes(storage.free_bytes);
  const total = formatBytes(storage.total_bytes);
  const reserve = formatBytes(storage.min_free_bytes);
  return `
    <section class="panel health-storage ${storage.low_space ? "needs-attention" : ""}">
      <div>
        <h2>${t("health.storage_title")}</h2>
        <p>${escapeHtml(template(t("health.storage_note"), { free, total, reserve }))}</p>
      </div>
      <code>${escapeHtml(storage.path || "")}</code>
    </section>`;
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const amount = bytes / (1024 ** index);
  return `${amount >= 10 || index === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[index]}`;
}

function renderInstallPanel(payload) {
  const installableMissing = installableMissingChecks(payload);
  const state = payload.tools_install || {};
  const active = state.status === "running";
  const terminal = state.status === "done" || state.status === "failed";
  if (!installableMissing.length && !active && !terminal) return "";
  const missingNames = installableMissing.map((check) => healthCheckLabel(check.name)).join(", ");
  const log = (state.log_tail || []).slice(-8).map((line) => `<div>${escapeHtml(line)}</div>`).join("");
  return `
    <section class="panel health-install-panel" id="health-install-panel">
      <div class="panel-head">
        <div>
          <h2>${t("health.autofix_title")}</h2>
          <p>${installableMissing.length ? `${t("health.autofix_note")} ${escapeHtml(missingNames)}` : t("health.autofix_ready_note")}</p>
        </div>
        <button class="button primary" id="install-health-tools" type="button" ${active ? "disabled" : ""}>
          ${active ? t("health.autofix_running") : t("health.autofix_button")}
        </button>
      </div>
      <div class="health-install-status ${state.status || "idle"}" id="health-install-status">
        <strong>${escapeHtml(statusLabel(state.status))}</strong>
        <span>${escapeHtml(state.message || "")}</span>
      </div>
      ${log ? `<div class="install-log" id="health-install-log">${log}</div>` : ""}
    </section>
  `;
}

function renderHealthDetails(checks) {
  return `
    <details class="panel health-details">
      <summary>
        <span>
          <strong>${t("health.details_title")}</strong>
          <small>${t("health.details_note")}</small>
        </span>
        <span class="badge optional">${checks.length}</span>
      </summary>
      <div class="health-table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th>${t("health.tool")}</th>
              <th>${t("common.path")}</th>
              <th>${t("common.status")}</th>
              <th>${t("common.version")}</th>
            </tr>
          </thead>
          <tbody>${checks.map((check) => {
            const config = HEALTH_CHECK_CONFIG[String(check.name || "")];
            const displayPath = String(check.path || "");
            const configuredPath = String(check.configured_path || "");
            const showConfiguredPath = configuredPath && configuredPath !== displayPath;
            const isLocalPath = displayPath && !displayPath.startsWith("python:") && !displayPath.startsWith("env:");
            return `
            <tr>
              <td>
                <div class="health-tool-cell">
                  <strong>${escapeHtml(healthCheckLabel(check.name))}</strong>
                  ${config?.env ? `<span class="health-env-tag" title="${t("health.config_var")}: ${escapeHtml(config.env)}">${escapeHtml(config.env)}</span>` : ""}
                </div>
              </td>
              <td>
                <div class="health-path-cell">
                  <code>${escapeHtml(displayPath)}</code>
                  ${showConfiguredPath ? `<small class="health-configured-path">${t("health.configured_value")}: <code>${escapeHtml(configuredPath)}</code></small>` : ""}
                  ${isLocalPath ? `
                    <button class="health-path-copy" type="button" data-copy-text="${escapeHtml(displayPath)}" title="${t("common.copy")}">
                      ${t("common.copy")}
                    </button>
                  ` : ""}
                  ${config?.editable ? `
                    <a class="health-path-edit-link" href="#/settings" title="${t("health.edit_in_settings")}">
                      ${t("common.edit")}
                    </a>
                  ` : ""}
                </div>
              </td>
              <td>${healthStatusBadge(check)}</td>
              <td>${escapeHtml(check.version || "")}</td>
            </tr>
          `;
          }).join("")}</tbody>
        </table>
      </div>
    </details>
  `;
}

function installableMissingChecks(payload) {
  return (payload.checks || []).filter((check) => {
    if (check.exists) return false;
    return INSTALLABLE_CHECKS.has(check.name);
  });
}

function renderRecoveryPanel(payload) {
  const checks = Array.isArray(payload.checks) ? payload.checks : [];
  const missingTranscription = checks.filter((check) => {
    if (check.exists) return false;
    return TRANSCRIPTION_CHECKS.has(check.name);
  });
  const missingCover = checks.filter((check) => {
    if (check.exists) return false;
    return COVER_LOCAL_CHECKS.has(check.name);
  });
  const coverProvider = String(payload.settings?.covers?.provider || "");

  let transcriptionHtml = "";
  if (missingTranscription.length) {
    const whisperCliReady = checks.some((check) => check.name === "whisper_bin" && check.exists);
    const backend = String(payload.settings?.whisper?.backend || "");
    const missingNames = missingTranscription.map((check) => healthCheckLabel(check.name)).join(", ");
    const cliAction = whisperCliReady && backend !== "cli"
      ? `<button class="button" id="switch-whisper-cli" type="button">${t("health.switch_to_cli")}</button>`
      : "";
    transcriptionHtml = `
      <section class="panel health-recovery-panel">
        <div class="panel-head">
          <div>
            <h2>${t("health.transcription_missing_title")}</h2>
            <p>${escapeHtml(template(t("health.transcription_missing_note"), { names: missingNames }))}</p>
          </div>
          <div class="health-recovery-actions">
            ${copyCommandButton(FASTER_WHISPER_INSTALL_COMMAND)}
            ${cliAction}
          </div>
        </div>
        <div class="notice">
          <strong>${t("health.transcription_recommended_title")}</strong>
          <span>${t("health.transcription_recommended_note")}</span>
        </div>
        <p class="muted">${t("health.transcription_install_note")}</p>
        <code class="health-command">${escapeHtml(FASTER_WHISPER_INSTALL_COMMAND)}</code>
      </section>
    `;
  }

  let coverHtml = "";
  if (missingCover.length && coverProvider === "local") {
    const missingCoverNames = missingCover.map((check) => healthCheckLabel(check.name)).join(", ");
    const coverKeyReady = Boolean(payload.settings?.covers?.cover_api_key_configured || payload.settings?.covers?.openai_api_key_configured);
    const coverAction = coverKeyReady
      ? `<button class="button" id="switch-cover-api" type="button">${t("health.switch_to_api_cover")}</button>`
      : `<a class="button" href="#/settings">${t("health.configure_api_cover")}</a>`;
    coverHtml = `
      <section class="panel health-recovery-panel">
        <div class="panel-head">
          <div>
            <h2>${t("health.cover_missing_title")}</h2>
            <p>${escapeHtml(template(t("health.cover_missing_note"), { names: missingCoverNames }))}</p>
          </div>
          <div class="health-recovery-actions">
            ${copyCommandButton(LOCAL_COVER_INSTALL_COMMAND)}
            ${coverAction}
          </div>
        </div>
        <div class="notice">
          <strong>${t("health.cover_recommended_title")}</strong>
          <span>${t("health.cover_recommended_note")}</span>
        </div>
        <p class="muted">${t("health.cover_install_note")}</p>
        <code class="health-command">${escapeHtml(LOCAL_COVER_INSTALL_COMMAND)}</code>
      </section>
    `;
  }

  return transcriptionHtml + coverHtml;
}

function isOptionalCheck(check) {
  return Boolean(check.optional || check.status === "optional_missing");
}

function healthCheckLabel(name) {
  const key = HEALTH_CHECK_LABEL_KEYS[String(name || "")];
  return key ? t(key) : String(name || "");
}

function template(value, replacements) {
  return Object.entries(replacements).reduce(
    (result, [key, replacement]) => result.replaceAll(`{${key}}`, String(replacement)),
    value,
  );
}

function statusLabel(status) {
  if (status === "running") return t("health.autofix_running");
  if (status === "done") return t("health.autofix_done");
  if (status === "failed") return t("health.autofix_failed");
  return t("health.autofix_idle");
}

function parseEventPayload(event) {
  try {
    return JSON.parse(event.data || "{}");
  } catch {
    return {};
  }
}

function healthStatusBadge(check) {
  if (check.exists) {
    return `<span class="badge accent">${t("common.ok")}</span>`;
  }
  if (check.optional || check.status === "optional_missing") {
    return `<span class="badge optional">${t("health.optional_missing")}</span>`;
  }
  return `<span class="badge failed">${t("common.missing")}</span>`;
}
