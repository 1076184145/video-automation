import { API } from "./api.js";
import { t } from "./i18n.js";
import { escapeHtml } from "./utils.js";

const groups = [
  ["directories", "settings.directories"],
  ["paths", "settings.paths"],
  ["whisper", "settings.whisper"],
  ["detection", "settings.detection"],
  ["subtitles", "settings.subtitles"],
  ["exports", "settings.exports"],
  ["optional_modules", "settings.optional_modules"],
  ["covers", "settings.covers"],
  ["api", "settings.api"]
];

export async function renderSettings() {
  const app = document.getElementById("app");
  app.innerHTML = `<div class="loading">${t("common.loading")}</div>`;
  try {
    const payload = await API.getHealth();
    const settings = payload.settings || {};
    app.innerHTML = `
      <section class="page-head">
        <div>
          <h1 class="page-title">${t("settings.title")}</h1>
          <p class="page-subtitle">${t("settings.note")}</p>
        </div>
      </section>
      <div class="settings-grid">${groups.map(([name, key]) => renderGroup(key, settings[name] || {})).join("")}</div>
    `;
  } catch (error) {
    app.innerHTML = `<div class="error">${t("common.error")} ${escapeHtml(error.message)} <button class="button" id="retry-settings">${t("common.retry")}</button></div>`;
    document.getElementById("retry-settings")?.addEventListener("click", renderSettings);
  }
}

function renderGroup(titleKey, values) {
  return `<section class="panel">
    <h2>${t(titleKey)}</h2>
    ${Object.entries(values).map(([key, value]) => `
      <div class="kv"><span>${escapeHtml(key)}</span><span>${escapeHtml(value)}</span></div>
    `).join("") || `<div class="empty">${t("common.empty")}</div>`}
  </section>`;
}
