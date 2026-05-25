import { API } from "./api.js";
import { t } from "./i18n.js";
import { escapeHtml } from "./utils.js";

export async function renderHealth() {
  const app = document.getElementById("app");
  app.innerHTML = `<div class="loading">${t("common.loading")}</div>`;
  try {
    const payload = await API.getHealth();
    app.innerHTML = `
      <section class="page-head">
        <div>
          <h1 class="page-title">${t("health.title")}</h1>
          <p class="page-subtitle">${payload.ok ? t("health.ready") : t("health.missing")}</p>
        </div>
        <span class="badge ${payload.ok ? "done" : "failed"}">${payload.ok ? t("common.ok") : t("common.missing")}</span>
      </section>
      <section class="panel">
        <table class="table">
          <thead><tr><th>${t("health.tool")}</th><th>${t("common.path")}</th><th>${t("common.status")}</th><th>${t("common.version")}</th></tr></thead>
          <tbody>${(payload.checks || []).map((check) => `
            <tr>
              <td>${escapeHtml(check.name)}</td>
              <td><code>${escapeHtml(check.path)}</code></td>
              <td>${healthStatusBadge(check)}</td>
              <td>${escapeHtml(check.version || "")}</td>
            </tr>
          `).join("")}</tbody>
        </table>
      </section>
    `;
  } catch (error) {
    app.innerHTML = `<div class="error">${t("common.error")} ${escapeHtml(error.message)} <button class="button" id="retry-health">${t("common.retry")}</button></div>`;
    document.getElementById("retry-health")?.addEventListener("click", renderHealth);
  }
}

function healthStatusBadge(check) {
  if (check.exists) {
    return `<span class="badge done">${t("common.ok")}</span>`;
  }
  if (check.optional || check.status === "optional_missing") {
    return `<span class="badge optional">${t("health.optional_missing")}</span>`;
  }
  return `<span class="badge failed">${t("common.missing")}</span>`;
}
