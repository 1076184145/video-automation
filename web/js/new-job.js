import { API } from "./api.js";
import { legacyProfilesToRecipes } from "./automation.js";
import { t } from "./i18n.js";
import { setButtonLoading, showToast } from "./toast.js";
import { basename, escapeHtml, jobName } from "./utils.js";

const options = [
  ["source_integrity_scan", "new.source_integrity_scan", true],
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
const CUSTOM_PROFILE_STORAGE_KEY = "videoAutomationCustomProfiles";
const CUSTOM_PROFILE_BACKUP_KEY = "videoAutomationCustomProfiles.migratedBackup";
const NEW_JOB_DISCLOSURE_STORAGE_KEY = "videoAutomationNewJobDisclosures";
const BUILTIN_PROFILES = {
  fast: { source_integrity_scan: false, detect_silence: true, detect_freeze: false, detect_scenes: true, plan_crop: false, render_review: false, render_final: true, vertical: false, burn_subtitles: true },
  analysis: { source_integrity_scan: true, detect_silence: true, detect_freeze: true, detect_scenes: true, plan_crop: true },
  douyin: { source_integrity_scan: true, detect_silence: true, detect_freeze: true, detect_scenes: true, plan_crop: true, render_review: false, render_final: true, vertical: true, burn_subtitles: true },
  bilibili: { source_integrity_scan: true, detect_silence: true, detect_freeze: true, detect_scenes: true, plan_crop: true, render_review: false, render_final: true, vertical: false, burn_subtitles: true },
  youtube_shorts: { source_integrity_scan: true, detect_silence: true, detect_freeze: true, detect_scenes: true, plan_crop: true, render_review: false, render_final: true, vertical: true, burn_subtitles: true }
};
const UPLOAD_CONCURRENCY = 2;
const UPLOAD_PROGRESS_THROTTLE_MS = 160;
const BATCH_PATH_LIMIT = 30;
const UPLOAD_FILE_LIMIT = 8;
const UPLOAD_TOTAL_LIMIT_BYTES = 20 * 1024 * 1024 * 1024;
const BROWSER_UPLOAD_CONFIRM_BYTES = 512 * 1024 * 1024;
let batchPaths = [];
let sourcePathBatchMirror = false;
let serverRecipes = [];

export async function renderNewJob(_match, { signal } = {}) {
  batchPaths = [];
  sourcePathBatchMirror = false;
  const app = document.getElementById("app");
  const library = await loadLibraryOptions(signal);
  serverRecipes = library.recipes || [];
  app.innerHTML = renderNewJobFormForTest(loadNewJobDisclosureState(), library);
  document.getElementById("new-job-form").addEventListener("submit", submit);
  document.getElementById("workflow-profile").addEventListener("change", applyProfile);
  document.getElementById("save-current-profile").addEventListener("click", saveCurrentProfile);
  document.getElementById("delete-current-profile").addEventListener("click", deleteCurrentProfile);
  document.getElementById("source-path").addEventListener("input", () => {
    if (sourcePathBatchMirror && batchPaths.length <= 1) {
      batchPaths = [];
      sourcePathBatchMirror = false;
      renderBatchList();
    }
    updateWizardSummary();
  });
  document.getElementById("source-path").addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.isComposing) return;
    event.preventDefault();
    addCurrentSourceToBatch();
  });
  document.getElementById("add-source-to-batch").addEventListener("click", addCurrentSourceToBatch);
  document.getElementById("whisper-language").addEventListener("change", updateWizardSummary);
  document.getElementById("project-id")?.addEventListener("change", applyProjectDefaultKit);
  document.querySelectorAll(".options input").forEach((input) => input.addEventListener("change", updateWizardSummary));
  bindWizardRail();
  bindNewJobDisclosures();
  bindUploadDropzone();
  renderBatchList();
  selectProjectFromHash();
  updateWizardSummary();
  loadRecordings();
  const handleVisibility = () => {
    if (document.visibilityState === "visible") {
      loadRecordings();
    }
  };
  document.addEventListener("visibilitychange", handleVisibility);
  return () => {
    document.removeEventListener("visibilitychange", handleVisibility);
  };
}

export function renderNewJobFormForTest(disclosures = {}, library = {}) {
  const sourceToolsOpen = disclosures.sourceTools ? " open" : "";
  const processingOptionsOpen = disclosures.processingOptions ? " open" : "";
  const projects = Array.isArray(library.projects) ? library.projects : [];
  const kits = Array.isArray(library.kits) ? library.kits : [];
  const recipes = Array.isArray(library.recipes) ? library.recipes : [];
  return `
    <section class="page-head">
      <div>
        <h1 class="page-title">${t("new.title")}</h1>
        <p class="page-subtitle">${t("new.subtitle")}</p>
      </div>
    </section>
    <form class="panel form" id="new-job-form">
      ${renderWizardRail()}
      <div class="new-job-wizard">
        <section class="wizard-step-card" id="new-step-source">
          <div class="wizard-step-head">
            <span class="wizard-step-index">01</span>
            <div>
              <h2>${t("new.wizard_source_title")}</h2>
              <p>${t("new.wizard_source_note")}</p>
            </div>
          </div>
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
            <div class="inline-input source-path-input">
              <input id="source-path" type="text" placeholder="${t("new.placeholder")}" autocomplete="off" />
              <button class="button" id="add-source-to-batch" type="button">${t("new.add_to_batch")}</button>
            </div>
          </div>
          <div class="batch-box" id="batch-box"></div>
          <details class="new-job-disclosure source-tools" id="new-job-source-tools"${sourceToolsOpen}>
            <summary>
              <span>
                <strong>${t("new.more_sources_title")}</strong>
                <small>${t("new.more_sources_note")}</small>
              </span>
              <span class="disclosure-chevron" aria-hidden="true">⌄</span>
            </summary>
            <div class="new-job-disclosure-body">
              <div class="recording-picker" id="recording-picker">
                <div class="loading">${t("common.loading")}</div>
              </div>
            </div>
          </details>
        </section>
        <section class="wizard-step-card" id="new-step-goal">
          <div class="wizard-step-head">
            <span class="wizard-step-index">02</span>
            <div>
              <h2>${t("new.wizard_goal_title")}</h2>
              <p>${t("new.wizard_goal_note")}</p>
            </div>
          </div>
          <div class="form-row library-context-fields">
            <div class="field">
              <label for="project-id">${t("new.project")}</label>
              <select id="project-id">
                <option value="">${t("new.project_none")}</option>
                ${projects.map((project) => `<option value="${escapeHtml(project.id)}" data-default-kit="${escapeHtml(project.default_kit_id || "")}">${escapeHtml(project.name)}</option>`).join("")}
              </select>
            </div>
            <div class="field">
              <label for="creator-kit-id">${t("new.creator_kit")}</label>
              <select id="creator-kit-id">
                <option value="">${t("new.creator_kit_auto")}</option>
                ${kits.map((kit) => `<option value="${escapeHtml(kit.id)}">${escapeHtml(kit.name)} · ${escapeHtml(kit.aspect || "—")}</option>`).join("")}
              </select>
            </div>
          </div>
          <div class="form-row">
            <div class="field">
              <label for="workflow-profile">${t("new.profile")}</label>
              <select id="workflow-profile">
                ${renderProfileOptions(recipes)}
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
          </div>
          <details class="new-job-disclosure processing-options" id="new-job-processing-options"${processingOptionsOpen}>
            <summary>
              <span>
                <strong>${t("new.advanced_options_title")}</strong>
                <small>${t("new.advanced_options_note")}</small>
              </span>
              <span class="disclosure-chevron" aria-hidden="true">⌄</span>
            </summary>
            <div class="new-job-disclosure-body">
              <div class="profile-actions">
                <button class="button compact-button" id="save-current-profile" type="button">${t("new.profile_save")}</button>
                <button class="button compact-button danger" id="delete-current-profile" type="button">${t("new.profile_delete")}</button>
              </div>
              <div class="field">
                <label>${t("new.wizard_options_title")}</label>
                <div class="options">${options.map(([name, key, checked]) => `
                  <label class="check"><input type="checkbox" name="${name}" ${checked ? "checked" : ""} /> ${t(key)}</label>
                `).join("")}</div>
              </div>
              <div class="notice feature-hint">
                <strong>${t("cover.title")}</strong>
                <p>${t("new.cover_hint")}</p>
              </div>
            </div>
          </details>
        </section>
        <section class="wizard-step-card wizard-step-run" id="new-step-run">
          <div class="wizard-step-head">
            <span class="wizard-step-index">03</span>
            <div>
              <h2>${t("new.wizard_run_title")}</h2>
              <p>${t("new.wizard_run_note")}</p>
            </div>
          </div>
          <div class="wizard-summary-grid" id="new-job-summary"></div>
          <div id="form-error"></div>
          <button class="button primary" type="submit">${t("new.start")}</button>
        </section>
      </div>
    </form>
  `;
}

function renderWizardRail() {
  const steps = [
    ["new-step-source", "01", "new.wizard_source_title"],
    ["new-step-goal", "02", "new.wizard_goal_title"],
    ["new-step-run", "03", "new.wizard_run_title"]
  ];
  return `
    <nav class="wizard-rail" aria-label="${t("new.wizard_nav")}">
      ${steps.map(([id, number, key], index) => `
        <button class="wizard-rail-item ${index === 0 ? "active" : ""}" type="button" data-wizard-target="${id}">
          <span>${number}</span>
          <strong>${t(key)}</strong>
        </button>
      `).join("")}
    </nav>
  `;
}

function loadNewJobDisclosureState() {
  try {
    const value = JSON.parse(localStorage.getItem(NEW_JOB_DISCLOSURE_STORAGE_KEY) || "{}");
    return {
      sourceTools: Boolean(value?.sourceTools),
      processingOptions: Boolean(value?.processingOptions),
    };
  } catch {
    return { sourceTools: false, processingOptions: false };
  }
}

function bindNewJobDisclosures() {
  const sourceTools = document.getElementById("new-job-source-tools");
  const processingOptions = document.getElementById("new-job-processing-options");
  const save = () => {
    try {
      localStorage.setItem(NEW_JOB_DISCLOSURE_STORAGE_KEY, JSON.stringify({
        sourceTools: Boolean(sourceTools?.open),
        processingOptions: Boolean(processingOptions?.open),
      }));
    } catch {
      // Storage is optional; the page remains fully usable without persistence.
    }
  };
  sourceTools?.addEventListener("toggle", save);
  processingOptions?.addEventListener("toggle", save);
}

function bindWizardRail() {
  document.querySelectorAll("[data-wizard-target]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll("[data-wizard-target]").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      document.getElementById(button.dataset.wizardTarget || "")?.scrollIntoView({
        behavior: "smooth",
        block: "start"
      });
    });
  });
}

function updateWizardSummary() {
  const target = document.getElementById("new-job-summary");
  const form = document.getElementById("new-job-form");
  if (!target || !form) return;
  const sourcePath = cleanSourcePath(document.getElementById("source-path")?.value || "");
  const sourceText = batchPaths.length
    ? `${t("new.wizard_batch_source")} ${batchPaths.length} ${t("new.batch_files")}`
    : sourcePath
      ? `${t("new.wizard_single_source")} ${basename(sourcePath)}`
      : t("new.wizard_no_source");
  const profileText = selectedOptionText("workflow-profile") || t("new.profile_custom");
  const languageText = selectedOptionText("whisper-language") || t("new.language_auto");
  const outputs = selectedOutputLabels(form);
  target.innerHTML = `
    ${summaryCard(t("new.wizard_source_summary"), sourceText)}
    ${summaryCard(t("new.wizard_profile_summary"), profileText)}
    ${summaryCard(t("new.wizard_language_summary"), languageText)}
    ${summaryCard(t("new.wizard_outputs_summary"), outputs.length ? outputs.join(" / ") : t("new.wizard_outputs_none"))}
  `;
}

function summaryCard(label, value) {
  return `
    <div class="wizard-summary-card">
      <span>${label}</span>
      <strong>${escapeHtml(value)}</strong>
    </div>
  `;
}

function selectedOptionText(id) {
  const select = document.getElementById(id);
  return select?.selectedOptions?.[0]?.textContent?.trim() || "";
}

function selectedOutputLabels(form) {
  return options
    .filter(([name]) => Boolean(form.elements[name]?.checked))
    .map(([, key]) => t(key));
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

async function uploadRecordings(files, options = {}) {
  const dropzone = document.getElementById("upload-dropzone");
  if (!dropzone) return;
  if (files.length > UPLOAD_FILE_LIMIT) {
    setUploadMessage(t("new.upload_too_many").replace("{count}", String(UPLOAD_FILE_LIMIT)), true);
    return;
  }
  const totalInputBytes = files.reduce((sum, file) => sum + (Number(file.size) || 0), 0);
  if (UPLOAD_TOTAL_LIMIT_BYTES > 0 && totalInputBytes > UPLOAD_TOTAL_LIMIT_BYTES) {
    setUploadMessage(
      t("new.upload_too_large")
        .replace("{limit}", formatBytes(UPLOAD_TOTAL_LIMIT_BYTES))
        .replace("{size}", formatBytes(totalInputBytes)),
      true
    );
    return;
  }
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
  const projectedBatch = new Set(batchPaths);
  for (const path of directPaths) projectedBatch.add(cleanSourcePath(path));
  const projectedCount = projectedBatch.size + uploadFiles.length;
  if (projectedCount > BATCH_PATH_LIMIT) {
    setUploadMessage(t("new.batch_limit").replace("{count}", String(BATCH_PATH_LIMIT)), true);
    return;
  }
  for (const path of directPaths) {
    addBatchPath(path);
    const input = document.getElementById("source-path");
    if (input) {
      input.value = path;
      sourcePathBatchMirror = true;
    }
  }
  if (!uploadFiles.length) {
    setUploadMessage(`${t("new.batch_direct_added")} ${directPaths.length} ${t("new.batch_files")}`);
    return;
  }
  if (shouldConfirmBrowserUploadForTest(files) && !options.confirmBrowserUpload) {
    setBrowserUploadConfirmation(uploadFiles, () => uploadRecordings(files, { confirmBrowserUpload: true }));
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
        const input = document.getElementById("source-path");
        if (input) {
          input.value = payload.path;
          sourcePathBatchMirror = true;
        }
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
  if (typeof path === "string" && (/^[A-Za-z]:[\\/]/.test(path) || path.startsWith("\\\\"))) {
    return cleanSourcePath(path);
  }
  return "";
}

export function shouldConfirmBrowserUploadForTest(files) {
  const items = Array.from(files || []).filter(Boolean);
  if (!items.length) return false;
  const filesToCopy = items.filter((file) => !localPathFromFile(file));
  if (!filesToCopy.length) return false;
  const copyBytes = filesToCopy.reduce((sum, file) => sum + (Number(file.size) || 0), 0);
  return copyBytes >= BROWSER_UPLOAD_CONFIRM_BYTES;
}

export function localPathFromFileForTest(file) {
  return localPathFromFile(file);
}

function setBrowserUploadConfirmation(files, onConfirm) {
  const totalBytes = files.reduce((sum, file) => sum + (Number(file.size) || 0), 0);
  setUploadMessage(`
    <div class="upload-confirm">
      <strong>${t("new.browser_upload_confirm_title")}</strong>
      <p>${t("new.browser_upload_confirm_note")
        .replace("{count}", String(files.length))
        .replace("{size}", formatBytes(totalBytes))}</p>
      <p>${t("new.browser_upload_path_tip")}</p>
      <button class="button compact-button" id="confirm-browser-upload" type="button">${t("new.browser_upload_confirm_action")}</button>
    </div>
  `);
  document.getElementById("confirm-browser-upload")?.addEventListener("click", onConfirm, { once: true });
}

function addBatchPath(path) {
  const value = cleanSourcePath(path);
  if (!value) return false;
  if (!batchPaths.includes(value)) {
    if (batchPaths.length >= BATCH_PATH_LIMIT) {
      setUploadMessage(t("new.batch_limit").replace("{count}", String(BATCH_PATH_LIMIT)), true);
      return false;
    }
    batchPaths.push(value);
  }
  renderBatchList();
  updateWizardSummary();
  return true;
}

function addCurrentSourceToBatch() {
  const input = document.getElementById("source-path");
  const value = cleanSourcePath(input?.value || "");
  if (!value) {
    setUploadMessage(t("new.path_required"), true);
    return;
  }
  addBatchPath(value);
  sourcePathBatchMirror = false;
  if (input) input.value = "";
  setUploadMessage(`${t("new.batch_direct_added")} 1 ${t("new.batch_files")}`);
}

function removeBatchPath(path) {
  batchPaths = batchPaths.filter((item) => item !== path);
  renderBatchList();
  updateWizardSummary();
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
      <small>${t("new.batch_limit_hint").replace("{count}", String(BATCH_PATH_LIMIT))}</small>
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
    updateWizardSummary();
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
      sourcePathBatchMirror = true;
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
  const selected = getProfilePayload(preset);
  if (!form) return;
  if (selected?.whisper_language) document.getElementById("whisper-language").value = selected.whisper_language;
  for (const [name, , checked] of options) {
    if (!form.elements[name]) continue;
    form.elements[name].checked = selected && Object.prototype.hasOwnProperty.call(selected, name)
      ? Boolean(selected[name])
      : Boolean(checked);
  }
  updateWizardSummary();
}

function renderProfileOptions(recipes = serverRecipes) {
  const customProfiles = loadCustomProfiles();
  return `
    <option value="">${t("new.profile_custom")}</option>
    <optgroup label="${t("new.profile_builtin_group")}">
      <option value="fast">${t("new.profile_fast")}</option>
      <option value="analysis">${t("new.profile_analysis")}</option>
      <option value="douyin">${t("new.profile_douyin")}</option>
      <option value="bilibili">${t("new.profile_bilibili")}</option>
      <option value="youtube_shorts">${t("new.profile_youtube_shorts")}</option>
    </optgroup>
    ${recipes.length ? `<optgroup label="${t("new.recipe_server_group")}">
      ${recipes.map((recipe) => `<option value="recipe:${escapeHtml(recipe.id)}">${escapeHtml(recipe.name)}</option>`).join("")}
    </optgroup>` : ""}
    ${customProfiles.length ? `<optgroup label="${t("new.profile_custom_group")}">
      ${customProfiles.map((profile) => `<option value="custom:${escapeHtml(profile.id)}">${escapeHtml(profile.name)}</option>`).join("")}
    </optgroup>` : ""}
  `;
}

function getProfilePayload(value) {
  if (!value) return null;
  if (value.startsWith("custom:")) {
    const id = value.slice("custom:".length);
    return loadCustomProfiles().find((profile) => profile.id === id)?.payload || null;
  }
  if (value.startsWith("recipe:")) {
    const id = value.slice("recipe:".length);
    return serverRecipes.find((recipe) => recipe.id === id)?.options || null;
  }
  return BUILTIN_PROFILES[value] || null;
}

export function builtInProfileForTest(value) {
  return { ...(BUILTIN_PROFILES[value] || {}) };
}

function loadCustomProfiles() {
  try {
    const parsed = JSON.parse(localStorage.getItem(CUSTOM_PROFILE_STORAGE_KEY) || "[]");
    return Array.isArray(parsed)
      ? parsed.filter((item) => item && typeof item.id === "string" && typeof item.name === "string" && item.payload)
      : [];
  } catch {
    return [];
  }
}

function storeCustomProfiles(profiles) {
  localStorage.setItem(CUSTOM_PROFILE_STORAGE_KEY, JSON.stringify(profiles.slice(0, 30)));
}

function currentProfilePayload(form) {
  const payload = { whisper_language: document.getElementById("whisper-language").value };
  for (const [name] of options) payload[name] = Boolean(form.elements[name]?.checked);
  return payload;
}

function refreshProfileSelect(selectedValue = "") {
  const select = document.getElementById("workflow-profile");
  if (!select) return;
  select.innerHTML = renderProfileOptions();
  select.value = selectedValue;
  updateWizardSummary();
}

async function saveCurrentProfile() {
  const form = document.getElementById("new-job-form");
  if (!form) return;
  const name = window.prompt(t("new.profile_name_prompt"));
  if (!name?.trim()) return;
  const legacy = [{
    id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
    name: name.trim().slice(0, 40),
    payload: currentProfilePayload(form),
  }];
  try {
    const recipe = await API.createRecipe(legacyProfilesToRecipes(legacy)[0]);
    serverRecipes = [recipe, ...serverRecipes.filter((item) => item.id !== recipe.id)];
    refreshProfileSelect(`recipe:${recipe.id}`);
    showToast(t("new.profile_saved"), "success");
  } catch (error) {
    showToast(`${t("new.profile_save_failed")} ${error.message}`, "error");
  }
}

async function deleteCurrentProfile() {
  const select = document.getElementById("workflow-profile");
  if (!select?.value?.startsWith("custom:") && !select?.value?.startsWith("recipe:")) return;
  if (!window.confirm(t("new.profile_delete_confirm"))) return;
  if (select.value.startsWith("recipe:")) {
    const id = select.value.slice("recipe:".length);
    try {
      await API.deleteRecipe(id);
      serverRecipes = serverRecipes.filter((recipe) => recipe.id !== id);
      refreshProfileSelect("");
      applyProfile({ currentTarget: { value: "" } });
      showToast(t("new.profile_deleted"), "success");
    } catch (error) {
      showToast(`${t("new.profile_delete_failed")} ${error.message}`, "error");
    }
    return;
  }
  const id = select.value.slice("custom:".length);
  storeCustomProfiles(loadCustomProfiles().filter((profile) => profile.id !== id));
  refreshProfileSelect("");
  applyProfile({ currentTarget: { value: "" } });
  showToast(t("new.profile_deleted"), "success");
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
  if (paths.length > BATCH_PATH_LIMIT) {
    errorBox.innerHTML = `<div class="error">${t("new.batch_limit").replace("{count}", String(BATCH_PATH_LIMIT))}</div>`;
    return;
  }
  const basePayload = collectJobOptions(form);
  setButtonLoading(button, true, t("common.loading"));
  try {
    if (paths.length > 1) {
      await API.submitBatch({
        ...basePayload,
        items: paths.map((item) => ({ path: item }))
      });
      errorBox.innerHTML = `<div class="notice">${t("new.batch_submit_started")} ${paths.length} ${t("new.batch_files")}</div>`;
      showToast(`${t("new.batch_submit_started")} ${paths.length} ${t("new.batch_files")}`, "success");
      location.hash = "#/";
    } else {
      const job = await API.submitJob({ ...basePayload, path: paths[0] });
      location.hash = `#/jobs/${encodeURIComponent(jobName(job))}`;
    }
  } catch (error) {
    errorBox.innerHTML = `<div class="error">${escapeHtml(error.message)}</div>`;
    showToast(error.message, "error");
  } finally {
    setButtonLoading(button, false);
  }
}

function collectJobOptions(form) {
  const selectedProfile = document.getElementById("workflow-profile").value;
  const payload = {
    profile: selectedProfile.startsWith("custom:") || selectedProfile.startsWith("recipe:") ? "" : selectedProfile,
    recipe_id: selectedProfile.startsWith("recipe:") ? selectedProfile.slice("recipe:".length) : "",
    whisper_language: document.getElementById("whisper-language").value,
    project_id: document.getElementById("project-id")?.value || "",
    creator_kit_id: document.getElementById("creator-kit-id")?.value || ""
  };
  for (const [name] of options) payload[name] = Boolean(form.elements[name]?.checked);
  return payload;
}

async function loadLibraryOptions(signal) {
  try {
    const [projects, kits, recipesResponse] = await Promise.all([
      API.getProjects({ signal }),
      API.getCreatorKits({ signal }),
      API.getRecipes({ signal }),
    ]);
    let recipes = recipesResponse.items || [];
    const legacyProfiles = loadCustomProfiles();
    if (legacyProfiles.length) {
      try {
        const imported = await API.importRecipes(legacyProfilesToRecipes(legacyProfiles));
        const byId = new Map([...recipes, ...(imported.items || [])].map((recipe) => [recipe.id, recipe]));
        recipes = [...byId.values()];
        const raw = localStorage.getItem(CUSTOM_PROFILE_STORAGE_KEY);
        if (raw) localStorage.setItem(CUSTOM_PROFILE_BACKUP_KEY, raw);
        localStorage.removeItem(CUSTOM_PROFILE_STORAGE_KEY);
      } catch {
        // Keep the browser profiles intact; the next visit retries migration.
      }
    }
    return { projects: projects.items || [], kits: kits.items || [], recipes };
  } catch {
    return { projects: [], kits: [], recipes: [] };
  }
}

function applyProjectDefaultKit() {
  const projectSelect = document.getElementById("project-id");
  const kitSelect = document.getElementById("creator-kit-id");
  const defaultKit = projectSelect?.selectedOptions?.[0]?.dataset.defaultKit || "";
  if (kitSelect && defaultKit && Array.from(kitSelect.options).some((option) => option.value === defaultKit)) {
    kitSelect.value = defaultKit;
  }
  updateWizardSummary();
}

function selectProjectFromHash() {
  const query = String(location.hash || "").split("?", 2)[1] || "";
  const projectId = new URLSearchParams(query).get("project") || "";
  const select = document.getElementById("project-id");
  if (!select || !Array.from(select.options).some((option) => option.value === projectId)) return;
  select.value = projectId;
  applyProjectDefaultKit();
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
