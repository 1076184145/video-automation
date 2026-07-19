import { t } from "./i18n.js";
import { loadingState } from "./ui-states.js";
import { basename, escapeHtml } from "./utils.js";

// New-job wizard view layer: pure HTML builders. State, uploads, profiles
// logic, and event binding live in new-job.js; this module only turns view
// data into markup (the dashboard.js / job-card.js split pattern).

// Pipeline feature checkboxes offered by the wizard, as
// [form field name, i18n key, checked by default]. The controller imports this
// list to read/apply the same options, so it is the single source of truth.
export const NEW_JOB_OPTIONS = [
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

export function renderNewJobForm(disclosures = {}, library = {}, profileOptionsHtml = "") {
  const sourceToolsOpen = disclosures.sourceTools ? " open" : "";
  const processingOptionsOpen = disclosures.processingOptions ? " open" : "";
  const projects = Array.isArray(library.projects) ? library.projects : [];
  const kits = Array.isArray(library.kits) ? library.kits : [];
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
                ${loadingState(t("common.loading"))}
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
                ${profileOptionsHtml}
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
                <div class="options">${NEW_JOB_OPTIONS.map(([name, key, checked]) => `
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

export function renderWizardRail() {
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

export function summaryCard(label, value) {
  return `
    <div class="wizard-summary-card">
      <span>${label}</span>
      <strong>${escapeHtml(value)}</strong>
    </div>
  `;
}

export function uploadProgressHtml(filename, percent) {
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

/** Batch box contents; returns "" when the batch is empty. */
export function batchListHtml(paths, limit) {
  if (!paths.length) return "";
  return `
    <div class="batch-head">
      <strong>${t("new.batch_selected")} (${paths.length})</strong>
      <small>${t("new.batch_limit_hint").replace("{count}", String(limit))}</small>
      <button class="button compact-button" id="clear-batch" type="button">${t("new.batch_clear")}</button>
    </div>
    <div class="batch-list">
      ${paths.map((path) => `
        <div class="batch-item">
          <span>${escapeHtml(basename(path))}</span>
          <button class="button compact-button" type="button" data-remove-batch="${escapeHtml(path)}">${t("new.batch_remove")}</button>
        </div>
      `).join("")}
    </div>
  `;
}

export function recordingListHtml(recordings, showAll) {
  const visible = showAll ? recordings : recordings.slice(0, 12);
  return `
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
}

function formatBytes(value) {
  const bytes = Number(value) || 0;
  if (bytes >= 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`;
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${Math.round(bytes / 1024)} KB`;
}
