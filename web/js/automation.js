import { API } from "./api.js";
import { t } from "./i18n.js";
import { setButtonLoading, showToast } from "./toast.js";
import { escapeHtml, formatDate } from "./utils.js";

const BASE_RECIPE_STAGES = ["transcribe", "plan_cuts", "style_subtitles", "plan_render"];

export function legacyProfilesToRecipes(profiles = []) {
  return profiles
    .filter((profile) => profile && typeof profile.id === "string" && typeof profile.name === "string" && profile.payload)
    .map((profile) => {
      const options = { ...profile.payload };
      const stages = new Set(BASE_RECIPE_STAGES);
      for (const [flag, stage] of [
        ["source_integrity_scan", "detect_corruption"],
        ["detect_silence", "detect_silence"],
        ["detect_freeze", "detect_freeze"],
        ["detect_scenes", "detect_scenes"],
        ["plan_crop", "plan_crop"],
        ["plan_uvr", "plan_uvr"],
        ["render_review", "render_review"],
        ["render_final", "render_final"],
      ]) {
        if (options[flag]) stages.add(stage);
      }
      return {
        client_id: profile.id,
        name: profile.name,
        stages: [...stages],
        options,
        target_platforms: [],
      };
    });
}

export function renderQueuePanel(queue = {}) {
  const items = Array.isArray(queue.items)
    ? queue.items.filter((item) => !["completed", "canceled"].includes(item.status))
    : [];
  const globalAction = queue.paused ? "resume" : "pause";
  return `
    <section class="queue-panel" aria-labelledby="queue-title">
      <div class="section-heading">
        <div>
          <h2 id="queue-title">${t("queue.title")}</h2>
          <p>${t(queue.paused ? "queue.paused_note" : "queue.note")}</p>
        </div>
        <button class="button compact-button" type="button" data-queue-global="${globalAction}">${t(`queue.${globalAction}`)}</button>
      </div>
      ${items.length ? `
        <div class="queue-list">
          ${items.map((item, index) => renderQueueItem(item, index, items.length)).join("")}
        </div>` : `<div class="empty queue-empty">${t("queue.empty")}</div>`}
    </section>`;
}

function renderQueueItem(item, index, total) {
  const status = String(item.status || "pending");
  const canceling = status === "running" && Boolean(item.cancel_requested);
  return `
    <article class="queue-row" data-queue-id="${escapeHtml(item.id)}">
      <div class="queue-position">${index + 1}</div>
      <div class="queue-row-main">
        <strong>${escapeHtml(item.job_name || item.id)}</strong>
        <div class="queue-row-meta">
          <span class="badge ${queueStatusGroup(status)}">${t(canceling ? "queue.canceling" : `queue.status_${status}`)}</span>
          <span>${t("queue.priority")} ${Number(item.priority || 0)}</span>
          ${item.updated_at ? `<span>${escapeHtml(formatDate(item.updated_at))}</span>` : ""}
        </div>
        ${item.error ? `<p class="queue-error">${escapeHtml(item.error)}</p>` : ""}
      </div>
      <div class="queue-row-actions">
        <button class="button compact-button" type="button" data-queue-move="up" ${index === 0 ? "disabled" : ""} aria-label="${t("queue.move_up")}">↑</button>
        <button class="button compact-button" type="button" data-queue-move="down" ${index === total - 1 ? "disabled" : ""} aria-label="${t("queue.move_down")}">↓</button>
        ${status === "pending" ? `<button class="button compact-button" type="button" data-queue-action="pause">${t("queue.pause")}</button>` : ""}
        ${status === "paused" ? `<button class="button compact-button" type="button" data-queue-action="resume">${t("queue.resume")}</button>` : ""}
        ${status === "failed" ? `
          <select class="compact-select" data-retry-stage-for="${escapeHtml(item.id)}" aria-label="${t("queue.retry_stage")}">
            ${["probe", "extract_audio", "transcribe", "detect_silence", "plan_cuts", "style_subtitles", "render_final"].map((stage) => `<option value="${stage}">${t(`stage.${stage}`)}</option>`).join("")}
          </select>
          <button class="button compact-button" type="button" data-queue-action="retry-stage">${t("queue.retry")}</button>` : ""}
        ${canceling
          ? `<button class="button compact-button danger" type="button" disabled>${t("queue.canceling")}</button>`
          : (["pending", "paused", "running", "failed"].includes(status) ? `<button class="button compact-button danger" type="button" data-queue-action="cancel">${t("common.cancel")}</button>` : "")}
      </div>
    </article>`;
}

function queueStatusGroup(status) {
  if (status === "failed" || status === "canceled") return "failed";
  if (status === "completed") return "done";
  if (status === "running") return "processing";
  return "review";
}

export function bindQueuePanel(root, reload) {
  const handler = async (event) => {
    const globalButton = event.target?.closest?.("[data-queue-global]");
    const actionButton = event.target?.closest?.("[data-queue-action]");
    const moveButton = event.target?.closest?.("[data-queue-move]");
    const button = globalButton || actionButton || moveButton;
    if (!button) return;
    setButtonLoading(button, true, t("common.loading"));
    try {
      if (globalButton) {
        await (globalButton.dataset.queueGlobal === "pause" ? API.pauseQueue() : API.resumeQueue());
      } else {
        const row = button.closest("[data-queue-id]");
        const id = row?.dataset.queueId;
        if (!id) return;
        if (moveButton) {
          const rows = Array.from(root.querySelectorAll("[data-queue-id]"));
          const index = rows.indexOf(row);
          const target = moveButton.dataset.queueMove === "up" ? index - 1 : index + 1;
          if (target >= 0 && target < rows.length) {
            [rows[index], rows[target]] = [rows[target], rows[index]];
            await API.reorderQueue(rows.map((entry) => entry.dataset.queueId));
          }
        } else if (button.dataset.queueAction === "pause") {
          await API.pauseQueueItem(id);
        } else if (button.dataset.queueAction === "resume") {
          await API.resumeQueueItem(id);
        } else if (button.dataset.queueAction === "cancel") {
          await API.cancelQueueItem(id);
        } else if (button.dataset.queueAction === "retry-stage") {
          const stage = row.querySelector(`[data-retry-stage-for="${CSS.escape(id)}"]`)?.value || "transcribe";
          await API.retryQueueStage(id, stage);
        }
      }
      await reload();
    } catch (error) {
      showToast(`${t("queue.action_failed")} ${error.message}`, "error");
    } finally {
      setButtonLoading(button, false);
    }
  };
  root.addEventListener("click", handler);
  return () => root.removeEventListener("click", handler);
}
