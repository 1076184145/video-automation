import { API, isAbortError } from "./api.js";
import { t } from "./i18n.js";
import { setButtonLoading, showToast } from "./toast.js";
import { errorState, loadingState } from "./ui-states.js";
import { escapeHtml } from "./utils.js";

const INSTALLABLE_CHECKS = new Set(["ffmpeg_path", "ffprobe_path"]);
const TRANSCRIPTION_CHECKS = new Set(["faster_whisper", "ctranslate2_cuda"]);
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
  demucs: "health.check.demucs",
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

  async function load() {
    const version = ++loadVersion;
    try {
      const payload = await API.getHealth({ signal });
      if (!isActive() || version !== loadVersion) return;
      latestPayload = payload;
      clearTimeout(loadingTimer);
      renderPayload(payload);
      bindInstallButton();
      bindRecoveryButtons();
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

  function bindInstallButton() {
    const button = document.getElementById("install-health-tools");
    if (!button) return;
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
  }

  function bindRecoveryButtons() {
    const switchButton = document.getElementById("switch-whisper-cli");
    if (switchButton) {
      switchButton.addEventListener("click", async () => {
        setButtonLoading(switchButton, true, t("health.switching_backend"));
        try {
          const payload = await API.updateSettings({ env: { WHISPER_BACKEND: "cli" } });
          if (!isActive()) return;
          latestPayload = payload;
          renderPayload(payload);
          bindInstallButton();
          bindRecoveryButtons();
          showToast(t("health.switched_backend"), "success");
        } catch (error) {
          if (!isActive()) return;
          showToast(`${t("common.error")} ${error.message}`, "error");
          setButtonLoading(switchButton, false);
        }
      });
    }
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
        bindInstallButton();
        bindRecoveryButtons();
      }
    });
  }

  function updateInstallState(state) {
    if (!isActive() || !state || typeof state !== "object") return;
    if (latestPayload) latestPayload.tools_install = state;
    const target = document.getElementById("health-install-panel");
    if (latestPayload && target) {
      target.outerHTML = renderInstallPanel(latestPayload);
      bindInstallButton();
      bindRecoveryButtons();
    } else if (latestPayload && ["running", "done", "failed"].includes(String(state.status || ""))) {
      renderPayload(latestPayload);
      bindInstallButton();
      bindRecoveryButtons();
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
      ${canStart ? `<a class="button primary health-start-action" href="#/new">${t("health.start_job")}</a>` : ""}
    </section>
    ${renderHealthWarnings(warnings)}
    ${renderStorageStatus(payload.storage || {})}
    ${renderInstallPanel(payload)}
    ${renderRecoveryPanel(payload)}
    ${renderHealthDetails(checks)}
  `;
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
          <thead><tr><th>${t("health.tool")}</th><th>${t("common.path")}</th><th>${t("common.status")}</th><th>${t("common.version")}</th></tr></thead>
          <tbody>${checks.map((check) => `
            <tr>
              <td>${escapeHtml(healthCheckLabel(check.name))}</td>
              <td><code>${escapeHtml(check.path)}</code></td>
              <td>${healthStatusBadge(check)}</td>
              <td>${escapeHtml(check.version || "")}</td>
            </tr>
          `).join("")}</tbody>
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
  if (!missingTranscription.length) return "";
  const whisperCliReady = checks.some((check) => check.name === "whisper_bin" && check.exists);
  const backend = String(payload.settings?.whisper?.backend || "");
  const missingNames = missingTranscription.map((check) => healthCheckLabel(check.name)).join(", ");
  const installCommand = "python -m pip install -r requirements-transcription-faster.txt";
  const cliAction = whisperCliReady && backend !== "cli"
    ? `<button class="button" id="switch-whisper-cli" type="button">${t("health.switch_to_cli")}</button>`
    : "";
  return `
    <section class="panel health-recovery-panel">
      <div class="panel-head">
        <div>
          <h2>${t("health.transcription_missing_title")}</h2>
          <p>${escapeHtml(template(t("health.transcription_missing_note"), { names: missingNames }))}</p>
        </div>
        ${cliAction}
      </div>
      <div class="notice">
        <strong>${t("health.transcription_recommended_title")}</strong>
        <span>${t("health.transcription_recommended_note")}</span>
      </div>
      <p class="muted">${t("health.transcription_install_note")}</p>
      <code class="health-command">${escapeHtml(installCommand)}</code>
    </section>
  `;
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
