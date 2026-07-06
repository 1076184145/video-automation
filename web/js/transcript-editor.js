import { API } from "./api.js";
import { t } from "./i18n.js";
import { clearReviewDraft, createDraftSaver } from "./review-drafts.js";
import { setButtonLoading, showToast } from "./toast.js";
import { escapeHtml, formatTime } from "./utils.js";
export const TRANSCRIPT_PAGE_SIZE = 200;
const transcriptStates = new WeakMap();

export function renderTranscript(transcript) {
  const segments = transcript?.segments || [];
  if (!segments.length) return `<div class="empty">${t("job.no_transcript")}</div>`;
  const backend = transcriptBackendLabel(transcript);
  return `
    <div class="transcript-editor">
      ${backend ? `<div class="transcript-backend"><strong>${t("job.transcript_backend")}</strong> ${backend}</div>` : ""}
      <div class="clip-toolbar">
        <button class="button primary" id="save-transcript" type="button">${t("job.save_transcript")}</button>
        ${segments.length > TRANSCRIPT_PAGE_SIZE ? `
          <div class="transcript-pagination" aria-label="${t("transcript.pagination")}">
            <button class="button compact-button" type="button" data-transcript-page="previous" disabled>${t("common.previous")}</button>
            <span data-transcript-page-label>1 / ${Math.ceil(segments.length / TRANSCRIPT_PAGE_SIZE)}</span>
            <button class="button compact-button" type="button" data-transcript-page="next">${t("common.next")}</button>
          </div>` : ""}
        <div id="transcript-editor-message"></div>
      </div>
      <div class="scroll-list" data-transcript-list>${renderTranscriptRows(segments, 0)}</div>
      <script type="application/json" data-transcript-data>${safeJsonForHtml(segments)}</script>
    </div>
  `;
}

function renderTranscriptRows(segments, page) {
  const start = page * TRANSCRIPT_PAGE_SIZE;
  return segments.slice(start, start + TRANSCRIPT_PAGE_SIZE).map((segment, offset) => {
    const index = start + offset;
    return `
        <div class="transcript-item editable-transcript" data-transcript-row>
          <button class="time transcript-seek" type="button" data-seek="${Number(segment.start || 0)}" title="${t("job.seek_to_time")}">${formatTime(segment.start)}</button>
          <textarea class="transcript-input" data-index="${index}" data-start="${Number(segment.start || 0)}" data-end="${Number(segment.end || segment.start || 0)}">${escapeHtml(segment.text || "")}</textarea>
        </div>
      `;
  }).join("");
}

function safeJsonForHtml(value) {
  return JSON.stringify(value).replace(/</g, "\\u003c");
}

function transcriptBackendLabel(transcript) {
  const rawBackend = String(transcript?.backend || "").trim();
  if (!rawBackend) return "";
  const backend = rawBackend.toLowerCase().startsWith("funasr")
    ? "FunASR"
    : rawBackend.toLowerCase().includes("whisper")
      ? "Faster-Whisper"
      : rawBackend;
  const details = [transcript?.model, transcript?.device]
    .filter(Boolean)
    .map((value) => escapeHtml(String(value)));
  return `<span class="badge optional">${escapeHtml(backend)}</span>${details.length ? ` <span class="muted">${details.join(" · ")}</span>` : ""}`;
}

export function bindTranscriptEditor(root, jobName, reload, setEditing, seekPreview = () => {}) {
  initializeTranscriptState(root);
  const draftSaver = createDraftSaver(jobName, "transcript", () => collectEditedTranscript(root));
  const handler = async (e) => {
    const seekButton = e.target?.closest?.("[data-seek]");
    if (seekButton) {
      seekPreview(Number(seekButton.dataset.seek || 0));
      return;
    }
    const pageButton = e.target?.closest?.("[data-transcript-page]");
    if (pageButton) {
      changeTranscriptPage(root, pageButton.dataset.transcriptPage === "next" ? 1 : -1);
      return;
    }
    if (e.target.id !== "save-transcript") return;
    const button = e.target;
    setButtonLoading(button, true, t("common.loading"));
    try {
      await API.updateTranscript(jobName, collectEditedTranscript(root));
      draftSaver.cancel();
      clearReviewDraft(jobName, "transcript");
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
      draftSaver.schedule();
    }
  };
  root.addEventListener("click", handler);
  root.addEventListener("input", inputHandler);
  return () => {
    root.removeEventListener("click", handler);
    root.removeEventListener("input", inputHandler);
    draftSaver.dispose();
  };
}

function collectEditedTranscript(root = document) {
  const state = transcriptStates.get(root);
  if (state) {
    syncVisibleTranscriptRows(root, state);
    return state.segments.map((segment) => ({
      start: Number(segment.start),
      end: Number(segment.end),
      text: String(segment.text || "").trim(),
    }));
  }
  return Array.from(root.querySelectorAll("[data-transcript-row] .transcript-input")).map((input) => ({
    start: Number(input.dataset.start),
    end: Number(input.dataset.end),
    text: input.value.trim()
  }));
}

function initializeTranscriptState(root) {
  const data = root.querySelector?.("[data-transcript-data]");
  if (!data) return null;
  try {
    const segments = JSON.parse(data.textContent || "[]");
    const state = { segments: Array.isArray(segments) ? segments : [], page: 0 };
    transcriptStates.set(root, state);
    return state;
  } catch {
    return null;
  }
}

function syncVisibleTranscriptRows(root, state) {
  for (const input of root.querySelectorAll("[data-transcript-row] .transcript-input")) {
    const index = Number(input.dataset.index);
    if (!Number.isInteger(index) || !state.segments[index]) continue;
    state.segments[index] = {
      ...state.segments[index],
      start: Number(input.dataset.start),
      end: Number(input.dataset.end),
      text: input.value.trim(),
    };
  }
}

function changeTranscriptPage(root, delta) {
  const state = transcriptStates.get(root) || initializeTranscriptState(root);
  if (!state) return;
  syncVisibleTranscriptRows(root, state);
  const pageCount = Math.max(1, Math.ceil(state.segments.length / TRANSCRIPT_PAGE_SIZE));
  state.page = Math.max(0, Math.min(pageCount - 1, state.page + delta));
  const list = root.querySelector("[data-transcript-list]");
  if (list) list.innerHTML = renderTranscriptRows(state.segments, state.page);
  const label = root.querySelector("[data-transcript-page-label]");
  if (label) label.textContent = `${state.page + 1} / ${pageCount}`;
  const previous = root.querySelector('[data-transcript-page="previous"]');
  const next = root.querySelector('[data-transcript-page="next"]');
  if (previous) previous.disabled = state.page === 0;
  if (next) next.disabled = state.page >= pageCount - 1;
}

function setTranscriptMessage(message, isError = false) {
  const box = document.getElementById("transcript-editor-message");
  if (!box) return;
  box.innerHTML = `<div class="${isError ? "error" : "notice"}">${message}</div>`;
}
export const collectEditedTranscriptForTest = collectEditedTranscript;
