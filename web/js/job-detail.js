import { API } from "./api.js";
import { t } from "./i18n.js";
import { renderTimeline } from "./timeline.js";
import { STAGES, basename, escapeHtml, fileMap, formatDate, formatTime, isTerminal, stageForStatus, statusGroup, statusLabelKey } from "./utils.js";

export async function renderJobDetail(match) {
  const name = decodeURIComponent(match[1]);
  const app = document.getElementById("app");
  let timer = null;
  let renderedKey = "";
  let lastStatus = "";
  let resizeTimer = null;
  let coverTimer = null;
  let timelineData = null;
  let isEditingClips = false;
  let isEditingTranscript = false;
  const actionCleanups = [];
  const hasUnsavedChanges = () => isEditingClips || isEditingTranscript;

  app.innerHTML = `<div class="loading">${t("common.loading")}</div>`;

  const handleResize = () => {
    if (!timelineData) return;
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      const canvas = document.querySelector("canvas.timeline");
      if (canvas) renderTimeline(canvas, timelineData);
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

  function startPolling() {
    if (document.visibilityState !== "visible" || isTerminal(lastStatus) || timer) return;
    timer = setInterval(load, 1500);
  }

  function stopPolling() {
    if (timer) clearInterval(timer);
    timer = null;
  }

  function stopCoverPolling() {
    if (coverTimer) clearInterval(coverTimer);
    coverTimer = null;
  }

  function syncCoverPolling(cover) {
    if (cover?.status === "generating") {
      if (!coverTimer) coverTimer = setInterval(() => load(true), 2500);
    } else {
      stopCoverPolling();
    }
  }

  const handleVisibility = () => {
    if (document.visibilityState === "visible") {
      load();
      startPolling();
    } else {
      stopPolling();
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

  let isFirstRender = true;

  async function load(forceRender = false) {
    try {
      const job = await API.getJob(name);
      lastStatus = job.status;
      const files = fileMap(job);
      const jobState = await loadFile(name, files, "job.json");
      if (jobState) {
        Object.assign(job, jobState);
      }
      deriveLiveProgress(job);

      const nextKey = `${job.status}|${job.updated_at}|${Array.from(files.values()).map((file) => `${file.name}:${file.size_bytes || 0}:${file.modified_at || 0}`).sort().join("|")}`;
      if (!forceRender && !isFirstRender && renderedKey === nextKey) {
        updateLiveStatus(job);
        return;
      }

      const [manifest, cuts, transcript, silence, freeze, scene, waveform, cover, segments, metadata, highlights, publishPackage, projectExport, health] = await Promise.all([
        loadFile(name, files, "manifest.json"),
        loadFile(name, files, "cuts.json"),
        loadFile(name, files, "transcript.json"),
        loadFile(name, files, "silence.json"),
        loadFile(name, files, "freeze.json"),
        loadFile(name, files, "scene.json"),
        loadFile(name, files, "waveform.json"),
        loadFile(name, files, "cover_manifest.json"),
        loadFile(name, files, "segments_manifest.json"),
        loadFile(name, files, "metadata.json"),
        loadFile(name, files, "highlights.json"),
        loadFile(name, files, "publish_package.json"),
        loadFile(name, files, "project_export_manifest.json"),
        loadHealthSafe()
      ]);

      const payload = { manifest, cuts, transcript, silence, freeze, scene, waveform, cover, segments, metadata, highlights, publishPackage, projectExport, health };

      if (isFirstRender) {
        app.innerHTML = pageShell();
        isFirstRender = false;
        actionCleanups.push(
          bindReviewActions(app, name, () => load(true)),
          bindJobActions(app, name, () => load(true)),
          bindClipEditor(app, name, () => {
            isEditingClips = false;
            return load(true);
          }, (editing) => {
            isEditingClips = editing;
          }),
          bindTranscriptEditor(app, name, () => {
            isEditingTranscript = false;
            return load(true);
          }, (editing) => {
            isEditingTranscript = editing;
          }),
          bindCoverActions(app, name, () => load(true)),
          bindEnhancementActions(app, name, () => load(true))
        );
      }

      updateGranular(job, files, payload, isEditingClips, isEditingTranscript);
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
        renderTimeline(canvas, timelineData);
      }

      if (isTerminal(job.status)) stopPolling();
      else startPolling();
      syncCoverPolling(cover);
      return { job, payload };
    } catch (error) {
      if (isFirstRender) {
        app.innerHTML = `<div class="error">${t("common.error")} ${escapeHtml(error.message)} <button class="button" id="retry">${t("common.retry")}</button></div>`;
        document.getElementById("retry")?.addEventListener("click", () => load());
      } else {
        console.error("Job update failed", error);
      }
    }
  }

  await load();
  if (!timer && !isTerminal(lastStatus)) {
    startPolling();
  }
  return () => {
    stopPolling();
    stopCoverPolling();
    clearTimeout(resizeTimer);
    actionCleanups.forEach((cleanup) => cleanup());
    window.removeEventListener("resize", handleResize);
    document.removeEventListener("visibilitychange", handleVisibility);
    window.removeEventListener("beforeunload", handleBeforeUnload);
    document.removeEventListener("click", guardNavigation, true);
    app.removeEventListener("input", markEditing, true);
    app.removeEventListener("change", markEditing, true);
  };
}

async function loadFile(jobName, files, filename) {
  if (!files.has(filename)) return null;
  try {
    return await API.getJobFile(jobName, filename);
  } catch {
    return null;
  }
}

async function loadHealthSafe() {
  try {
    return await API.getHealth({ timeout: 5000, retries: 0 });
  } catch {
    return null;
  }
}

function pageShell() {
  return `
    <div id="section-head"></div>
    <div id="section-review"></div>
    <section class="panel live-progress" id="section-progress"></section>
    <div id="section-actions"></div>
    <section class="panel">
      <h2>${t("job.pipeline")}</h2>
      <div class="pipeline" id="section-pipeline"></div>
    </section>
    <section class="panel">
      <h2>${t("job.preview")}</h2>
      <div id="section-preview"></div>
    </section>
    <section class="panel cover-panel">
      <h2>${t("cover.title")}</h2>
      <div id="section-covers"></div>
    </section>
    <section class="panel enhancements-panel">
      <h2>${t("enhance.title")}</h2>
      <div id="section-enhancements"></div>
    </section>
    <section class="panel timeline-wrap">
      <h2>${t("job.timeline")}</h2>
      ${renderTimelineLegend()}
      <canvas class="timeline" role="img" aria-label="${t("timeline.aria")}"></canvas>
    </section>
    <section class="detail-grid">
      <div class="panel">
        <h2>${t("job.transcript")}</h2>
        <div id="section-transcript"></div>
      </div>
      <div class="panel">
        <h2>${t("job.clips")}</h2>
        <div id="section-clips"></div>
      </div>
    </section>
    <section class="panel">
      <h2>${t("job.downloads")}</h2>
      <div class="downloads" id="section-downloads"></div>
    </section>
    <section class="panel" id="section-meta"></section>
  `;
}

function updateGranular(job, files, payload, isEditingClips, isEditingTranscript) {
  const { manifest = {}, cuts = {}, transcript = {} } = payload;
  const safeHtml = (id, html) => {
    const el = document.getElementById(id);
    if (el && el.innerHTML !== html) el.innerHTML = html;
  };

  safeHtml("section-head", `
    <section class="page-head">
      <div>
        <p class="page-subtitle"><a href="#/">${t("nav.dashboard")}</a> / ${t("job.detail")}</p>
        <h1 class="page-title">${escapeHtml(manifest.source_name || basename(job.source_path))}</h1>
      </div>
      <span class="badge ${statusGroup(job.status)}">${t(statusLabelKey(job.status))}</span>
    </section>
  `);

  safeHtml("section-review", job.status === "needs_review" ? renderReviewActions() : "");
  safeHtml("section-actions", renderJobActions());
  safeHtml("section-progress", renderLiveProgress(job));
  safeHtml("section-pipeline", STAGES.map((stage) => renderStage(stage, job, files)).join(""));

  const preview = files.has("final.mp4") ? "final.mp4" : files.has("review.mp4") ? "review.mp4" : "";
  const previewFile = preview ? files.get(preview) : null;
  const previewVersion = previewFile ? `${previewFile.size_bytes || 0}-${previewFile.modified_at || 0}` : "";
  const previewUrl = preview ? API.jobFileUrl(basename(job.job_dir), preview, false, previewVersion) : "";
  
  const previewContainer = document.getElementById("section-preview");
  if (previewContainer) {
    if (!preview) {
      if (previewContainer.innerHTML !== `<div class="empty">${t("job.no_preview")}</div>`) {
        previewContainer.innerHTML = `<div class="empty">${t("job.no_preview")}</div>`;
      }
    } else {
      let video = previewContainer.querySelector("video");
      if (!video) {
        previewContainer.innerHTML = `<video class="video-preview" src="${previewUrl}" controls preload="metadata"></video>`;
        video = previewContainer.querySelector("video");
        setPreviewOrientation(video);
      } else if (video.getAttribute("src") !== previewUrl) {
        video.setAttribute("src", previewUrl);
        setPreviewOrientation(video);
      } else {
        setPreviewOrientation(video);
      }
    }
  }

  if (!isEditingTranscript) {
    safeHtml("section-transcript", renderTranscript(transcript));
  }

  if (!isEditingClips) {
    safeHtml("section-clips", renderClips(cuts));
  }

  safeHtml("section-covers", renderCovers(basename(job.job_dir), files, payload.cover, manifest, cuts, transcript, payload.health));
  safeHtml("section-enhancements", renderEnhancements(basename(job.job_dir), files, payload));
  safeHtml("section-downloads", renderDownloads(basename(job.job_dir), files));

  safeHtml("section-meta", `
    <div class="meta">${t("common.created")}: ${escapeHtml(formatDate(job.created_at))} · ${t("common.duration")}: ${formatTime(manifest.duration_seconds || cuts.duration_seconds || 0)}</div>
    ${job.error ? `<div class="error">${escapeHtml(job.error)}</div>` : ""}
  `);

  updateLiveStatus(job);
}

function setPreviewOrientation(video) {
  if (!video) return;
  const apply = () => {
    const portrait = video.videoHeight > video.videoWidth;
    video.classList.toggle("is-portrait", portrait);
    video.classList.toggle("is-landscape", !portrait);
  };
  video.removeEventListener("loadedmetadata", apply);
  video.addEventListener("loadedmetadata", apply, { once: true });
  if (video.readyState >= 1) apply();
}

function renderJobActions() {
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

function bindJobActions(root, jobName, reload) {
  const handler = async (e) => {
    if (e.target.id === "rerun-stage-button") {
      const button = e.target;
      const stage = document.getElementById("rerun-stage")?.value;
      if (!stage || !window.confirm(`${t("job.rerun_confirm")} ${t(`stage.${stage}`)}`)) return;
      button.disabled = true;
      try {
        await API.rerunStage(jobName, stage);
        setActionMessage(t("job.rerun_started"));
        await reload();
      } catch (error) {
        setActionMessage(`${t("job.rerun_failed")}${escapeHtml(error.message)}`, true);
      } finally {
        button.disabled = false;
      }
    } else if (e.target.id === "delete-job") {
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

function setActionMessage(message, isError = false) {
  const box = document.getElementById("job-action-message");
  if (!box) return;
  box.innerHTML = `<div class="${isError ? "error" : "notice"}">${message}</div>`;
}

function renderReviewActions() {
  return `
    <section class="panel review-actions">
      <div>
        <h2>${t("status.review")}</h2>
        <p class="page-subtitle">${t("job.approve_note")}</p>
        <div id="approve-error"></div>
      </div>
      <button class="button primary" id="approve-job" type="button">${t("job.approve")}</button>
    </section>
  `;
}

function bindReviewActions(root, jobName, reload) {
  const handler = async (e) => {
    if (e.target.id === "approve-job") {
      const button = e.target;
      button.disabled = true;
      button.textContent = t("common.loading");
      try {
        await API.approveJob(jobName);
        await reload();
      } catch (error) {
        const box = document.getElementById("approve-error");
        if (box) box.innerHTML = `<div class="error">${t("job.approve_failed")}${escapeHtml(error.message)}</div>`;
        button.disabled = false;
        button.textContent = t("job.approve");
      }
    }
  };
  root.addEventListener("click", handler);
  return () => root.removeEventListener("click", handler);
}

function renderCovers(jobName, files, cover, manifest, cuts, transcript, health) {
  const state = cover || { status: "idle", candidates: {}, selected: {} };
  const defaultTitle = escapeHtml(state.title || defaultCoverTitle(manifest, cuts, transcript));
  const status = state.status || "idle";
  const coverSettings = health?.settings?.covers || {};
  const missingKey = coverSettings.provider === "openai" && coverSettings.openai_api_key_configured === false;
  const message = [
    missingKey ? `<div class="notice">${t("cover.key_missing")}</div>` : "",
    state.error ? `<div class="error">${escapeHtml(state.error)}</div>` : status === "generating" ? `<div class="notice">${t("cover.generating")}</div>` : ""
  ].join("");
  const candidateHtml = ["9:16", "16:9"].map((aspect) => renderCoverAspect(jobName, files, state, aspect)).join("");
  return `
    <div class="cover-form">
      <div class="field">
        <label for="cover-title">${t("cover.video_title")}</label>
        <input id="cover-title" type="text" value="${defaultTitle}" placeholder="${t("cover.title_placeholder")}" />
      </div>
      <div class="cover-controls">
        <div class="field compact">
          <label for="cover-style">${t("cover.style")}</label>
          <select id="cover-style">
            ${["short_video", "clean", "cinematic", "gaming"].map((value) => `<option value="${value}" ${state.style === value ? "selected" : ""}>${t(`cover.style_${value}`)}</option>`).join("")}
          </select>
        </div>
        <div class="field compact">
          <label for="cover-count">${t("cover.count")}</label>
          <select id="cover-count">
            <option value="3" ${(state.count || 3) !== 5 ? "selected" : ""}>3</option>
            <option value="5" ${(state.count || 3) === 5 ? "selected" : ""}>5</option>
          </select>
        </div>
        <label class="check"><input id="cover-aspect-vertical" type="checkbox" value="9:16" ${(state.aspects || ["9:16", "16:9"]).includes("9:16") ? "checked" : ""} /> ${t("cover.vertical")}</label>
        <label class="check"><input id="cover-aspect-landscape" type="checkbox" value="16:9" ${(state.aspects || ["9:16", "16:9"]).includes("16:9") ? "checked" : ""} /> ${t("cover.landscape")}</label>
        <button class="button primary" id="generate-covers" type="button" ${status === "generating" || missingKey ? "disabled" : ""}>${status === "generating" ? t("common.loading") : t("cover.generate")}</button>
      </div>
      <p class="muted">${t("cover.usage_note")}</p>
      <div id="cover-message">${message}</div>
    </div>
    <div class="cover-grid">${candidateHtml || `<div class="empty">${t("cover.no_candidates")}</div>`}</div>
  `;
}

function renderCoverAspect(jobName, files, cover, aspect) {
  const candidates = cover?.candidates?.[aspect] || [];
  if (!candidates.length) return "";
  const selected = cover?.selected?.[aspect] || "";
  return `
    <div class="cover-aspect ${aspect === "16:9" ? "landscape" : "vertical"}">
      <div class="cover-aspect-head">
        <h3>${aspect === "9:16" ? t("cover.vertical") : t("cover.landscape")}</h3>
        ${selected ? `<span class="badge done">${t("cover.selected")}: ${escapeHtml(selected)}</span>` : ""}
      </div>
      <div class="cover-candidates">
        ${candidates.map((candidate) => renderCoverCandidate(jobName, files, candidate, aspect, selected)).join("")}
      </div>
    </div>
  `;
}

function renderCoverCandidate(jobName, files, candidate, aspect, selected) {
  const file = candidate.file || "";
  if (!file || !files.has(file)) return "";
  const info = files.get(file);
  const cacheKey = `${info.size_bytes || 0}-${info.modified_at || 0}`;
  const url = API.jobFileUrl(jobName, file, false, cacheKey);
  const downloadUrl = API.jobFileUrl(jobName, file, true, cacheKey);
  const isSelected = selected === file;
  return `
    <article class="cover-card ${isSelected ? "selected" : ""}">
      <img src="${url}" alt="${escapeHtml(file)}" loading="lazy" />
      <div class="cover-card-actions">
        <button class="button compact-button ${isSelected ? "primary" : ""}" type="button" data-select-cover data-aspect="${aspect}" data-file="${escapeHtml(file)}">${isSelected ? t("cover.selected") : t("cover.select")}</button>
        <a class="button compact-button download-link file-image" download href="${downloadUrl}">${t("common.download")}</a>
      </div>
    </article>
  `;
}

function bindCoverActions(root, jobName, reload) {
  const handler = async (e) => {
    if (e.target.id === "generate-covers") {
      if (!window.confirm(t("cover.confirm_generate"))) return;
      const button = e.target;
      button.disabled = true;
      button.textContent = t("common.loading");
      try {
        await API.generateCovers(jobName, collectCoverOptions());
        setCoverMessage(t("cover.started"));
        await reload();
      } catch (error) {
        setCoverMessage(`${t("cover.failed")}${escapeHtml(error.message)}`, true);
      } finally {
        button.disabled = false;
        button.textContent = t("cover.generate");
      }
      return;
    }
    const selectButton = e.target.closest("[data-select-cover]");
    if (!selectButton) return;
    selectButton.disabled = true;
    try {
      await API.selectCover(jobName, {
        aspect: selectButton.dataset.aspect,
        candidate: selectButton.dataset.file
      });
      setCoverMessage(t("cover.select_ok"));
      await reload();
    } catch (error) {
      setCoverMessage(`${t("cover.select_failed")}${escapeHtml(error.message)}`, true);
    } finally {
      selectButton.disabled = false;
    }
  };
  root.addEventListener("click", handler);
  return () => root.removeEventListener("click", handler);
}

function collectCoverOptions() {
  const aspects = Array.from(document.querySelectorAll("#cover-aspect-vertical, #cover-aspect-landscape"))
    .filter((input) => input.checked)
    .map((input) => input.value);
  return {
    title: document.getElementById("cover-title")?.value?.trim() || "",
    style: document.getElementById("cover-style")?.value || "short_video",
    count: Number(document.getElementById("cover-count")?.value || 3),
    aspects: aspects.length ? aspects : ["9:16", "16:9"]
  };
}

function setCoverMessage(message, isError = false) {
  const box = document.getElementById("cover-message");
  if (!box) return;
  box.innerHTML = `<div class="${isError ? "error" : "notice"}">${message}</div>`;
}

function defaultCoverTitle(manifest, cuts, transcript) {
  const source = manifest?.source_name || "";
  const clipText = (cuts?.clips || []).find((clip) => clip?.transcript_text)?.transcript_text || "";
  const transcriptText = (transcript?.segments || []).find((segment) => segment?.text)?.text || "";
  return basename(source || clipText || transcriptText || "video-cover").replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").slice(0, 48);
}

function renderEnhancements(jobName, files, payload) {
  const settings = payload.health?.settings?.optional_modules || {};
  const llmConfigured = Boolean(settings.llm_model) && payload.health?.settings?.covers?.openai_api_key_configured !== false;
  return `
    <div class="enhancement-grid">
      ${renderSegmentsPanel(jobName, files, payload.segments)}
      ${renderMetadataPanel(payload.metadata, llmConfigured)}
      ${renderHighlightsPanel(payload.highlights, llmConfigured)}
      ${renderPublishPanel(jobName, files, payload.publishPackage)}
      ${renderProjectExportPanel(jobName, files, payload.projectExport)}
    </div>
    <div id="enhancement-message"></div>
  `;
}

function renderPlatformChecks(idPrefix = "enhance") {
  return ["douyin", "bilibili", "youtube_shorts"].map((platform) => `
    <label class="check compact-check"><input class="${idPrefix}-platform" type="checkbox" value="${platform}" ${platform === "douyin" ? "checked" : ""} /> ${t(`platform.${platform}`)}</label>
  `).join("");
}

function renderSegmentsPanel(jobName, files, segments) {
  const platformHtml = (segments?.platforms || []).map((platform) => `
    <div class="mini-list">
      <strong>${t(`platform.${platform.name}`)} · ${platform.segment_count || 0}</strong>
      ${(platform.segments || []).map((segment) => {
        const file = segment.file || "";
        const exists = files.has(file);
        return `<a class="mini-row ${exists ? "" : "disabled"}" ${exists ? `download href="${API.jobFileUrl(jobName, file, true)}"` : ""}>${escapeHtml(file)} · ${formatTime(segment.duration)}</a>`;
      }).join("")}
    </div>
  `).join("");
  return `
    <article class="enhancement-card">
      <h3>${t("enhance.segments")}</h3>
      <p class="muted">${t("enhance.segments_note")}</p>
      <div class="inline-options">${renderPlatformChecks("segment")}</div>
      <button class="button" id="generate-segments" type="button">${t("enhance.generate_segments")}</button>
      ${platformHtml || `<div class="empty">${t("enhance.no_segments")}</div>`}
    </article>
  `;
}

function renderMetadataPanel(metadata, llmConfigured) {
  const value = escapeHtml(JSON.stringify(metadata || metadataTemplate(), null, 2));
  return `
    <article class="enhancement-card">
      <h3>${t("enhance.metadata")}</h3>
      <p class="muted">${llmConfigured ? t("enhance.metadata_note") : t("enhance.llm_missing")}</p>
      <div class="field compact">
        <label for="metadata-platform">${t("enhance.platform")}</label>
        <select id="metadata-platform">
          ${["douyin", "bilibili", "youtube_shorts"].map((platform) => `<option value="${platform}">${t(`platform.${platform}`)}</option>`).join("")}
        </select>
      </div>
      <div class="button-row">
        <button class="button" id="generate-metadata" type="button" ${llmConfigured ? "" : "disabled"}>${t("enhance.generate_metadata")}</button>
        <button class="button primary" id="save-metadata" type="button">${t("common.save")}</button>
      </div>
      <textarea class="json-editor" id="metadata-json" spellcheck="false">${value}</textarea>
    </article>
  `;
}

function renderHighlightsPanel(highlights, llmConfigured) {
  const items = highlights?.highlights || [];
  return `
    <article class="enhancement-card">
      <h3>${t("enhance.highlights")}</h3>
      <p class="muted">${llmConfigured ? t("enhance.highlights_note") : t("enhance.llm_missing")}</p>
      <button class="button" id="generate-highlights" type="button" ${llmConfigured ? "" : "disabled"}>${t("enhance.generate_highlights")}</button>
      ${highlights?.summary ? `<p>${escapeHtml(highlights.summary)}</p>` : ""}
      ${items.length ? `<div class="mini-list">${items.map((item) => `
        <button class="mini-row" type="button" data-seek="${Number(item.start || 0)}">
          ${formatTime(item.start)}-${formatTime(item.end)} · ${escapeHtml(String(item.score ?? "-"))} · ${escapeHtml(item.reason || "")}
        </button>
      `).join("")}</div>` : `<div class="empty">${t("enhance.no_highlights")}</div>`}
    </article>
  `;
}

function renderPublishPanel(jobName, files, publishPackage) {
  const packageFiles = ["publish_package.json", "metadata.json", "cover_vertical.jpg", "cover_landscape.jpg", "final.mp4", "review.mp4"].filter((name) => files.has(name));
  return `
    <article class="enhancement-card">
      <h3>${t("enhance.publish_package")}</h3>
      <p class="muted">${t("enhance.publish_note")}</p>
      <div class="inline-options">${renderPlatformChecks("publish")}</div>
      <button class="button" id="generate-publish-package" type="button">${t("enhance.generate_publish_package")}</button>
      ${publishPackage ? `<div class="notice">${t("enhance.publish_ready")}</div>` : ""}
      <div class="mini-list">
        ${packageFiles.map((name) => `<a class="mini-row" download href="${API.jobFileUrl(jobName, name, true)}">${escapeHtml(name)}</a>`).join("") || `<div class="empty">${t("common.empty")}</div>`}
      </div>
    </article>
  `;
}

function renderProjectExportPanel(jobName, files, projectExport) {
  const exportFiles = projectExportFiles(projectExport);
  return `
    <article class="enhancement-card">
      <h3>${t("enhance.project_export")}</h3>
      <p class="muted">${t("enhance.project_export_note")}</p>
      <div class="inline-options">
        <label class="check compact-check"><input class="project-export-target" type="checkbox" value="premiere" checked /> ${t("enhance.project_premiere")}</label>
        <label class="check compact-check"><input class="project-export-target" type="checkbox" value="jianying" checked /> ${t("enhance.project_jianying")}</label>
      </div>
      <label class="check compact-check"><input id="project-export-include-clips" type="checkbox" /> ${t("enhance.project_include_clips")}</label>
      <button class="button" id="generate-project-export" type="button">${t("enhance.generate_project_export")}</button>
      ${projectExport ? `<div class="notice">${t("enhance.project_export_ready")}</div>` : ""}
      <div class="mini-list">
        ${exportFiles.map((file) => {
          const path = file.relative_path || file;
          const exists = files.has(path);
          return `<a class="mini-row ${exists ? "" : "disabled"}" ${exists ? `download href="${API.jobFileUrl(jobName, path, true)}"` : ""}>${escapeHtml(path)}</a>`;
        }).join("") || `<div class="empty">${t("enhance.no_project_export")}</div>`}
      </div>
    </article>
  `;
}

function projectExportFiles(projectExport) {
  const result = [];
  const exports = projectExport?.exports || {};
  Object.values(exports).forEach((entry) => {
    if (!entry || typeof entry !== "object") return;
    (entry.files || []).forEach((file) => result.push(file));
    (entry.clips || []).forEach((file) => result.push(file));
  });
  const seen = new Set();
  return result.filter((file) => {
    const key = file?.relative_path || file?.name || "";
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function bindEnhancementActions(root, jobName, reload) {
  const handler = async (event) => {
    const seekButton = event.target?.closest?.("[data-seek]");
    if (seekButton) {
      seekPreview(Number(seekButton.dataset.seek || 0));
      return;
    }
    if (event.target.id === "generate-segments") {
      await runEnhancement(event.target, () => API.generateSegments(jobName, { platforms: checkedPlatforms("segment") }), reload, "enhance.started");
    } else if (event.target.id === "generate-metadata") {
      await runEnhancement(event.target, () => API.generateMetadata(jobName, { platform: document.getElementById("metadata-platform")?.value || "douyin", force: true }), reload, "enhance.started");
    } else if (event.target.id === "save-metadata") {
      await runEnhancement(event.target, () => API.saveMetadata(jobName, parseMetadataEditor()), reload, "enhance.saved");
    } else if (event.target.id === "generate-highlights") {
      await runEnhancement(event.target, () => API.generateHighlights(jobName, { force: true }), reload, "enhance.started");
    } else if (event.target.id === "generate-publish-package") {
      await runEnhancement(event.target, () => API.generatePublishPackage(jobName, { platforms: checkedPlatforms("publish"), force: true }), reload, "enhance.started");
    } else if (event.target.id === "generate-project-export") {
      await runEnhancement(event.target, () => API.generateProjectExport(jobName, { targets: checkedProjectExportTargets(), include_clips: Boolean(document.getElementById("project-export-include-clips")?.checked), force: true }), reload, "enhance.started");
    }
  };
  root.addEventListener("click", handler);
  return () => root.removeEventListener("click", handler);
}

async function runEnhancement(button, action, reload, successKey) {
  button.disabled = true;
  try {
    await action();
    setEnhancementMessage(t(successKey));
    await reload();
  } catch (error) {
    setEnhancementMessage(escapeHtml(error.message), true);
  } finally {
    button.disabled = false;
  }
}

function checkedPlatforms(prefix) {
  return Array.from(document.querySelectorAll(`.${prefix}-platform`)).filter((input) => input.checked).map((input) => input.value);
}

function checkedProjectExportTargets() {
  return Array.from(document.querySelectorAll(".project-export-target")).filter((input) => input.checked).map((input) => input.value);
}

function metadataTemplate() {
  return {
    titles: [],
    descriptions: [],
    tags: [],
    hashtags: [],
    cover_titles: [],
    platform_notes: []
  };
}

function parseMetadataEditor() {
  try {
    return JSON.parse(document.getElementById("metadata-json")?.value || "{}");
  } catch (error) {
    throw new Error(`${t("enhance.invalid_json")} ${error.message}`);
  }
}

function setEnhancementMessage(message, isError = false) {
  const box = document.getElementById("enhancement-message");
  if (!box) return;
  box.innerHTML = `<div class="${isError ? "error" : "notice"}">${message}</div>`;
}

function renderStage(stage, job, files) {
  const complete = stageComplete(stage, job, files);
  const current = !complete && ((job.current_stage || stageForStatus(job.status)) === stage);
  const failed = job.status === "failed";
  return `<div class="stage ${failed ? "failed" : current ? "current" : complete ? "done" : ""}" title="${stage}">
    <div class="stage-dot">${failed ? "!" : complete ? "✓" : ""}</div>
    <div class="stage-label">${t(`stage.${stage}`)}</div>
  </div>`;
}

function stageComplete(stage, job, files) {
  if (job.status === "done") return true;
  const outputs = {
    probe: "manifest.json",
    extract_audio: "audio.wav",
    transcribe: "transcript.json",
    detect_silence: "silence.json",
    detect_freeze: "freeze.json",
    detect_scenes: "scene.json",
    plan_cuts: "cuts.json",
    style_subtitles: "subtitles.ass",
    plan_crop: "crop_plan.json",
    plan_uvr: "uvr_plan.json",
    plan_render: "render_preview.json",
    render_review: "review.mp4",
    render_final: "final.mp4"
  };
  return files.has(outputs[stage]);
}

function renderLiveProgress(job) {
  const percent = typeof job.stage_progress === "number" ? Math.round(job.stage_progress) : null;
  const message = job.stage_message || (job.current_stage ? `${job.current_stage} / ${job.status}` : job.status);
  const started = job.stage_started_at ? `${t("job.stage_started")}: ${escapeHtml(formatDate(job.stage_started_at))}` : "";
  return `
    <div class="live-progress-head">
      <div>
        <h2>${t("job.current_progress")}</h2>
        <p id="stage-progress-text" class="page-subtitle">${escapeHtml(message)}${started ? ` · ${started}` : ""}</p>
      </div>
      <span id="stage-progress-percent" class="badge ${statusGroup(job.status)}">${percent === null ? t(statusLabelKey(job.status)) : `${percent}%`}</span>
    </div>
    <div class="progress stage-progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percent ?? 0}" aria-label="${t("job.current_progress")}"><span id="stage-progress-fill" style="width: ${percent === null ? 0 : percent}%"></span></div>
  `;
}

function renderTimelineLegend() {
  const items = [
    ["keep", "timeline.keep"],
    ["invalid", "timeline.invalid"],
    ["scene", "timeline.scene"],
    ["transcript", "timeline.transcript"],
    ["waveform", "timeline.waveform"]
  ];
  return `<div class="timeline-legend">${items.map(([kind, key]) => `
    <span class="legend-item"><span class="legend-swatch ${kind}"></span>${t(key)}</span>
  `).join("")}</div>`;
}

function deriveLiveProgress(job) {
  if (typeof job.stage_progress === "number" && job.stage_progress >= 100) return;
  if (job.current_stage !== "transcribe" || !job.stage_started_at || !job.stage_estimate_seconds) return;
  const started = Date.parse(job.stage_started_at);
  if (!Number.isFinite(started)) return;
  const elapsed = Math.max(0, (Date.now() - started) / 1000);
  const estimate = Number(job.stage_estimate_seconds);
  if (!Number.isFinite(estimate) || estimate <= 0) return;
  job.stage_progress = Math.min(95, Math.max(0, elapsed / estimate * 100));
  job.stage_message = t("job.whisper_progress").replace("{elapsed}", String(Math.round(elapsed)));
}

function updateLiveStatus(job) {
  const badge = document.querySelector(".page-head .badge");
  if (badge) {
    badge.className = `badge ${statusGroup(job.status)}`;
    badge.textContent = t(statusLabelKey(job.status));
  }
  const percent = typeof job.stage_progress === "number" ? Math.round(job.stage_progress) : null;
  const message = job.stage_message || (job.current_stage ? `${job.current_stage} / ${job.status}` : job.status);
  const started = job.stage_started_at ? `${t("job.stage_started")}: ${formatDate(job.stage_started_at)}` : "";
  const text = document.getElementById("stage-progress-text");
  if (text) {
    text.textContent = `${message}${started ? ` · ${started}` : ""}`;
  }
  const percentBadge = document.getElementById("stage-progress-percent");
  if (percentBadge) {
    percentBadge.className = `badge ${statusGroup(job.status)}`;
    percentBadge.textContent = percent === null ? t(statusLabelKey(job.status)) : `${percent}%`;
  }
  const fill = document.getElementById("stage-progress-fill");
  if (fill && percent !== null) {
    fill.style.width = `${percent}%`;
  }
  const progressBar = document.querySelector(".stage-progress");
  if (progressBar) {
    progressBar.setAttribute("aria-valuenow", String(percent ?? 0));
  }
}

function renderTranscript(transcript) {
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

function renderClips(cuts) {
  const clips = cuts?.clips || [];
  if (!clips.length) return `<div class="empty">${t("job.no_clips")}</div>`;
  return `
    <div class="clip-toolbar">
      <button class="button" id="add-clip" type="button">${t("job.add_clip")}</button>
      <button class="button primary" id="save-cuts" type="button">${t("job.save_cuts")}</button>
      <div id="clip-editor-message"></div>
    </div>
    <table class="table clip-editor"><thead><tr><th>#</th><th>${t("job.keep")}</th><th>${t("job.start")}</th><th>${t("job.end")}</th><th>${t("common.duration")}</th><th>${t("job.score")}</th><th>${t("job.scenes")}</th><th>${t("job.reason")}</th><th>${t("job.content")}</th><th>${t("job.actions")}</th></tr></thead><tbody id="clip-editor-body">
    ${clips.map((clip, index) => renderClipRow(clip, index)).join("")}
  </tbody></table>`;
}

function renderClipRow(clip, index) {
  return `<tr data-clip-row>
    <td>${index + 1}</td>
    <td><label class="check" style="padding:4px;border:none;background:transparent;box-shadow:none;"><input type="checkbox" data-field="keep" ${clip.keep === false ? "" : "checked"} /></label></td>
    <td><input class="time-input" type="text" inputmode="text" data-field="start" value="${formatClipTimeInput(clip.start || 0)}" title="${t("job.time_format_hint")}" /></td>
    <td><input class="time-input" type="text" inputmode="text" data-field="end" value="${formatClipTimeInput(clip.end || 0)}" title="${t("job.time_format_hint")}" /></td>
    <td>${formatTime(clip.duration)}</td>
    <td><span class="badge optional">${clip.content_score ?? "-"}</span></td>
    <td>${clip.scene_count || 0}</td>
    <td><input class="reason-input" type="text" data-field="reason" value="${escapeHtml(clip.reason || "manual edit")}" /></td>
    <td><textarea class="content-input" data-field="content" data-original="${escapeHtml(clip.transcript_text || "")}" data-subtitle-override="${clip.subtitle_override ? "1" : ""}">${escapeHtml(clip.subtitle_text || clip.transcript_text || "")}</textarea></td>
    <td><button class="button compact-button danger" type="button" data-remove-clip>${t("common.delete")}</button></td>
  </tr>`;
}

function bindClipEditor(root, jobName, reload, setEditing) {
  const handler = async (e) => {
    if (e.target.id === "add-clip") {
      const body = document.getElementById("clip-editor-body");
      if (!body) return;
      setEditing(true);
      const rows = Array.from(body.querySelectorAll("[data-clip-row]"));
      const lastEnd = parseClipTime(rows.at(-1)?.querySelector('[data-field="end"]')?.value || "0");
      body.insertAdjacentHTML("beforeend", renderClipRow({ start: lastEnd, end: lastEnd + 5, duration: 5, reason: "manual edit" }, rows.length));
    } else if (e.target.id === "save-cuts") {
      const button = e.target;
      button.disabled = true;
      try {
        const clips = collectEditedClips(root);
        await API.updateCuts(jobName, clips);
        await API.rerunStage(jobName, "render_review");
        setClipMessage(t("job.save_cuts_preview"));
        await reload();
      } catch (error) {
        setClipMessage(`${t("job.save_cuts_failed")}${escapeHtml(error.message)}`, true);
      } finally {
        button.disabled = false;
      }
    } else if (e.target.closest("[data-remove-clip]")) {
      setEditing(true);
      e.target.closest("[data-clip-row]")?.remove();
    }
  };
  root.addEventListener("click", handler);
  return () => root.removeEventListener("click", handler);
}

function bindTranscriptEditor(root, jobName, reload, setEditing) {
  const handler = async (e) => {
    const seekButton = e.target?.closest?.("[data-seek]");
    if (seekButton) {
      seekPreview(Number(seekButton.dataset.seek || 0));
      return;
    }
    if (e.target.id !== "save-transcript") return;
    const button = e.target;
    button.disabled = true;
    try {
      await API.updateTranscript(jobName, collectEditedTranscript(root));
      setTranscriptMessage(t("job.save_transcript_preview"));
      await reload();
    } catch (error) {
      setTranscriptMessage(`${t("job.save_transcript_failed")}${escapeHtml(error.message)}`, true);
    } finally {
      button.disabled = false;
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

function seekPreview(seconds) {
  const video = document.querySelector("#section-preview video");
  if (!video || !Number.isFinite(seconds)) return;
  video.currentTime = Math.max(0, seconds);
  video.focus({ preventScroll: true });
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

function formatClipTimeInput(value) {
  const total = Math.max(0, Number(value) || 0);
  const whole = Math.floor(total);
  const minutes = Math.floor(whole / 60);
  const seconds = whole % 60;
  const fraction = total - whole;
  const secondsText = fraction > 0.0005 ? (seconds + fraction).toFixed(1).replace(/\.0$/, "") : String(seconds);
  return minutes > 0 ? `${minutes}分${secondsText}秒` : `${secondsText}秒`;
}

function parseClipTime(value) {
  const raw = String(value ?? "").trim();
  if (!raw) return NaN;
  const normalized = raw
    .replace(/[：]/g, ":")
    .replace(/\s+/g, "")
    .toLowerCase();
  const chinese = normalized.match(/^(?:(\d+(?:\.\d+)?)小时)?(?:(\d+(?:\.\d+)?)分)?(?:(\d+(?:\.\d+)?)秒?)?$/);
  if (chinese && (chinese[1] || chinese[2] || chinese[3])) {
    return (Number(chinese[1] || 0) * 3600) + (Number(chinese[2] || 0) * 60) + Number(chinese[3] || 0);
  }
  const compact = normalized.match(/^(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m)?(?:(\d+(?:\.\d+)?)s?)?$/);
  if (compact && (compact[1] || compact[2] || compact[3])) {
    return (Number(compact[1] || 0) * 3600) + (Number(compact[2] || 0) * 60) + Number(compact[3] || 0);
  }
  if (normalized.includes(":")) {
    const parts = normalized.split(":").map(Number);
    if (parts.length >= 2 && parts.length <= 3 && parts.every(Number.isFinite)) {
      return parts.reduce((total, part) => total * 60 + part, 0);
    }
  }
  return Number(normalized.replace(/秒$/, ""));
}

function setClipMessage(message, isError = false) {
  const box = document.getElementById("clip-editor-message");
  if (!box) return;
  box.innerHTML = `<div class="${isError ? "error" : "notice"}">${message}</div>`;
}

function renderDownloads(jobName, files) {
  const creator = ["final.mp4", "review.mp4", "cover_vertical.jpg", "cover_landscape.jpg", "subtitles_clipped.ass", "subtitles.ass", "audio_hq.flac"];
  const coverCandidates = Array.from(files.keys()).filter((name) => /^cover_(9x16|16x9)_\d+\.jpg$/i.test(name));
  const segmentFiles = Array.from(files.keys()).filter((name) => name.startsWith("segments/"));
  const projectExportFiles = Array.from(files.keys()).filter((name) => name.startsWith("project_exports/"));
  const advanced = ["thumbnail.jpg", ...coverCandidates, ...segmentFiles, ...projectExportFiles, "cover_manifest.json", "segments_manifest.json", "metadata.json", "highlights.json", "publish_package.json", "project_export_manifest.json", "waveform.json", "cuts.json", "cuts.md", "transcript.json", "transcript.srt", "crop_plan.json", "uvr_plan.json", "platform_export_plan.json", "bgm_mix_plan.json", "webhook_plan.json", "render_preview.json", "final_render_preview.json"];
  const link = (name, primary = false) => `<a class="button download-link ${fileKind(name)} ${primary ? "primary" : ""}" download href="${API.jobFileUrl(jobName, name, true)}">${fileIcon(name)} ${t("common.download")} ${name}</a>`;
  const mainLinks = creator.filter((name) => files.has(name)).map((name) => link(name, name === "final.mp4")).join("");
  const advancedLinks = advanced.filter((name) => files.has(name)).map((name) => link(name)).join("");
  if (!mainLinks && !advancedLinks) return `<div class="empty">${t("common.empty")}</div>`;
  return `
    <div class="download-group">
      <div class="download-primary">${mainLinks || `<div class="empty">${t("common.empty")}</div>`}</div>
      ${advancedLinks ? `<details class="download-advanced"><summary>${t("job.advanced_outputs")}</summary><div class="downloads">${advancedLinks}</div></details>` : ""}
    </div>
  `;
}

function fileKind(name) {
  if (/\.(mp4|mov|mkv|webm)$/i.test(name)) return "file-video";
  if (/\.(jpg|jpeg|png|webp)$/i.test(name)) return "file-image";
  if (/\.(ass|srt)$/i.test(name)) return "file-subtitle";
  if (/\.(wav|flac|mp3|m4a)$/i.test(name)) return "file-audio";
  return "file-data";
}

function fileIcon(name) {
  if (/\.(mp4|mov|mkv|webm)$/i.test(name)) return t("file.video");
  if (/\.(jpg|jpeg|png|webp)$/i.test(name)) return t("cover.image");
  if (/\.(ass|srt)$/i.test(name)) return t("file.subtitle");
  if (/\.(wav|flac|mp3|m4a)$/i.test(name)) return t("file.audio");
  return t("file.data");
}
