import { API } from "./api.js";
import { t } from "./i18n.js";
import { setButtonLoading, showToast } from "./toast.js";
import { escapeHtml } from "./utils.js";

const INSTALLABLE_CHECKS = new Set(["ffmpeg_path", "ffprobe_path"]);
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

export async function renderHealth() {
  const app = document.getElementById("app");
  let events = null;
  let latestPayload = null;

  const loadingTimer = setTimeout(() => {
    app.innerHTML = `<div class="loading">${t("common.loading")}</div>`;
  }, 150);

  async function load() {
    try {
      const payload = await API.getHealth();
      latestPayload = payload;
      clearTimeout(loadingTimer);
      renderPayload(payload);
      bindInstallButton();
      startEvents();
    } catch (error) {
      clearTimeout(loadingTimer);
      app.innerHTML = `<div class="error">${t("common.error")} ${escapeHtml(error.message)} <button class="button" id="retry-health">${t("common.retry")}</button></div>`;
      document.getElementById("retry-health")?.addEventListener("click", load);
    }
  }

  function renderPayload(payload) {
    app.innerHTML = renderHealthPayloadForTest(payload);
  }

  function bindInstallButton() {
    const button = document.getElementById("install-health-tools");
    if (!button) return;
    button.addEventListener("click", async () => {
      setButtonLoading(button, true, t("health.autofix_running"));
      try {
        const response = await API.installHealthTools({ install_ffmpeg: true });
        updateInstallState(response.tools_install || {});
        showToast(t("health.autofix_started"), "success");
      } catch (error) {
        showToast(`${t("health.autofix_failed")} ${error.message}`, "error");
        setButtonLoading(button, false);
      }
    });
  }

  function startEvents() {
    if (events) return;
    events = API.openEvents();
    events.addEventListener("hello", (event) => {
      const payload = parseEventPayload(event);
      if (payload?.tools_install) updateInstallState(payload.tools_install);
    });
    events.addEventListener("tools_install", (event) => {
      updateInstallState(parseEventPayload(event));
    });
    events.addEventListener("health", (event) => {
      const payload = parseEventPayload(event);
      if (payload?.checks) {
        latestPayload = payload;
        renderPayload(payload);
        bindInstallButton();
      }
    });
  }

  function updateInstallState(state) {
    if (!state || typeof state !== "object") return;
    if (latestPayload) latestPayload.tools_install = state;
    const target = document.getElementById("health-install-panel");
    if (latestPayload && target) {
      target.outerHTML = renderInstallPanel(latestPayload);
      bindInstallButton();
    } else if (latestPayload && ["running", "done", "failed"].includes(String(state.status || ""))) {
      renderPayload(latestPayload);
      bindInstallButton();
    }
    if (state.status === "done") {
      showToast(t("health.autofix_done"), "success");
      load();
    } else if (state.status === "failed") {
      showToast(t("health.autofix_failed"), "error");
    }
  }

  await load();
  return () => {
    if (events) events.close();
  };
}

export function renderHealthPayloadForTest(payload = {}) {
  const checks = Array.isArray(payload.checks) ? payload.checks : [];
  const requiredChecks = checks.filter((check) => !isOptionalCheck(check));
  const requiredMissing = requiredChecks.filter((check) => !check.exists);
  const optionalMissing = checks.filter((check) => isOptionalCheck(check) && !check.exists);
  const ready = requiredMissing.length === 0;
  const readyCount = requiredChecks.length - requiredMissing.length;
  const title = ready
    ? t("health.overview_ready_title")
    : template(t("health.overview_missing_title"), { count: requiredMissing.length });
  const note = ready ? t("health.overview_ready_note") : t("health.overview_missing_note");
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
        <span class="eyebrow">${ready ? t("health.ready") : t("health.missing")}</span>
        <h2>${escapeHtml(title)}</h2>
        <p>${escapeHtml(note)}</p>
        <div class="health-summary-badges">
          <span>${escapeHtml(template(t("health.core_ready"), { ready: readyCount, total: requiredChecks.length }))}</span>
          <span>${escapeHtml(template(t("health.optional_count"), { count: optionalMissing.length }))}</span>
        </div>
        ${missingList}
      </div>
      ${ready ? `<a class="button primary health-start-action" href="#/new">${t("health.start_job")}</a>` : ""}
    </section>
    ${renderInstallPanel(payload)}
    ${renderHealthDetails(checks)}
  `;
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
