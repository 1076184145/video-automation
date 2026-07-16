import { API } from "./api.js";
import { errorHintHtml } from "./error-hints.js";
import { localizedErrorMessage, t } from "./i18n.js";
import { setButtonLoading, showToast } from "./toast.js";
import { STAGES, escapeHtml, formatTime } from "./utils.js";
export function renderSourceWarning(corrupt) {
  const errorCount = Number(corrupt?.error_count || 0);
  if (!corrupt || corrupt.status === "skipped" || (corrupt.status === "ok" && errorCount < 1)) return "";
  const time = Number.isFinite(Number(corrupt.first_error_at_seconds))
    ? formatTime(Number(corrupt.first_error_at_seconds))
    : t("job.corrupt_unknown_time");
  const errors = Array.isArray(corrupt.errors) ? corrupt.errors.slice(0, 3) : [];
  const errorHtml = errors.length
    ? `
      <details class="source-warning-details">
        <summary>${t("job.corrupt_technical_details")}</summary>
        <ul>${errors.map((line) => `<li>${escapeHtml(line)}</li>`).join("")}</ul>
      </details>`
    : "";
  const title = corrupt.status === "corrupt"
    ? t("job.corrupt_title")
    : errorCount > 0
      ? t("job.corrupt_timestamp_title")
      : t("job.corrupt_scan_issue");
  const countHtml = errorCount > 0
    ? `<p>${t("job.corrupt_warning_count").replace("{count}", escapeHtml(String(errorCount)))}</p>`
    : "";
  return `
    <section class="notice warning source-warning">
      <div>
        <strong>${title}</strong>
        ${countHtml}
        <p>${t("job.corrupt_message").replace("{time}", escapeHtml(time))}</p>
        ${errorHtml}
      </div>
    </section>
  `;
}

export function renderJobActions() {
  return `
    <section class="panel job-actions">
      <div class="field compact">
        <label for="rerun-stage">${t("job.rerun_stage")}</label>
        <select id="rerun-stage">
          ${STAGES.map((stage) => `<option value="${stage}">${t(`stage.${stage}`)}</option>`).join("")}
        </select>
      </div>
      <button class="button" id="rerun-stage-button" type="button">${t("job.rerun")}</button>
      <button class="button danger" id="delete-job" type="button">${t("job.delete_job")}</button>
      <div id="job-action-message"></div>
    </section>
  `;
}

export function bindJobActions(root, jobName, reload) {
  const handler = async (e) => {
    const adviceButton = e.target?.closest?.("[data-error-action]");
    if (adviceButton) {
      await runErrorAdviceAction(adviceButton, jobName, reload);
      return;
    }
    if (e.target.id === "cancel-job") {
      const button = e.target;
      if (!window.confirm(t("job.cancel_job_confirm"))) return;
      setButtonLoading(button, true, t("common.loading"));
      try {
        await API.cancelJob(jobName);
        showToast(t("job.cancel_job_success"), "success");
        await reload();
      } catch (error) {
        setActionMessage(`${t("job.cancel_job_failed")} ${escapeHtml(error.message)}`, true);
        showToast(`${t("job.cancel_job_failed")} ${error.message}`, "error");
      } finally {
        setButtonLoading(button, false);
      }
    } else if (e.target.id === "rerun-stage-button") {
      const button = e.target;
      const stage = document.getElementById("rerun-stage")?.value;
      if (!stage || !window.confirm(`${t("job.rerun_confirm")} ${t(`stage.${stage}`)}`)) return;
      setButtonLoading(button, true, t("common.loading"));
      try {
        await API.rerunStage(jobName, stage);
        setActionMessage(t("job.rerun_started"));
        showToast(t("job.rerun_started"), "success");
        await reload();
      } catch (error) {
        setActionMessage(`${t("job.rerun_failed")}${escapeHtml(error.message)}`, true);
        showToast(`${t("job.rerun_failed")}${error.message}`, "error");
      } finally {
        setButtonLoading(button, false);
      }
    } else if (["delete-job", "delete-stale-job"].includes(e.target.id)) {
      if (!window.confirm(t("job.delete_job_confirm"))) return;
      try {
        await API.deleteJob(jobName);
        location.hash = "#/";
      } catch (error) {
        setActionMessage(`${t("common.delete")} failed: ${escapeHtml(error.message)}`, true);
      }
    }
  };
  root.addEventListener("click", handler);
  return () => root.removeEventListener("click", handler);
}

export function renderJobError(job) {
  const advice = job.error_advice;
  if (!advice || typeof advice !== "object") return `<div class="error">${errorHintHtml(job.error)}</div>`;
  const steps = Array.isArray(advice.next_steps) ? advice.next_steps : [];
  const actions = Array.isArray(advice.actions) ? advice.actions : [];
  const raw = advice.raw_error || job.error || "";
  return `
    <div class="error error-advice-card">
      <div class="error-advice-head">
        <strong>${escapeHtml(advice.title || t("error_hint.title"))}</strong>
        <span>${escapeHtml(advice.code || "error")}</span>
      </div>
      <p>${escapeHtml(advice.summary || t("error_hint.generic"))}</p>
      ${steps.length ? `
        <div class="error-advice-section">
          <b>${t("error_advice.next_steps")}</b>
          <ol>${steps.map((step) => `<li>${escapeHtml(step)}</li>`).join("")}</ol>
        </div>` : ""}
      ${actions.length ? `
        <div class="error-advice-actions">
          ${actions.map((action) => renderErrorAdviceAction(action)).join("")}
        </div>` : ""}
      ${raw ? `<details class="error-advice-raw"><summary>${t("error_advice.raw")}</summary><pre>${escapeHtml(raw)}</pre></details>` : ""}
    </div>`;
}

function renderErrorAdviceAction(action) {
  if (!action || typeof action !== "object") return "";
  const encoded = encodeURIComponent(JSON.stringify(action));
  return `<button class="button" type="button" data-error-action="${encoded}">${escapeHtml(action.label || t("common.retry"))}</button>`;
}

async function runErrorAdviceAction(button, jobName, reload) {
  let action = {};
  try {
    action = JSON.parse(decodeURIComponent(button.dataset.errorAction || "{}"));
  } catch {
    setActionMessage(t("error_advice.invalid_action"), true);
    return;
  }
  setButtonLoading(button, true, t("common.loading"));
  try {
    if (action.type === "settings_patch_and_rerun") {
      if (action.env && typeof action.env === "object") {
        await API.updateSettings({ env: action.env });
      }
      if (action.stage) {
        await API.rerunStage(jobName, action.stage);
      }
      setActionMessage(t("error_advice.settings_rerun_started"));
      showToast(t("error_advice.settings_rerun_started"), "success");
      await reload();
      return;
    }
    if (action.type === "rerun_stage" && action.stage) {
      await API.rerunStage(jobName, action.stage);
      setActionMessage(t("job.rerun_started"));
      showToast(t("job.rerun_started"), "success");
      await reload();
      return;
    }
    if (action.type === "skip_transcribe") {
      await API.rerunStage(jobName, action.stage || "detect_silence");
      setActionMessage(t("error_advice.skip_transcribe_started"));
      showToast(t("error_advice.skip_transcribe_started"), "success");
      await reload();
      return;
    }
    if (action.target) {
      location.hash = action.target;
      return;
    }
    setActionMessage(t("error_advice.unsupported_action"), true);
  } catch (error) {
    setActionMessage(`${t("error_advice.action_failed")} ${escapeHtml(error.message || String(error))}`, true);
    showToast(`${t("error_advice.action_failed")} ${error.message || String(error)}`, "error");
  } finally {
    setButtonLoading(button, false);
  }
}

export function bindDownloadActions(root) {
  const handler = async (event) => {
    const button = event.target?.closest?.("[data-copy-path]");
    if (!button) return;
    const original = button.textContent;
    button.disabled = true;
    try {
      await copyText(button.dataset.copyPath || "");
      button.textContent = "OK";
      setTimeout(() => {
        button.textContent = original || t("common.copy");
        button.disabled = false;
      }, 1500);
    } catch (error) {
      button.disabled = false;
      setActionMessage(`${t("common.copy_path_failed")} ${escapeHtml(error.message)}`, true);
    }
  };
  root.addEventListener("click", handler);
  return () => root.removeEventListener("click", handler);
}

async function copyText(text) {
  if (!text) return;
  if (navigator.clipboard?.writeText && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  try {
    document.execCommand("copy");
  } finally {
    textarea.remove();
  }
}

function setActionMessage(message, isError = false) {
  const box = document.getElementById("job-action-message");
  if (!box) return;
  box.innerHTML = `<div class="${isError ? "error" : "notice"}">${message}</div>`;
}

export function renderQualityGate(quality) {
  if (!quality || typeof quality !== "object") return "";
  const blocking = Array.isArray(quality.blocking) ? quality.blocking : [];
  const advisory = Array.isArray(quality.advisory) ? quality.advisory : [];
  if (!blocking.length && !advisory.length) {
    return `<div class="quality-gate passed" role="status"><strong>${t("quality.passed")}</strong></div>`;
  }
  const item = (entry) => {
    const context = [entry.expected && `${t("quality.expected")}: ${entry.expected}`, entry.actual && `${t("quality.actual")}: ${entry.actual}`]
      .filter(Boolean).join(" · ");
    const messageKey = entry.code ? `quality.check.${entry.code}` : "";
    const localizedMessage = messageKey ? t(messageKey) : "";
    const message = localizedMessage && localizedMessage !== messageKey
      ? localizedMessage
      : entry.message || entry.code || "";
    return `<li><strong>${escapeHtml(message)}</strong>${context ? `<span>${escapeHtml(context)}</span>` : ""}</li>`;
  };
  return `
    <div class="quality-gate ${blocking.length ? "blocked" : "advisory"}" ${blocking.length ? 'role="alert"' : 'role="status"'}>
      <strong>${blocking.length ? t("quality.blocked") : t("quality.advisory")}</strong>
      ${blocking.length ? `<ul>${blocking.map(item).join("")}</ul>` : ""}
      ${advisory.length ? `<p>${t("quality.advisory")}</p><ul>${advisory.map(item).join("")}</ul>` : ""}
    </div>`;
}

export function renderReviewActions(quality) {
  return `
    <section class="panel review-actions">
      <div>
        <h2>${t("status.review")}</h2>
        <p class="page-subtitle">${t("job.approve_note")}</p>
        ${renderQualityGate(quality)}
        <div id="approve-error"></div>
      </div>
      <button class="button primary" id="approve-job" type="button">${t("job.approve")}</button>
    </section>
  `;
}

export function bindReviewActions(root, jobName, reload) {
  const handler = async (e) => {
    if (e.target.id === "approve-job") {
      const button = e.target;
      setButtonLoading(button, true, t("common.loading"));
      try {
        await API.approveJob(jobName);
        showToast(t("job.approve"), "success");
        await reload();
      } catch (error) {
        const box = document.getElementById("approve-error");
        const message = localizedErrorMessage(error);
        if (box) box.innerHTML = `<div class="error">${t("job.approve_failed")}${escapeHtml(message)}</div>`;
        showToast(`${t("job.approve_failed")}${message}`, "error");
        setButtonLoading(button, false);
      }
    }
  };
  root.addEventListener("click", handler);
  return () => root.removeEventListener("click", handler);
}
