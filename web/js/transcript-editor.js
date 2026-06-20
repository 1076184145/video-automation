import { API } from "./api.js";
import { t } from "./i18n.js";
import { setButtonLoading, showToast } from "./toast.js";
import { escapeHtml, formatTime } from "./utils.js";
export function renderTranscript(transcript) {
  const segments = transcript?.segments || [];
  if (!segments.length) return `<div class="empty">${t("job.no_transcript")}</div>`;
  return `
    <div class="transcript-editor">
      <div class="clip-toolbar">
        <button class="button primary" id="save-transcript" type="button">${t("job.save_transcript")}</button>
        <div id="transcript-editor-message"></div>
      </div>
      <div class="scroll-list">${segments.map((segment, index) => `
        <div class="transcript-item editable-transcript" data-transcript-row>
          <button class="time transcript-seek" type="button" data-seek="${Number(segment.start || 0)}" title="${t("job.seek_to_time")}">${formatTime(segment.start)}</button>
          <textarea class="transcript-input" data-index="${index}" data-start="${Number(segment.start || 0)}" data-end="${Number(segment.end || segment.start || 0)}">${escapeHtml(segment.text || "")}</textarea>
        </div>
      `).join("")}</div>
    </div>
  `;
}

export function bindTranscriptEditor(root, jobName, reload, setEditing, seekPreview = () => {}) {
  const handler = async (e) => {
    const seekButton = e.target?.closest?.("[data-seek]");
    if (seekButton) {
      seekPreview(Number(seekButton.dataset.seek || 0));
      return;
    }
    if (e.target.id !== "save-transcript") return;
    const button = e.target;
    setButtonLoading(button, true, t("common.loading"));
    try {
      await API.updateTranscript(jobName, collectEditedTranscript(root));
      setTranscriptMessage(t("job.save_transcript_preview"));
      showToast(t("job.save_transcript_preview"), "success");
      await reload();
    } catch (error) {
      setTranscriptMessage(`${t("job.save_transcript_failed")}${escapeHtml(error.message)}`, true);
      showToast(`${t("job.save_transcript_failed")}${error.message}`, "error");
    } finally {
      setButtonLoading(button, false);
    }
  };
  const inputHandler = (e) => {
    if (e.target?.closest?.(".transcript-editor")) {
      setEditing(true);
    }
  };
  root.addEventListener("click", handler);
  root.addEventListener("input", inputHandler);
  return () => {
    root.removeEventListener("click", handler);
    root.removeEventListener("input", inputHandler);
  };
}

function collectEditedTranscript(root = document) {
  return Array.from(root.querySelectorAll("[data-transcript-row] .transcript-input")).map((input) => ({
    start: Number(input.dataset.start),
    end: Number(input.dataset.end),
    text: input.value.trim()
  }));
}

function setTranscriptMessage(message, isError = false) {
  const box = document.getElementById("transcript-editor-message");
  if (!box) return;
  box.innerHTML = `<div class="${isError ? "error" : "notice"}">${message}</div>`;
}
export const collectEditedTranscriptForTest = collectEditedTranscript;