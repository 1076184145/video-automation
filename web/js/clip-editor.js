import { API } from "./api.js";
import { formatClipTimeInput, parseClipTime } from "./clip-time.js";
import { t } from "./i18n.js";
import { clearReviewDraft, createDraftSaver } from "./review-drafts.js";
import { setButtonLoading, showToast } from "./toast.js";
import { escapeHtml, formatTime } from "./utils.js";
export function renderClips(cuts, feedback) {
  const clips = cuts?.clips || [];
  if (!clips.length) return `<div class="empty">${t("job.no_clips")}</div>`;
  const feedbackByClip = clipFeedbackMap(feedback);
  return `
    <div class="clip-toolbar">
      <button class="button" id="add-clip" type="button">${t("job.add_clip")}</button>
      <button class="button compact-button" id="undo-clips" type="button" disabled title="${t("job.undo_hint")}">${t("job.undo")}</button>
      <button class="button compact-button" id="redo-clips" type="button" disabled title="${t("job.redo_hint")}">${t("job.redo")}</button>
      <button class="button compact-button" id="clear-clip-selection" type="button">${t("job.batch_clear_selection")}</button>
      <button class="button compact-button" id="batch-keep-clips" type="button">${t("job.batch_keep")}</button>
      <button class="button compact-button" id="batch-drop-clips" type="button">${t("job.batch_drop")}</button>
      <button class="button compact-button danger" id="batch-delete-clips" type="button">${t("job.batch_delete")}</button>
      <button class="button primary" id="save-cuts" type="button">${t("job.save_cuts")}</button>
      <div id="clip-editor-message"></div>
    </div>
    <div class="clip-editor-wrapper">
    <table class="table clip-editor"><thead><tr><th><input type="checkbox" id="select-all-clips" aria-label="${t("job.select_all_clips")}" /></th><th>#</th><th>${t("job.keep")}</th><th>${t("job.start")}</th><th>${t("job.end")}</th><th>${t("common.duration")}</th><th>${t("job.score")}</th><th>${t("job.scenes")}</th><th>${t("job.reason")}</th><th>${t("job.content")}</th><th>${t("job.actions")}</th></tr></thead><tbody id="clip-editor-body">
    ${clips.map((clip, index) => renderClipRow(clip, index, feedbackByClip.get(clipKey(clip)))).join("")}
  </tbody></table></div>`;
}

export function renderClipRow(clip, index, feedback) {
  const score = clip.final_score ?? clip.content_score ?? "-";
  const semanticReasons = Array.isArray(clip.semantic_reasons) ? clip.semantic_reasons.filter(Boolean).join(" / ") : "";
  const scoreTitle = semanticReasons ? `${t("job.semantic_reason")}: ${semanticReasons}` : t("job.score");
  const key = clipKey(clip);
  const feedbackAction = feedback?.action || "";
  return `<tr data-clip-row data-clip-key="${escapeHtml(key)}" data-feedback-action="${escapeHtml(feedbackAction)}" draggable="true">
    <td><input type="checkbox" class="clip-select" data-clip-select aria-label="${t("job.select_clip")}" /></td>
    <td>${index + 1}</td>
    <td><label class="check" style="padding:4px;border:none;background:transparent;box-shadow:none;"><input type="checkbox" data-field="keep" ${clip.keep === false ? "" : "checked"} /></label></td>
    <td><input class="time-input" type="text" inputmode="text" data-field="start" value="${formatClipTimeInput(clip.start || 0)}" title="${t("job.time_format_hint")}" /></td>
    <td><input class="time-input" type="text" inputmode="text" data-field="end" value="${formatClipTimeInput(clip.end || 0)}" title="${t("job.time_format_hint")}" /></td>
    <td>${formatTime(clip.duration)}</td>
    <td><span class="badge optional" title="${escapeHtml(scoreTitle)}">${escapeHtml(String(score))}</span></td>
    <td>${clip.scene_count || 0}</td>
    <td><input class="reason-input" type="text" data-field="reason" value="${escapeHtml(clip.reason || "manual edit")}" /></td>
    <td><textarea class="content-input" data-field="content" data-original="${escapeHtml(clip.transcript_text || "")}" data-subtitle-override="${clip.subtitle_override ? "1" : ""}">${escapeHtml(clip.subtitle_text || clip.transcript_text || "")}</textarea></td>
    <td class="clip-actions">
      <button class="button compact-button" type="button" data-seek-clip="${Number(clip.start || 0)}" title="${t("job.play_clip")}">▶</button>
      <button class="button compact-button feedback-button ${feedbackAction === "accepted" ? "active accepted" : ""}" type="button" data-clip-feedback="accepted" title="${t("job.feedback_accept")}">${t("job.feedback_accept_short")}</button>
      <button class="button compact-button feedback-button ${feedbackAction === "rejected" ? "active rejected" : ""}" type="button" data-clip-feedback="rejected" title="${t("job.feedback_reject")}">${t("job.feedback_reject_short")}</button>
      <button class="button compact-button danger" type="button" data-remove-clip>${t("common.delete")}</button>
    </td>
  </tr>`;
}

function clipKey(clip) {
  return `${Number(clip?.start || 0).toFixed(3)}-${Number(clip?.end || 0).toFixed(3)}`;
}

function clipFeedbackMap(feedback) {
  const map = new Map();
  const items = Array.isArray(feedback?.items) ? feedback.items : [];
  items.forEach((item) => {
    if (item?.clip_key) map.set(String(item.clip_key), item);
  });
  return map;
}

export function bindClipEditor(root, jobName, reload, setEditing, seekPreview = () => {}) {
  let draggedRow = null;
  let lastSelectedIndex = -1;
  let inputSnapshot = null;
  const draftSaver = createDraftSaver(jobName, "cuts", () => collectEditedClips(root));
  const history = createClipHistory(root, () => {
    setEditing(true);
    draftSaver.schedule();
    setClipMessage(t("job.undo_redo_changed"));
  });
  const handler = async (e) => {
    const selectBox = e.target?.closest?.("[data-clip-select]");
    if (selectBox) {
      handleClipSelection(root, selectBox, e.shiftKey, lastSelectedIndex);
      lastSelectedIndex = clipRowIndex(selectBox.closest("[data-clip-row]"));
      return;
    }
    if (e.target?.id === "select-all-clips") {
      setClipSelection(root, e.target.checked);
      lastSelectedIndex = -1;
      return;
    }
    if (e.target.id === "undo-clips") {
      history.undo();
      return;
    }
    if (e.target.id === "redo-clips") {
      history.redo();
      return;
    }
    const feedbackButton = e.target?.closest?.("[data-clip-feedback]");
    if (feedbackButton) {
      await saveClipFeedback(jobName, feedbackButton);
      return;
    }
    const seekButton = e.target?.closest?.("[data-seek-clip]");
    if (seekButton) {
      seekPreview(Number(seekButton.dataset.seekClip || 0));
      return;
    }
    if (e.target.id === "add-clip") {
      const body = document.getElementById("clip-editor-body");
      if (!body) return;
      history.push();
      setEditing(true);
      const rows = Array.from(body.querySelectorAll("[data-clip-row]"));
      const lastEnd = parseClipTime(rows.at(-1)?.querySelector('[data-field="end"]')?.value || "0");
      body.insertAdjacentHTML("beforeend", renderClipRow({ start: lastEnd, end: lastEnd + 5, duration: 5, reason: "manual edit" }, rows.length));
      draftSaver.schedule();
    } else if (e.target.id === "clear-clip-selection") {
      setClipSelection(root, false);
      lastSelectedIndex = -1;
    } else if (e.target.id === "batch-keep-clips" || e.target.id === "batch-drop-clips") {
      const rows = selectedClipRows(root);
      if (!rows.length) return showToast(t("job.no_selected_clips"), "warning");
      const keep = e.target.id === "batch-keep-clips";
      history.push();
      rows.forEach((row) => {
        const checkbox = row.querySelector('[data-field="keep"]');
        if (checkbox) checkbox.checked = keep;
      });
      setEditing(true);
      draftSaver.schedule();
      setClipMessage(t(keep ? "job.batch_keep_done" : "job.batch_drop_done"));
    } else if (e.target.id === "batch-delete-clips") {
      const rows = selectedClipRows(root);
      if (!rows.length) return showToast(t("job.no_selected_clips"), "warning");
      if (!window.confirm(t("job.batch_delete_confirm").replace("{count}", String(rows.length)))) return;
      history.push();
      rows.forEach((row) => row.remove());
      refreshClipRowNumbers(root);
      setEditing(true);
      draftSaver.schedule();
      setClipMessage(t("job.batch_delete_done").replace("{count}", String(rows.length)));
    } else if (e.target.id === "save-cuts") {
      const button = e.target;
      setButtonLoading(button, true, t("common.loading"));
      try {
        const clips = collectEditedClips(root);
        await API.updateCuts(jobName, clips);
        draftSaver.cancel();
        clearReviewDraft(jobName, "cuts");
        await API.rerunStage(jobName, "render_review");
        setClipMessage(t("job.save_cuts_preview"));
        showToast(t("job.save_cuts_preview"), "success");
        await reload();
      } catch (error) {
        setClipMessage(`${t("job.save_cuts_failed")}${escapeHtml(error.message)}`, true);
        showToast(`${t("job.save_cuts_failed")}${error.message}`, "error");
      } finally {
        setButtonLoading(button, false);
      }
    } else if (e.target.closest("[data-remove-clip]")) {
      history.push();
      setEditing(true);
      e.target.closest("[data-clip-row]")?.remove();
      refreshClipRowNumbers(root);
      draftSaver.schedule();
    }
  };
  const dragStart = (event) => {
    const row = event.target?.closest?.("[data-clip-row]");
    if (!row || event.target?.closest?.("input, textarea, button, select, label")) {
      event.preventDefault();
      return;
    }
    history.push();
    draggedRow = row;
    row.classList.add("dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", "");
  };
  const dragOver = (event) => {
    if (!draggedRow) return;
    const row = event.target?.closest?.("[data-clip-row]");
    if (!row || row === draggedRow) return;
    event.preventDefault();
    const rect = row.getBoundingClientRect();
    const before = event.clientY < rect.top + rect.height / 2;
    row.parentNode.insertBefore(draggedRow, before ? row : row.nextSibling);
  };
  const dragEnd = () => {
    if (!draggedRow) return;
    draggedRow.classList.remove("dragging");
    draggedRow = null;
    refreshClipRowNumbers(root);
    setEditing(true);
    draftSaver.schedule();
    setClipMessage(t("job.clips_reordered"));
  };
  const drop = (event) => event.preventDefault();
  const beforeMutate = () => history.push();
  const historyCommand = (event) => {
    if (event.detail?.direction === "redo") history.redo();
    else history.undo();
  };
  const pointerDown = (event) => {
    const target = event.target?.closest?.('[data-field="keep"]');
    if (target) history.push();
  };
  const focusIn = (event) => {
    const target = event.target?.closest?.("[data-field]");
    if (!target || !target.closest("[data-clip-row]")) return;
    inputSnapshot = history.snapshot();
    target.dataset.historyCaptured = "0";
  };
  const inputHandler = (event) => {
    const target = event.target?.closest?.("[data-field]");
    if (!target || !target.closest("[data-clip-row]")) return;
    if (target.dataset.historyCaptured !== "1") {
      history.pushSnapshot(inputSnapshot);
      target.dataset.historyCaptured = "1";
    }
    setEditing(true);
    draftSaver.schedule();
  };
  const focusOut = (event) => {
    const target = event.target?.closest?.("[data-field]");
    if (!target) return;
    delete target.dataset.historyCaptured;
    inputSnapshot = null;
  };
  root.addEventListener("click", handler);
  root.addEventListener("clip-editor:before-mutate", beforeMutate);
  root.addEventListener("clip-editor:history", historyCommand);
  root.addEventListener("pointerdown", pointerDown);
  root.addEventListener("focusin", focusIn);
  root.addEventListener("input", inputHandler);
  root.addEventListener("focusout", focusOut);
  root.addEventListener("dragstart", dragStart);
  root.addEventListener("dragover", dragOver);
  root.addEventListener("drop", drop);
  root.addEventListener("dragend", dragEnd);
  return () => {
    root.removeEventListener("click", handler);
    root.removeEventListener("clip-editor:before-mutate", beforeMutate);
    root.removeEventListener("clip-editor:history", historyCommand);
    root.removeEventListener("pointerdown", pointerDown);
    root.removeEventListener("focusin", focusIn);
    root.removeEventListener("input", inputHandler);
    root.removeEventListener("focusout", focusOut);
    root.removeEventListener("dragstart", dragStart);
    root.removeEventListener("dragover", dragOver);
    root.removeEventListener("drop", drop);
    root.removeEventListener("dragend", dragEnd);
    draftSaver.dispose();
  };
}

async function saveClipFeedback(jobName, button) {
  const row = button.closest("[data-clip-row]");
  if (!row) return;
  const requested = button.dataset.clipFeedback || "";
  const current = row.dataset.feedbackAction || "";
  const action = current === requested ? "clear" : requested;
  const payload = {
    clip_key: row.dataset.clipKey || "",
    action,
    index: clipRowIndex(row),
    start: parseClipTime(row.querySelector('[data-field="start"]')?.value || "0"),
    end: parseClipTime(row.querySelector('[data-field="end"]')?.value || "0"),
    reason: row.querySelector('[data-field="reason"]')?.value || "",
    text: row.querySelector('[data-field="content"]')?.value || "",
  };
  setButtonLoading(button, true);
  try {
    await API.saveClipFeedback(jobName, payload);
    applyClipFeedbackState(row, action);
    showToast(t(action === "clear" ? "job.feedback_cleared" : "job.feedback_saved"), "success");
  } catch (error) {
    showToast(`${t("job.feedback_failed")}${error.message}`, "error");
  } finally {
    setButtonLoading(button, false);
  }
}

function applyClipFeedbackState(row, action) {
  const value = action === "clear" ? "" : action;
  row.dataset.feedbackAction = value;
  row.querySelectorAll("[data-clip-feedback]").forEach((button) => {
    const active = value && button.dataset.clipFeedback === value;
    button.classList.toggle("active", Boolean(active));
    button.classList.toggle("accepted", active && value === "accepted");
    button.classList.toggle("rejected", active && value === "rejected");
  });
}

function createClipHistory(root, onRestore) {
  const undoStack = [];
  const redoStack = [];
  const limit = 80;
  const entry = (clips) => ({ key: JSON.stringify(clips), clips: clips.map((clip) => ({ ...clip })) });
  const snapshot = () => collectClipHistory(root);
  const updateButtons = () => {
    const undo = root.querySelector("#undo-clips");
    const redo = root.querySelector("#redo-clips");
    if (undo) undo.disabled = undoStack.length < 1;
    if (redo) redo.disabled = redoStack.length < 1;
  };
  const restore = (clips) => {
    const body = root.querySelector("#clip-editor-body");
    if (!body) return;
    body.innerHTML = clips.map((clip, index) => renderClipRow(clip, index)).join("");
    refreshClipRowNumbers(root);
    updateButtons();
    onRestore?.();
  };
  const pushSnapshot = (clips) => {
    if (!Array.isArray(clips) || !clips.length) return;
    const next = entry(clips);
    if (undoStack.at(-1)?.key === next.key) return;
    undoStack.push(next);
    if (undoStack.length > limit) undoStack.shift();
    redoStack.length = 0;
    updateButtons();
  };
  const undo = () => {
    const previous = undoStack.pop();
    if (!previous) return;
    redoStack.push(entry(snapshot()));
    restore(previous.clips);
    showToast(t("job.undo_done"), "success");
  };
  const redo = () => {
    const next = redoStack.pop();
    if (!next) return;
    undoStack.push(entry(snapshot()));
    restore(next.clips);
    showToast(t("job.redo_done"), "success");
  };
  updateButtons();
  return { push: () => pushSnapshot(snapshot()), pushSnapshot, snapshot, undo, redo };
}

export function refreshClipRowNumbers(root = document) {
  root.querySelectorAll("[data-clip-row]").forEach((row, index) => {
    const cell = row.querySelector("td:nth-child(2)");
    if (cell) cell.textContent = String(index + 1);
  });
  syncSelectAllClips(root);
}

export function clipFromRow(row) {
  const start = parseClipTime(row.querySelector('[data-field="start"]')?.value);
  const end = parseClipTime(row.querySelector('[data-field="end"]')?.value);
  return {
    keep: row.querySelector('[data-field="keep"]')?.checked !== false,
    start,
    end,
    duration: Number.isFinite(start) && Number.isFinite(end) ? Math.max(0, end - start) : 0,
    reason: row.querySelector('[data-field="reason"]')?.value || "manual split",
    content_score: row.querySelector("td:nth-child(7)")?.textContent?.trim() || "",
    scene_count: Number(row.querySelector("td:nth-child(8)")?.textContent?.trim() || 0),
    transcript_text: row.querySelector('[data-field="content"]')?.value || "",
    subtitle_text: row.querySelector('[data-field="content"]')?.value || "",
    subtitle_override: row.querySelector('[data-field="content"]')?.dataset.subtitleOverride === "1"
  };
}

function collectClipHistory(root = document) {
  return Array.from(root.querySelectorAll("[data-clip-row]")).map((row) => clipFromRow(row));
}

function selectedClipRows(root = document) {
  return Array.from(root.querySelectorAll("[data-clip-row]")).filter((row) => row.querySelector("[data-clip-select]")?.checked);
}

function setClipSelection(root = document, selected) {
  root.querySelectorAll("[data-clip-row]").forEach((row) => {
    const checkbox = row.querySelector("[data-clip-select]");
    if (checkbox) checkbox.checked = selected;
    row.classList.toggle("selected-row", selected);
  });
  syncSelectAllClips(root);
}

function handleClipSelection(root, checkbox, shiftKey, lastSelectedIndex) {
  const row = checkbox.closest("[data-clip-row]");
  const currentIndex = clipRowIndex(row);
  if (shiftKey && lastSelectedIndex >= 0 && currentIndex >= 0) {
    const [start, end] = [lastSelectedIndex, currentIndex].sort((a, b) => a - b);
    const rows = Array.from(root.querySelectorAll("[data-clip-row]"));
    for (let index = start; index <= end; index += 1) {
      const target = rows[index]?.querySelector("[data-clip-select]");
      if (target) target.checked = checkbox.checked;
      rows[index]?.classList.toggle("selected-row", checkbox.checked);
    }
  } else {
    row?.classList.toggle("selected-row", checkbox.checked);
  }
  syncSelectAllClips(root);
}

function clipRowIndex(row) {
  if (!row) return -1;
  return Array.from(row.parentNode?.querySelectorAll("[data-clip-row]") || []).indexOf(row);
}

function syncSelectAllClips(root = document) {
  const selectAll = root.querySelector("#select-all-clips");
  if (!selectAll) return;
  const rows = Array.from(root.querySelectorAll("[data-clip-row]"));
  const selected = rows.filter((row) => row.querySelector("[data-clip-select]")?.checked).length;
  selectAll.checked = rows.length > 0 && selected === rows.length;
  selectAll.indeterminate = selected > 0 && selected < rows.length;
}

function collectEditedClips(root = document) {
  return Array.from(root.querySelectorAll("[data-clip-row]")).map((row, index) => {
    const start = parseClipTime(row.querySelector('[data-field="start"]')?.value);
    const end = parseClipTime(row.querySelector('[data-field="end"]')?.value);
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
      throw new Error(`clip ${index + 1} start/end is invalid`);
    }
    return {
      start,
      end,
      keep: Boolean(row.querySelector('[data-field="keep"]')?.checked),
      reason: row.querySelector('[data-field="reason"]')?.value || "manual edit",
      ...collectEditedSubtitle(row)
    };
  });
}

function collectEditedSubtitle(row) {
  const input = row.querySelector('[data-field="content"]');
  if (!input) return {};
  const value = input.value.trim();
  const original = String(input.dataset.original || "").trim();
  const existingOverride = input.dataset.subtitleOverride === "1";
  if (!existingOverride && value === original) return {};
  return {
    transcript_text: value,
    subtitle_text: value,
    subtitle_override: true
  };
}

export function setClipMessage(message, isError = false) {
  const box = document.getElementById("clip-editor-message");
  if (!box) return;
  box.innerHTML = `<div class="${isError ? "error" : "notice"}">${message}</div>`;
}
export const collectEditedClipsForTest = collectEditedClips;
