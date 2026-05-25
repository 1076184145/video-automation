import { API } from "./api.js";
import { t } from "./i18n.js";
import { basename, escapeHtml, jobName } from "./utils.js";

const options = [
  ["detect_silence", "new.detect_silence", true],
  ["detect_freeze", "new.detect_freeze", true],
  ["detect_scenes", "new.detect_scenes", true],
  ["plan_crop", "new.plan_crop", true],
  ["render_review", "new.render_review", false],
  ["render_final", "new.render_final", false],
  ["vertical", "new.vertical", false],
  ["burn_subtitles", "new.burn_subtitles", false],
  ["skip_transcribe", "new.skip_transcribe", false]
];
const UPLOAD_CONCURRENCY = 2;
const UPLOAD_PROGRESS_THROTTLE_MS = 160;
let batchPaths = [];
let downloadTimer = null;

export function renderNewJob() {
  batchPaths = [];
  if (downloadTimer) clearInterval(downloadTimer);
  downloadTimer = null;
  const app = document.getElementById("app");
  app.innerHTML = `
    <section class="page-head">
      <div>
        <h1 class="page-title">${t("new.title")}</h1>
        <p class="page-subtitle">${t("new.subtitle")}</p>
      </div>
    </section>
    <form class="panel form" id="new-job-form">
      <div class="upload-dropzone" id="upload-dropzone" tabindex="0" role="button" aria-label="${t("new.drop_aria")}">
        <div class="upload-icon">+</div>
        <div>
          <strong>${t("new.drop_title")}</strong>
          <p>${t("new.drop_note")}</p>
        </div>
        <input id="upload-file" type="file" accept="video/*,audio/*,.mp4,.mkv,.mov,.flv,.avi,.m4v,.webm,.mp3,.m4a,.wav" multiple hidden />
      </div>
      <div class="field">
        <label for="source-path">${t("new.source_path")}</label>
        <input id="source-path" type="text" placeholder="${t("new.placeholder")}" autocomplete="off" />
      </div>
      <div class="batch-box" id="batch-box"></div>
      <div class="download-box" id="download-box">
        <div class="field">
          <label for="download-url">${t("download.url")}</label>
          <div class="inline-input">
            <input id="download-url" type="text" placeholder="${t("download.placeholder")}" autocomplete="off" />
            <button class="button" id="start-download" type="button">${t("download.start")}</button>
          </div>
        </div>
        <div id="download-list"></div>
      </div>
      <div class="recording-picker" id="recording-picker">
        <div class="loading">${t("common.loading")}</div>
      </div>
      <div class="field">
        <label for="workflow-profile">${t("new.profile")}</label>
        <select id="workflow-profile">
          <option value="">${t("new.profile_custom")}</option>
          <option value="analysis">${t("new.profile_analysis")}</option>
          <option value="douyin">${t("new.profile_douyin")}</option>
          <option value="bilibili">${t("new.profile_bilibili")}</option>
          <option value="youtube_shorts">${t("new.profile_youtube_shorts")}</option>
        </select>
      </div>
      <div class="field">
        <label for="whisper-language">${t("new.whisper_language")}</label>
        <select id="whisper-language">
          <option value="Chinese">${t("new.language_chinese")}</option>
          <option value="English">${t("new.language_english")}</option>
          <option value="auto">${t("new.language_auto")}</option>
        </select>
      </div>
      <div class="options">${options.map(([name, key, checked]) => `
        <label class="check"><input type="checkbox" name="${name}" ${checked ? "checked" : ""} /> ${t(key)}</label>
      `).join("")}</div>
      <div id="form-error"></div>
      <button class="button primary" type="submit">${t("new.start")}</button>
    </form>
  `;
  document.getElementById("new-job-form").addEventListener("submit", submit);
  document.getElementById("workflow-profile").addEventListener("change", applyProfile);
  document.getElementById("source-path").addEventListener("input", () => {
    if (batchPaths.length <= 1) {
      batchPaths = [];
      renderBatchList();
    }
  });
  bindUploadDropzone();
  bindDownloadBox();
  renderBatchList();
  loadRecordings();
  loadDownloads();
  clearInterval(downloadTimer);
  downloadTimer = setInterval(loadDownloads, 2500);
  return () => clearInterval(downloadTimer);
}

function bindUploadDropzone() {
  const dropzone = document.getElementById("upload-dropzone");
  const input = document.getElementById("upload-file");
  if (!dropzone || !input) return;
  dropzone.addEventListener("click", () => input.click());
  dropzone.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      input.click();
    }
  });
  input.addEventListener("change", () => {
    const files = Array.from(input.files || []).filter(isMediaFile);
    if (files.length) uploadRecordings(files);
    input.value = "";
  });
  for (const eventName of ["dragenter", "dragover"]) {
    dropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropzone.classList.add("dragging");
    });
  }
  for (const eventName of ["dragleave", "drop"]) {
    dropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropzone.classList.remove("dragging");
    });
  }
  dropzone.addEventListener("drop", (event) => {
    const files = Array.from(event.dataTransfer?.files || []).filter(isMediaFile);
    if (!files.length) {
      setUploadMessage(t("new.drop_invalid"), true);
      return;
    }
    uploadRecordings(files);
  });
}

async function uploadRecording(file) {
  return uploadRecordings([file]);
}

async function uploadRecordings(files) {
  const dropzone = document.getElementById("upload-dropzone");
  if (!dropzone) return;
  const directPaths = [];
  const uploadFiles = [];
  for (const file of files) {
    const localPath = localPathFromFile(file);
    if (localPath) {
      directPaths.push(localPath);
    } else {
      uploadFiles.push(file);
    }
  }
  for (const path of directPaths) {
    addBatchPath(path);
    document.getElementById("source-path").value = path;
  }
  if (!uploadFiles.length) {
    setUploadMessage(`${t("new.batch_direct_added")} ${directPaths.length} ${t("new.batch_files")}`);
    return;
  }
  dropzone.classList.add("uploading");
  const uploaded = [];
  const totalBytes = uploadFiles.reduce((sum, file) => sum + (Number(file.size) || 0), 0);
  const progress = new Map(uploadFiles.map((file) => [file.name, 0]));
  let completed = 0;
  let lastRenderAt = 0;

  const renderProgress = (force = false) => {
    const now = Date.now();
    if (!force && now - lastRenderAt < UPLOAD_PROGRESS_THROTTLE_MS) return;
    lastRenderAt = now;
    const loadedBytes = uploadFiles.reduce((sum, file) => {
      const percent = progress.get(file.name) || 0;
      return sum + (Number(file.size) || 0) * percent / 100;
    }, 0);
    const percent = totalBytes > 0 ? loadedBytes / totalBytes * 100 : 0;
    setUploadMessage(uploadProgressHtml(`${completed}/${uploadFiles.length} ${t("new.batch_files")}`, percent));
  };

  try {
    renderProgress(true);
    await runWithConcurrency(uploadFiles, UPLOAD_CONCURRENCY, async (file) => {
      const payload = await API.uploadRecording(file, (percent) => {
        progress.set(file.name, percent);
        renderProgress(false);
      });
      if (payload.path) {
        uploaded.push(payload.path);
        addBatchPath(payload.path);
        document.getElementById("source-path").value = payload.path;
      }
      progress.set(file.name, 100);
      completed += 1;
      renderProgress(true);
    });
    const totalAdded = uploaded.length + directPaths.length;
    setUploadMessage(`${t("new.upload_done")} ${totalAdded} ${t("new.batch_files")}`);
    await loadRecordings();
  } catch (error) {
    setUploadMessage(`${t("new.upload_failed")}${escapeHtml(error.message)}`, true);
  } finally {
    dropzone.classList.remove("uploading");
  }
}

async function runWithConcurrency(items, limit, worker) {
  let cursor = 0;
  const count = Math.max(1, Math.min(limit, items.length));
  await Promise.all(Array.from({ length: count }, async () => {
    while (cursor < items.length) {
      const index = cursor;
      cursor += 1;
      await worker(items[index], index);
    }
  }));
}

function localPathFromFile(file) {
  const path = file?.path || file?.mozFullPath;
  if (typeof path === "string" && /^[A-Za-z]:[\\/]/.test(path)) return path;
  return "";
}

function addBatchPath(path) {
  const value = cleanSourcePath(path);
  if (!value) return;
  if (!batchPaths.includes(value)) batchPaths.push(value);
  renderBatchList();
}

function removeBatchPath(path) {
  batchPaths = batchPaths.filter((item) => item !== path);
  renderBatchList();
}

function renderBatchList() {
  const target = document.getElementById("batch-box");
  if (!target) return;
  if (!batchPaths.length) {
    target.innerHTML = "";
    return;
  }
  target.innerHTML = `
    <div class="batch-head">
      <strong>${t("new.batch_selected")} (${batchPaths.length})</strong>
      <button class="button compact-button" id="clear-batch" type="button">${t("new.batch_clear")}</button>
    </div>
    <div class="batch-list">
      ${batchPaths.map((path) => `
        <div class="batch-item">
          <span>${escapeHtml(basename(path))}</span>
          <button class="button compact-button" type="button" data-remove-batch="${escapeHtml(path)}">${t("new.batch_remove")}</button>
        </div>
      `).join("")}
    </div>
  `;
  target.querySelector("#clear-batch")?.addEventListener("click", () => {
    batchPaths = [];
    renderBatchList();
  });
  target.querySelectorAll("[data-remove-batch]").forEach((button) => {
    button.addEventListener("click", () => removeBatchPath(button.dataset.removeBatch || ""));
  });
}

function uploadProgressHtml(filename, percent) {
  const rounded = Math.round(percent);
  return `
    <div class="upload-progress-head">
      <span>${t("new.uploading")} ${escapeHtml(filename)}</span>
      <strong>${rounded}%</strong>
    </div>
    <div class="progress upload-progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${rounded}" aria-label="${t("new.upload_progress")}">
      <span style="width:${rounded}%"></span>
    </div>
  `;
}

function setUploadMessage(message, isError = false) {
  const errorBox = document.getElementById("form-error");
  if (!errorBox) return;
  errorBox.innerHTML = `<div class="${isError ? "error" : "notice"}">${message}</div>`;
}

function bindDownloadBox() {
  document.getElementById("start-download")?.addEventListener("click", async () => {
    const input = document.getElementById("download-url");
    const url = input?.value?.trim() || "";
    if (!url) return;
    const button = document.getElementById("start-download");
    button.disabled = true;
    try {
      await API.startDownload({ url });
      input.value = "";
      await loadDownloads();
    } catch (error) {
      setUploadMessage(`${t("download.failed")}${escapeHtml(error.message)}`, true);
    } finally {
      button.disabled = false;
    }
  });
}

async function loadDownloads() {
  const target = document.getElementById("download-list");
  if (!target) return;
  try {
    const state = await API.getDownloads();
    renderDownloadList(target, state);
  } catch (error) {
    target.innerHTML = `<div class="error">${escapeHtml(error.message)}</div>`;
  }
}

function renderDownloadList(target, state) {
  const downloads = state?.downloads || [];
  const disabled = state?.enabled === false;
  if (disabled) {
    target.innerHTML = `<div class="notice">${t("download.disabled")}</div>`;
    return;
  }
  if (!downloads.length) {
    target.innerHTML = `<div class="empty">${t("download.empty")}</div>`;
    return;
  }
  target.innerHTML = `
    <div class="download-queue">
      ${downloads.slice(0, 8).map((item) => `
        <div class="download-item">
          <div>
            <strong>${escapeHtml(item.output_name || item.url || item.id)}</strong>
            <small>${escapeHtml(item.status || "")} · ${Math.round(Number(item.progress || 0))}%</small>
          </div>
          ${item.status === "done" ? `<button class="button compact-button" type="button" data-import-download="${escapeHtml(item.id)}">${t("download.import")}</button>` : ""}
          ${item.error ? `<div class="error">${escapeHtml(item.error)}</div>` : ""}
        </div>
      `).join("")}
    </div>
  `;
  target.querySelectorAll("[data-import-download]").forEach((button) => {
    button.addEventListener("click", () => importDownload(button.dataset.importDownload || ""));
  });
}

async function importDownload(id) {
  const form = document.getElementById("new-job-form");
  if (!form || !id) return;
  const button = document.querySelector(`[data-import-download="${CSS.escape(id)}"]`);
  if (button) button.disabled = true;
  try {
    const result = await API.importDownload(id, collectJobOptions(form));
    location.hash = `#/jobs/${encodeURIComponent(jobName(result.job))}`;
  } catch (error) {
    setUploadMessage(`${t("download.import_failed")}${escapeHtml(error.message)}`, true);
    if (button) button.disabled = false;
  }
}

function isMediaFile(file) {
  return file && (
    file.type.startsWith("video/") ||
    file.type.startsWith("audio/") ||
    /\.(mp4|mkv|mov|flv|avi|m4v|webm|mp3|m4a|wav)$/i.test(file.name)
  );
}

async function loadRecordings() {
  const target = document.getElementById("recording-picker");
  if (!target) return;
  try {
    const recordings = await API.getRecordings();
    if (!recordings.length) {
      target.innerHTML = `<div class="empty">${t("new.no_recordings")}</div>`;
      return;
    }
    renderRecordingList(target, recordings, false);
  } catch (error) {
    target.innerHTML = `<div class="error">${escapeHtml(error.message)}</div>`;
  }
}

function renderRecordingList(target, recordings, showAll) {
  const visible = showAll ? recordings : recordings.slice(0, 12);
  target.innerHTML = `
    <div class="recording-head">${t("new.pick_recording")}</div>
    <div class="recording-list">
      ${visible.map((file) => `
        <button class="recording-item" type="button" data-path="${escapeHtml(file.path)}">
          <span>${escapeHtml(file.relative_path || basename(file.path))}</span>
          <small>${formatBytes(file.size_bytes)}</small>
        </button>
      `).join("")}
      ${!showAll && recordings.length > visible.length ? `<button class="button" id="show-all-recordings" type="button">${t("new.show_all_recordings")} (${recordings.length})</button>` : ""}
    </div>
  `;
  target.querySelectorAll("[data-path]").forEach((button) => {
    button.addEventListener("click", () => {
      document.getElementById("source-path").value = button.dataset.path || "";
      addBatchPath(button.dataset.path || "");
    });
  });
  document.getElementById("show-all-recordings")?.addEventListener("click", () => {
    renderRecordingList(target, recordings, true);
  });
}

function applyProfile(event) {
  const form = document.getElementById("new-job-form");
  const preset = event.currentTarget.value;
  const profiles = {
    analysis: { detect_silence: true, detect_freeze: true, detect_scenes: true, plan_crop: true },
    douyin: { detect_silence: true, detect_freeze: true, detect_scenes: true, plan_crop: true, render_review: true, render_final: true, vertical: true, burn_subtitles: true },
    bilibili: { detect_silence: true, detect_freeze: true, detect_scenes: true, plan_crop: true, render_review: true, render_final: true, vertical: false, burn_subtitles: true },
    youtube_shorts: { detect_silence: true, detect_freeze: true, detect_scenes: true, plan_crop: true, render_review: true, render_final: true, vertical: true, burn_subtitles: true }
  };
  const selected = profiles[preset];
  if (!form) return;
  for (const [name, , checked] of options) {
    if (!form.elements[name]) continue;
    form.elements[name].checked = selected && Object.prototype.hasOwnProperty.call(selected, name)
      ? Boolean(selected[name])
      : Boolean(checked);
  }
}

async function submit(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector('[type="submit"]');
  const path = cleanSourcePath(document.getElementById("source-path").value);
  const errorBox = document.getElementById("form-error");
  const paths = batchPaths.length ? batchPaths : (path ? [path] : []);
  if (!paths.length) {
    errorBox.innerHTML = `<div class="error">${t("new.path_required")}</div>`;
    return;
  }
  const basePayload = collectJobOptions(form);
  button.disabled = true;
  const originalText = button.textContent;
  button.textContent = t("common.loading");
  try {
    if (paths.length > 1) {
      await API.submitBatch({
        ...basePayload,
        items: paths.map((item) => ({ path: item }))
      });
      errorBox.innerHTML = `<div class="notice">${t("new.batch_submit_started")} ${paths.length} ${t("new.batch_files")}</div>`;
      location.hash = "#/";
    } else {
      const job = await API.submitJob({ ...basePayload, path: paths[0] });
      location.hash = `#/jobs/${encodeURIComponent(jobName(job))}`;
    }
  } catch (error) {
    errorBox.innerHTML = `<div class="error">${escapeHtml(error.message)}</div>`;
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
}

function collectJobOptions(form) {
  const payload = {
    profile: document.getElementById("workflow-profile").value,
    whisper_language: document.getElementById("whisper-language").value
  };
  for (const [name] of options) payload[name] = Boolean(form.elements[name]?.checked);
  return payload;
}

function cleanSourcePath(value) {
  let path = String(value || "").trim();
  while (path.length >= 2 && path[0] === path[path.length - 1] && (path[0] === "\"" || path[0] === "'")) {
    path = path.slice(1, -1).trim();
  }
  return path;
}

function formatBytes(value) {
  const bytes = Number(value) || 0;
  if (bytes >= 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`;
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${Math.round(bytes / 1024)} KB`;
}
