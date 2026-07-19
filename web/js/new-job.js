import { API, isAbortError } from "./api.js";
import { legacyProfilesToRecipes } from "./automation.js";
import { t } from "./i18n.js";
import {
  NEW_JOB_OPTIONS,
  batchListHtml,
  recordingListHtml,
  renderNewJobForm,
  summaryCard,
  uploadProgressHtml,
} from "./new-job-view.js";
import { setButtonLoading, showToast } from "./toast.js";
import { basename, escapeHtml, jobName } from "./utils.js";

// New-job wizard controller: owns batch/upload state, profile logic, and all
// event wiring. Markup lives in new-job-view.js; this module only reads and
// drives the DOM.

const options = NEW_JOB_OPTIONS;
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
export async function renderNewJob(_match, { signal } = {}) {
  const controller = new AbortController();
  const abortSession = () => controller.abort(signal?.reason);
  signal?.addEventListener("abort", abortSession, { once: true });
  const state = {
    batchPaths: [],
    sourcePathBatchMirror: false,
    serverRecipes: [],
    disposed: false,
    uploadInProgress: false,
    recordingLoadVersion: 0,
    signal: controller.signal,
  };
  const app = document.getElementById("app");
  const library = await loadLibraryOptions(state.signal);
  state.signal.throwIfAborted();
  state.serverRecipes = library.recipes || [];
  app.innerHTML = renderNewJobFormForTest(loadNewJobDisclosureState(), library);
  document.getElementById("new-job-form").addEventListener("submit", (event) => submit(event, state));
  document.getElementById("workflow-profile").addEventListener("change", (event) => applyProfile(event, state));
  document.getElementById("save-current-profile").addEventListener("click", () => saveCurrentProfile(state));
  document.getElementById("delete-current-profile").addEventListener("click", () => deleteCurrentProfile(state));
  document.getElementById("source-path").addEventListener("input", () => {
    if (state.sourcePathBatchMirror && state.batchPaths.length <= 1) {
      state.batchPaths = [];
      state.sourcePathBatchMirror = false;
      renderBatchList(state);
    }
    updateWizardSummary(state);
  });
  document.getElementById("source-path").addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.isComposing) return;
    event.preventDefault();
    addCurrentSourceToBatch(state);
  });
  document.getElementById("add-source-to-batch").addEventListener("click", () => addCurrentSourceToBatch(state));
  document.getElementById("whisper-language").addEventListener("change", () => updateWizardSummary(state));
  document.getElementById("project-id")?.addEventListener("change", () => applyProjectDefaultKit(state));
  document.querySelectorAll(".options input").forEach((input) => input.addEventListener("change", () => updateWizardSummary(state)));
  bindWizardRail();
  bindNewJobDisclosures();
  bindUploadDropzone(state);
  renderBatchList(state);
  selectProjectFromHash(state);
  updateWizardSummary(state);
  loadRecordings(state);
  const handleVisibility = () => {
    if (document.visibilityState === "visible") {
      loadRecordings(state);
    }
  };
  document.addEventListener("visibilitychange", handleVisibility);
  return () => {
    state.disposed = true;
    state.recordingLoadVersion += 1;
    controller.abort();
    signal?.removeEventListener("abort", abortSession);
    document.removeEventListener("visibilitychange", handleVisibility);
  };
}

export function renderNewJobFormForTest(disclosures = {}, library = {}) {
  const recipes = Array.isArray(library.recipes) ? library.recipes : [];
  return renderNewJobForm(disclosures, library, renderProfileOptions(recipes));
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

function updateWizardSummary(state) {
  const target = document.getElementById("new-job-summary");
  const form = document.getElementById("new-job-form");
  if (!target || !form) return;
  const sourcePath = cleanSourcePath(document.getElementById("source-path")?.value || "");
  const sourceText = state.batchPaths.length
    ? `${t("new.wizard_batch_source")} ${state.batchPaths.length} ${t("new.batch_files")}`
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

function selectedOptionText(id) {
  const select = document.getElementById(id);
  return select?.selectedOptions?.[0]?.textContent?.trim() || "";
}

function selectedOutputLabels(form) {
  return options
    .filter(([name]) => Boolean(form.elements[name]?.checked))
    .map(([, key]) => t(key));
}

function bindUploadDropzone(state) {
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
    if (files.length) uploadRecordings(files, state);
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
    uploadRecordings(files, state);
  });
}

async function uploadRecordings(files, state, options = {}) {
  const dropzone = document.getElementById("upload-dropzone");
  if (!dropzone || state.disposed || state.signal.aborted) return;
  if (state.uploadInProgress) {
    setUploadMessage(t("new.upload_in_progress"), true);
    return;
  }
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
  const projectedBatch = new Set(state.batchPaths);
  for (const path of directPaths) projectedBatch.add(cleanSourcePath(path));
  const projectedCount = projectedBatch.size + uploadFiles.length;
  if (projectedCount > BATCH_PATH_LIMIT) {
    setUploadMessage(t("new.batch_limit").replace("{count}", String(BATCH_PATH_LIMIT)), true);
    return;
  }
  for (const path of directPaths) {
    addBatchPath(state, path);
    const input = document.getElementById("source-path");
    if (input) {
      input.value = path;
      state.sourcePathBatchMirror = true;
    }
  }
  if (!uploadFiles.length) {
    setUploadMessage(`${t("new.batch_direct_added")} ${directPaths.length} ${t("new.batch_files")}`);
    return;
  }
  if (shouldConfirmBrowserUploadForTest(files) && !options.confirmBrowserUpload) {
    setBrowserUploadConfirmation(uploadFiles, () => uploadRecordings(files, state, { confirmBrowserUpload: true }));
    return;
  }
  state.uploadInProgress = true;
  const uploadController = new AbortController();
  const abortUploads = () => uploadController.abort(state.signal.reason);
  state.signal.addEventListener("abort", abortUploads, { once: true });
  dropzone.classList.add("uploading");
  const uploaded = [];
  const totalBytes = uploadFiles.reduce((sum, file) => sum + (Number(file.size) || 0), 0);
  const progress = new Map(uploadFiles.map((_, index) => [index, 0]));
  let completed = 0;
  let lastRenderAt = 0;

  const renderProgress = (force = false) => {
    const now = Date.now();
    if (!force && now - lastRenderAt < UPLOAD_PROGRESS_THROTTLE_MS) return;
    lastRenderAt = now;
    const loadedBytes = uploadFiles.reduce((sum, file, index) => {
      const percent = progress.get(index) || 0;
      return sum + (Number(file.size) || 0) * percent / 100;
    }, 0);
    const percent = totalBytes > 0 ? loadedBytes / totalBytes * 100 : 0;
    setUploadMessage(uploadProgressHtml(`${completed}/${uploadFiles.length} ${t("new.batch_files")}`, percent));
  };

  try {
    renderProgress(true);
    await runWithConcurrency(uploadFiles, UPLOAD_CONCURRENCY, async (file, index) => {
      const payload = await API.uploadRecording(file, (percent) => {
        if (state.disposed) return;
        progress.set(index, percent);
        renderProgress(false);
      }, { signal: uploadController.signal });
      if (state.disposed || uploadController.signal.aborted) return;
      if (payload.path) {
        uploaded.push(payload.path);
        addBatchPath(state, payload.path);
        const input = document.getElementById("source-path");
        if (input) {
          input.value = payload.path;
          state.sourcePathBatchMirror = true;
        }
      }
      progress.set(index, 100);
      completed += 1;
      renderProgress(true);
    });
    const totalAdded = uploaded.length + directPaths.length;
    if (state.disposed || state.signal.aborted) return;
    setUploadMessage(`${t("new.upload_done")} ${totalAdded} ${t("new.batch_files")}`);
    await loadRecordings(state);
  } catch (error) {
    uploadController.abort();
    if (isAbortError(error, state.signal) || state.disposed) return;
    setUploadMessage(`${t("new.upload_failed")}${escapeHtml(error.message)}`, true);
  } finally {
    state.signal.removeEventListener("abort", abortUploads);
    state.uploadInProgress = false;
    if (dropzone.isConnected) dropzone.classList.remove("uploading");
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

function addBatchPath(state, path) {
  const value = cleanSourcePath(path);
  if (!value) return false;
  if (!state.batchPaths.includes(value)) {
    if (state.batchPaths.length >= BATCH_PATH_LIMIT) {
      setUploadMessage(t("new.batch_limit").replace("{count}", String(BATCH_PATH_LIMIT)), true);
      return false;
    }
    state.batchPaths.push(value);
  }
  renderBatchList(state);
  updateWizardSummary(state);
  return true;
}

function addCurrentSourceToBatch(state) {
  const input = document.getElementById("source-path");
  const value = cleanSourcePath(input?.value || "");
  if (!value) {
    setUploadMessage(t("new.path_required"), true);
    return;
  }
  addBatchPath(state, value);
  state.sourcePathBatchMirror = false;
  if (input) input.value = "";
  setUploadMessage(`${t("new.batch_direct_added")} 1 ${t("new.batch_files")}`);
}

function removeBatchPath(state, path) {
  state.batchPaths = state.batchPaths.filter((item) => item !== path);
  renderBatchList(state);
  updateWizardSummary(state);
}

function renderBatchList(state) {
  const target = document.getElementById("batch-box");
  if (!target) return;
  target.innerHTML = batchListHtml(state.batchPaths, BATCH_PATH_LIMIT);
  target.querySelector("#clear-batch")?.addEventListener("click", () => {
    state.batchPaths = [];
    renderBatchList(state);
    updateWizardSummary(state);
  });
  target.querySelectorAll("[data-remove-batch]").forEach((button) => {
    button.addEventListener("click", () => removeBatchPath(state, button.dataset.removeBatch || ""));
  });
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

async function loadRecordings(state) {
  const version = ++state.recordingLoadVersion;
  const target = document.getElementById("recording-picker");
  if (!target || state.disposed || state.signal.aborted) return;
  try {
    const recordings = await API.getRecordings({ signal: state.signal });
    if (state.disposed || state.signal.aborted || version !== state.recordingLoadVersion || !target.isConnected) return;
    if (!recordings.length) {
      target.innerHTML = `<div class="empty">${t("new.no_recordings")}</div>`;
      return;
    }
    renderRecordingList(target, recordings, false, state);
  } catch (error) {
    if (isAbortError(error, state.signal) || state.disposed || version !== state.recordingLoadVersion) return;
    target.innerHTML = `<div class="error">${escapeHtml(error.message)}</div>`;
  }
}

function renderRecordingList(target, recordings, showAll, state) {
  target.innerHTML = recordingListHtml(recordings, showAll);
  target.querySelectorAll("[data-path]").forEach((button) => {
    button.addEventListener("click", () => {
      document.getElementById("source-path").value = button.dataset.path || "";
      state.sourcePathBatchMirror = true;
      addBatchPath(state, button.dataset.path || "");
    });
  });
  document.getElementById("show-all-recordings")?.addEventListener("click", () => {
    renderRecordingList(target, recordings, true, state);
  });
}

function applyProfile(event, state) {
  const form = document.getElementById("new-job-form");
  const preset = event.currentTarget.value;
  const selected = getProfilePayload(preset, state);
  if (!form) return;
  if (selected?.whisper_language) document.getElementById("whisper-language").value = selected.whisper_language;
  for (const [name, , checked] of options) {
    if (!form.elements[name]) continue;
    form.elements[name].checked = selected && Object.prototype.hasOwnProperty.call(selected, name)
      ? Boolean(selected[name])
      : Boolean(checked);
  }
  updateWizardSummary(state);
}

function renderProfileOptions(recipes = []) {
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

function getProfilePayload(value, state) {
  if (!value) return null;
  if (value.startsWith("custom:")) {
    const id = value.slice("custom:".length);
    return loadCustomProfiles().find((profile) => profile.id === id)?.payload || null;
  }
  if (value.startsWith("recipe:")) {
    const id = value.slice("recipe:".length);
    return state.serverRecipes.find((recipe) => recipe.id === id)?.options || null;
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
  try {
    localStorage.setItem(CUSTOM_PROFILE_STORAGE_KEY, JSON.stringify(profiles.slice(0, 30)));
    return true;
  } catch {
    return false;
  }
}

function currentProfilePayload(form) {
  const payload = { whisper_language: document.getElementById("whisper-language").value };
  for (const [name] of options) payload[name] = Boolean(form.elements[name]?.checked);
  return payload;
}

function refreshProfileSelect(state, selectedValue = "") {
  const select = document.getElementById("workflow-profile");
  if (!select) return;
  select.innerHTML = renderProfileOptions(state.serverRecipes);
  select.value = selectedValue;
  updateWizardSummary(state);
}

async function saveCurrentProfile(state) {
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
    if (state.disposed) return;
    state.serverRecipes = [recipe, ...state.serverRecipes.filter((item) => item.id !== recipe.id)];
    refreshProfileSelect(state, `recipe:${recipe.id}`);
    showToast(t("new.profile_saved"), "success");
  } catch (error) {
    if (!state.disposed) showToast(`${t("new.profile_save_failed")} ${error.message}`, "error");
  }
}

async function deleteCurrentProfile(state) {
  const select = document.getElementById("workflow-profile");
  if (!select?.value?.startsWith("custom:") && !select?.value?.startsWith("recipe:")) return;
  if (!window.confirm(t("new.profile_delete_confirm"))) return;
  if (select.value.startsWith("recipe:")) {
    const id = select.value.slice("recipe:".length);
    try {
      await API.deleteRecipe(id);
      if (state.disposed) return;
      state.serverRecipes = state.serverRecipes.filter((recipe) => recipe.id !== id);
      refreshProfileSelect(state, "");
      applyProfile({ currentTarget: { value: "" } }, state);
      showToast(t("new.profile_deleted"), "success");
    } catch (error) {
      if (!state.disposed) showToast(`${t("new.profile_delete_failed")} ${error.message}`, "error");
    }
    return;
  }
  const id = select.value.slice("custom:".length);
  if (!storeCustomProfiles(loadCustomProfiles().filter((profile) => profile.id !== id))) {
    showToast(t("new.profile_delete_failed"), "error");
    return;
  }
  refreshProfileSelect(state, "");
  applyProfile({ currentTarget: { value: "" } }, state);
  showToast(t("new.profile_deleted"), "success");
}

async function submit(event, state) {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector('[type="submit"]');
  const path = cleanSourcePath(document.getElementById("source-path").value);
  const errorBox = document.getElementById("form-error");
  const paths = state.batchPaths.length ? state.batchPaths : (path ? [path] : []);
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
      if (state.disposed) return;
      errorBox.innerHTML = `<div class="notice">${t("new.batch_submit_started")} ${paths.length} ${t("new.batch_files")}</div>`;
      showToast(`${t("new.batch_submit_started")} ${paths.length} ${t("new.batch_files")}`, "success");
      location.hash = "#/";
    } else {
      const job = await API.submitJob({ ...basePayload, path: paths[0] });
      if (state.disposed) return;
      location.hash = `#/jobs/${encodeURIComponent(jobName(job))}`;
    }
  } catch (error) {
    if (state.disposed || isAbortError(error, state.signal)) return;
    errorBox.innerHTML = `<div class="error">${escapeHtml(error.message)}</div>`;
    showToast(error.message, "error");
  } finally {
    if (button?.isConnected) setButtonLoading(button, false);
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
    signal?.throwIfAborted();
    let recipes = recipesResponse.items || [];
    const legacyProfiles = loadCustomProfiles();
    if (legacyProfiles.length) {
      try {
        const imported = await API.importRecipes(legacyProfilesToRecipes(legacyProfiles));
        signal?.throwIfAborted();
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
  } catch (error) {
    if (isAbortError(error, signal)) throw error;
    return { projects: [], kits: [], recipes: [] };
  }
}

function applyProjectDefaultKit(state) {
  const projectSelect = document.getElementById("project-id");
  const kitSelect = document.getElementById("creator-kit-id");
  const defaultKit = projectSelect?.selectedOptions?.[0]?.dataset.defaultKit || "";
  if (kitSelect && defaultKit && Array.from(kitSelect.options).some((option) => option.value === defaultKit)) {
    kitSelect.value = defaultKit;
  }
  updateWizardSummary(state);
}

function selectProjectFromHash(state) {
  const query = String(location.hash || "").split("?", 2)[1] || "";
  const projectId = new URLSearchParams(query).get("project") || "";
  const select = document.getElementById("project-id");
  if (!select || !Array.from(select.options).some((option) => option.value === projectId)) return;
  select.value = projectId;
  applyProjectDefaultKit(state);
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
