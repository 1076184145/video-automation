import { API } from "./api.js";
import { parseClipTime } from "./clip-time.js";
import { bindCoverActions } from "./cover-panel.js";
import { bindClipEditor, clipFromRow, refreshClipRowNumbers, renderClipRow, setClipMessage } from "./clip-editor.js";
import { bindDetailResizer, bindTimelineActions, seekPreview } from "./detail-layout.js";
import { bindJobDetailTabs } from "./detail-tabs.js";
import { bindEnhancementActions } from "./enhancement-panel.js";
import { bindTranscriptEditor } from "./transcript-editor.js";
import { errorHintHtml } from "./error-hints.js";
import { t } from "./i18n.js";
import { bindDownloadActions, bindJobActions, bindReviewActions } from "./job-actions.js";
import { isJobEventForName, isTypingTarget, loadHealthSafe, loadJobFile, parseEventPayload } from "./job-detail-data.js";
import { renderJobDetailShell, updateJobDetailView } from "./job-detail-view.js";
import { deriveLiveProgress, updateLiveStatus } from "./job-status.js";
import { restoreNativeVideoControls } from "./preview-player.js";
import { renderTimeline } from "./timeline.js";
import { showToast } from "./toast.js";
import { fileMap, formatTime, isTerminal } from "./utils.js";

export async function renderJobDetail(match) {
  const name = decodeURIComponent(match[1]);
  const app = document.getElementById("app");
  let events = null;
  let renderedKey = "";
  let lastStatus = "";
  let resizeTimer = null;
  let timelineData = null;
  let timelineCurrentTime = 0;
  let timelineFrame = 0;
  let timelineView = null;
  let activeTranscriptKey = "";
  let activeClipKey = "";
  let isEditingClips = false;
  let isEditingTranscript = false;
  let deferredFullRender = false;
  let disposed = false;
  const actionCleanups = [];
  const hasUnsavedChanges = () => isEditingClips || isEditingTranscript;

  app.innerHTML = `<div class="loading">${t("common.loading")}</div>`;

  const handleResize = () => {
    if (!timelineData) return;
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      const canvas = document.querySelector("canvas.timeline");
      if (canvas) drawTimeline(canvas);
    }, 120);
  };
  window.addEventListener("resize", handleResize);

  const markEditing = (event) => {
    if (event.target?.closest?.(".clip-editor, .clip-toolbar")) {
      isEditingClips = true;
    }
    if (event.target?.closest?.(".transcript-editor")) {
      isEditingTranscript = true;
    }
  };
  app.addEventListener("input", markEditing, true);
  app.addEventListener("change", markEditing, true);

  function startEvents() {
    if (document.visibilityState !== "visible" || isTerminal(lastStatus) || events) return;
    events = API.openEvents();
    events.addEventListener("hello", (event) => {
      const payload = parseEventPayload(event);
      const current = (payload.jobs || []).find((job) => isJobEventForName(job, name));
      if (current) {
        handleLiveJobEvent(current);
      }
    });
    events.addEventListener("job", (event) => {
      const job = parseEventPayload(event);
      if (!isJobEventForName(job, name)) return;
      handleLiveJobEvent(job);
    });
    events.onerror = () => {
      // EventSource reconnects automatically.
    };
  }

  function stopEvents() {
    if (events) events.close();
    events = null;
  }

  function handleLiveJobEvent(job) {
    lastStatus = job.status || lastStatus;
    if (isTerminal(lastStatus)) {
      load(true);
      stopEvents();
      return;
    }
    deriveLiveProgress(job);
    updateLiveStatus(job);
  }

  const handleVisibility = () => {
    if (document.visibilityState === "visible") {
      load();
      startEvents();
    } else {
      stopEvents();
    }
  };
  document.addEventListener("visibilitychange", handleVisibility);

  const handleBeforeUnload = (event) => {
    if (!hasUnsavedChanges()) return;
    event.preventDefault();
    event.returnValue = "";
  };
  const guardNavigation = (event) => {
    const anchor = event.target?.closest?.('a[href^="#/"]');
    if (!anchor || !hasUnsavedChanges()) return;
    if (window.confirm(t("job.unsaved_confirm"))) return;
    event.preventDefault();
    event.stopPropagation();
  };
  window.addEventListener("beforeunload", handleBeforeUnload);
  document.addEventListener("click", guardNavigation, true);

  const handleKeydown = (event) => {
    if (!event.defaultPrevented && !event.altKey && (event.ctrlKey || event.metaKey) && event.key?.toLowerCase?.() === "z") {
      const body = document.getElementById("clip-editor-body");
      if (body && !event.target?.closest?.(".transcript-editor")) {
        event.preventDefault();
        body.dispatchEvent(new CustomEvent("clip-editor:history", {
          bubbles: true,
          detail: { direction: event.shiftKey ? "redo" : "undo" }
        }));
      }
      return;
    }
    if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return;
    if (isTypingTarget(event.target)) return;
    const video = document.querySelector("#section-preview video");
    if (!video) return;
    if (event.code === "Space") {
      event.preventDefault();
      if (video.paused) video.play().catch(() => {});
      else video.pause();
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      video.currentTime = Math.max(0, (video.currentTime || 0) - 5);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      const duration = Number.isFinite(video.duration) ? video.duration : Number.MAX_SAFE_INTEGER;
      video.currentTime = Math.min(duration, (video.currentTime || 0) + 5);
    } else if (event.key === "[" || event.key === "]") {
      event.preventDefault();
      setActiveClipBoundary(event.key === "[" ? "start" : "end", video.currentTime || 0);
    } else if (event.key?.toLowerCase?.() === "s") {
      event.preventDefault();
      splitActiveClip(video.currentTime || 0);
    }
  };
  document.addEventListener("keydown", handleKeydown);

  function redrawTimeline() {
    const canvas = document.querySelector("canvas.timeline");
    if (canvas && timelineData) {
      drawTimeline(canvas);
    }
  }

  function drawTimeline(canvas) {
    if (!canvas || !timelineData) return;
    ensureTimelineView(timelineData.duration);
    renderTimeline(canvas, timelineData, {
      currentTime: timelineCurrentTime,
      viewStart: timelineView.start,
      viewEnd: timelineView.end,
      onViewChange: updateTimelineView
    });
    updateTimelineWindowLabel();
  }

  function ensureTimelineView(duration) {
    const total = Math.max(1, Number(duration || 1));
    if (!timelineView || timelineView.duration !== total) {
      timelineView = { start: 0, end: total, duration: total };
      return;
    }
    const span = Math.max(1, Math.min(total, timelineView.end - timelineView.start));
    const start = Math.max(0, Math.min(total - span, timelineView.start));
    timelineView = { start, end: start + span, duration: total };
  }

  function updateTimelineView(next) {
    if (!timelineView || !next) return;
    const total = timelineView.duration;
    const span = Math.max(1, Math.min(total, Number(next.viewEnd) - Number(next.viewStart)));
    const start = Math.max(0, Math.min(total - span, Number(next.viewStart) || 0));
    timelineView = { start, end: start + span, duration: total };
    redrawTimeline();
  }

  function resetTimelineView() {
    if (!timelineView) return;
    timelineView = { start: 0, end: timelineView.duration, duration: timelineView.duration };
    redrawTimeline();
  }

  function updateTimelineWindowLabel() {
    const label = document.getElementById("timeline-window");
    if (!label || !timelineView) return;
    label.textContent = `${formatTime(timelineView.start)} - ${formatTime(timelineView.end)} / ${formatTime(timelineView.duration)}`;
  }

  function scheduleTimelineRedraw(video) {
    timelineCurrentTime = Number(video?.currentTime || 0);
    if (timelineFrame) return;
    timelineFrame = requestAnimationFrame(() => {
      timelineFrame = 0;
      redrawTimeline();
      updateActiveTranscript(timelineCurrentTime, { allowScroll: !isEditingTranscript });
      updateActiveClipRow(timelineCurrentTime);
    });
  }

  function bindPreviewPlayer(video) {
    if (!video) return;
    if (video.dataset.timelineBound !== "1") {
      video.dataset.timelineBound = "1";
      const sync = () => scheduleTimelineRedraw(video);
      const flushDeferred = () => {
        if (!deferredFullRender || hasUnsavedChanges()) return;
        deferredFullRender = false;
        load(true);
      };
      video.addEventListener("timeupdate", sync);
      video.addEventListener("seeked", sync);
      video.addEventListener("loadedmetadata", sync);
      video.addEventListener("pause", flushDeferred);
      video.addEventListener("ended", flushDeferred);
    }
    restoreNativeVideoControls(video);
  }

  function updateActiveTranscript(currentTime, { allowScroll } = { allowScroll: true }) {
    const rows = Array.from(document.querySelectorAll("[data-transcript-row]"));
    let active = null;
    rows.forEach((row, index) => {
      const input = row.querySelector(".transcript-input");
      const start = Number(input?.dataset.start || 0);
      const end = Number(input?.dataset.end || start);
      const isActive = Number.isFinite(start) && Number.isFinite(end) && currentTime >= start && currentTime < Math.max(end, start + 0.25);
      row.classList.toggle("active", isActive);
      if (isActive) active = { row, key: String(index) };
    });
    if (!active) {
      activeTranscriptKey = "";
      return;
    }
    if (allowScroll && active.key !== activeTranscriptKey) {
      active.row.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
    activeTranscriptKey = active.key;
  }

  function updateActiveClipRow(currentTime) {
    const rows = Array.from(document.querySelectorAll("[data-clip-row]"));
    let nextKey = "";
    rows.forEach((row, index) => {
      const start = parseClipTime(row.querySelector('[data-field="start"]')?.value);
      const end = parseClipTime(row.querySelector('[data-field="end"]')?.value);
      const isActive = Number.isFinite(start) && Number.isFinite(end) && currentTime >= start && currentTime < Math.max(end, start + 0.25);
      row.classList.toggle("active-row", isActive);
      if (isActive) nextKey = String(index);
    });
    activeClipKey = nextKey;
  }

  function activeClipRow() {
    const rows = Array.from(document.querySelectorAll("[data-clip-row]"));
    if (activeClipKey) return rows[Number(activeClipKey)] || null;
    return rows.find((row) => row.classList.contains("active-row")) || rows[0] || null;
  }

  function setActiveClipBoundary(field, seconds) {
    const row = activeClipRow();
    if (!row) {
      showToast(t("job.no_active_clip"), "warning");
      return;
    }
    const startInput = row.querySelector('[data-field="start"]');
    const endInput = row.querySelector('[data-field="end"]');
    const start = parseClipTime(startInput?.value);
    const end = parseClipTime(endInput?.value);
    const value = Math.max(0, Number(seconds) || 0);
    if (field === "start") {
      if (Number.isFinite(end) && value >= end) {
        showToast(t("job.shortcut_invalid_range"), "warning");
        return;
      }
      row.dispatchEvent(new CustomEvent("clip-editor:before-mutate", { bubbles: true }));
      startInput.value = formatClipTimeInput(value);
    } else {
      if (Number.isFinite(start) && value <= start) {
        showToast(t("job.shortcut_invalid_range"), "warning");
        return;
      }
      row.dispatchEvent(new CustomEvent("clip-editor:before-mutate", { bubbles: true }));
      endInput.value = formatClipTimeInput(value);
    }
    row.classList.add("active-row");
    isEditingClips = true;
    setClipMessage(t(field === "start" ? "job.shortcut_start_set" : "job.shortcut_end_set"));
  }

  function splitActiveClip(seconds) {
    const row = activeClipRow();
    if (!row) {
      showToast(t("job.no_active_clip"), "warning");
      return;
    }
    const startInput = row.querySelector('[data-field="start"]');
    const endInput = row.querySelector('[data-field="end"]');
    const start = parseClipTime(startInput?.value);
    const end = parseClipTime(endInput?.value);
    const split = Number(seconds) || 0;
    if (!Number.isFinite(start) || !Number.isFinite(end) || split <= start + 0.05 || split >= end - 0.05) {
      showToast(t("job.shortcut_split_invalid"), "warning");
      return;
    }
    row.dispatchEvent(new CustomEvent("clip-editor:before-mutate", { bubbles: true }));
    const nextClip = clipFromRow(row);
    nextClip.start = split;
    nextClip.end = end;
    nextClip.duration = end - split;
    endInput.value = formatClipTimeInput(split);
    row.insertAdjacentHTML("afterend", renderClipRow(nextClip, 0));
    refreshClipRowNumbers(document);
    isEditingClips = true;
    setClipMessage(t("job.shortcut_split_done"));
    showToast(t("job.shortcut_split_done"), "success");
  }

  let isFirstRender = true;

  async function load(forceRender = false) {
    if (disposed) return null;
    try {
      const job = await API.getJob(name);
      if (disposed) return null;
      lastStatus = job.status;
      const files = fileMap(job);
      const jobState = await loadJobFile(name, files, "job.json");
      if (disposed) return null;
      if (jobState) {
        Object.assign(job, jobState);
      }
      deriveLiveProgress(job);

      if (!forceRender && !isFirstRender && shouldDeferFullRender(job)) {
        deferredFullRender = true;
        updateLiveStatus(job);
        return { job, payload: null };
      }

      const nextKey = `${job.status}|${job.updated_at}|${Array.from(files.values()).map((file) => `${file.name}:${file.size_bytes || 0}:${file.modified_at || 0}`).sort().join("|")}`;
      if (!forceRender && !isFirstRender && renderedKey === nextKey && !detailSectionsNeedRender()) {
        updateLiveStatus(job);
        return;
      }

      const [manifest, corrupt, cuts, transcript, silence, freeze, scene, waveform, stageTimings, cover, segments, metadata, highlights, highlightCut, highlightRender, publishPackage, projectExport, health] = await Promise.all([
        loadJobFile(name, files, "manifest.json"),
        loadJobFile(name, files, "corrupt.json"),
        loadJobFile(name, files, "cuts.json"),
        loadJobFile(name, files, "transcript.json"),
        loadJobFile(name, files, "silence.json"),
        loadJobFile(name, files, "freeze.json"),
        loadJobFile(name, files, "scene.json"),
        loadJobFile(name, files, "waveform.json"),
        loadJobFile(name, files, "stage_timings.json"),
        loadJobFile(name, files, "cover_manifest.json"),
        loadJobFile(name, files, "segments_manifest.json"),
        loadJobFile(name, files, "metadata.json"),
        loadJobFile(name, files, "highlights.json"),
        loadJobFile(name, files, "highlight_cut.json"),
        loadJobFile(name, files, "highlight_render_status.json"),
        loadJobFile(name, files, "publish_package.json"),
        loadJobFile(name, files, "project_export_manifest.json"),
        loadHealthSafe()
      ]);
      if (disposed) return null;

      const payload = { manifest, corrupt, cuts, transcript, silence, freeze, scene, waveform, stageTimings, cover, segments, metadata, highlights, highlightCut, highlightRender, publishPackage, projectExport, health };

      if (isFirstRender) {
        app.innerHTML = renderJobDetailShell();
        isFirstRender = false;
        actionCleanups.push(
          bindJobDetailTabs(app, { jobName: name, status: job.status }),
          bindTimelineActions(app, resetTimelineView),
          bindDetailResizer(app),
          bindReviewActions(app, name, () => load(true)),
          bindJobActions(app, name, () => load(true)),
          bindClipEditor(app, name, () => {
            isEditingClips = false;
            return load(true);
          }, (editing) => {
            isEditingClips = editing;
          }, seekPreview),
          bindTranscriptEditor(app, name, () => {
            isEditingTranscript = false;
            return load(true);
          }, (editing) => {
            isEditingTranscript = editing;
          }, seekPreview),
          bindCoverActions(app, name, () => load(true)),
          bindEnhancementActions(app, name, () => load(true), seekPreview),
          bindDownloadActions(app)
        );
      }

      updateJobDetailView(job, files, payload, {
        isEditingClips,
        isEditingTranscript,
        bindPreview: bindPreviewPlayer,
      });
      renderedKey = nextKey;

      const canvas = document.querySelector("canvas.timeline");
      if (canvas) {
        timelineData = {
          duration: cuts?.duration_seconds || manifest?.duration_seconds || 1,
          clips: cuts?.clips || [],
          invalid: cuts?.invalid_segments || [],
          scenes: scene?.scenes || cuts?.highlight_signals?.scenes || [],
          transcript: transcript?.segments || [],
          waveform
        };
        drawTimeline(canvas);
      }

      if (isTerminal(job.status)) stopEvents();
      else startEvents();
      return { job, payload };
    } catch (error) {
      if (isFirstRender) {
        app.innerHTML = `<div class="error">${errorHintHtml(error.message)} <button class="button" id="retry">${t("common.retry")}</button></div>`;
        document.getElementById("retry")?.addEventListener("click", () => load());
      } else {
        console.error("Job update failed", error);
      }
    }
  }

  function detailSectionsNeedRender() {
    return ["section-covers", "section-enhancements", "section-downloads"].some((id) => {
      const element = document.getElementById(id);
      return element && (!element.innerHTML.trim() || Boolean(element.querySelector(".loading")));
    });
  }

  function shouldDeferFullRender(job) {
    if (isTerminal(job.status)) return false;
    if (hasUnsavedChanges()) return true;
    const video = document.querySelector("#section-preview video");
    if (video && !video.paused && !video.ended) return true;
    return Boolean(app.querySelector("details[open]"));
  }

  await load();
  if (!events && !isTerminal(lastStatus)) {
    startEvents();
  }
  return () => {
    disposed = true;
    stopEvents();
    clearTimeout(resizeTimer);
    if (timelineFrame) cancelAnimationFrame(timelineFrame);
    actionCleanups.forEach((cleanup) => cleanup());
    window.removeEventListener("resize", handleResize);
    document.removeEventListener("visibilitychange", handleVisibility);
    window.removeEventListener("beforeunload", handleBeforeUnload);
    document.removeEventListener("click", guardNavigation, true);
    document.removeEventListener("keydown", handleKeydown);
    app.removeEventListener("input", markEditing, true);
    app.removeEventListener("change", markEditing, true);
  };
}
