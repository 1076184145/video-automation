import { t } from "./i18n.js";
import { escapeHtml } from "./utils.js";

const routes = [];
let cleanup = null;

export function addRoute(pattern, render) {
  routes.push({ pattern, render });
}

export function navigate(hash) {
  location.hash = hash;
}

export async function renderRoute() {
  if (typeof cleanup === "function") cleanup();
  cleanup = null;
  const hash = location.hash || "#/";
  const path = hash.slice(1) || "/";
  
  const app = document.getElementById("app");
  if (app) {
    app.classList.remove("page-enter-active");
    app.classList.add("page-enter");
  }

  for (const route of routes) {
    const match = path.match(route.pattern);
    if (!match) continue;
    try {
      cleanup = await route.render(match);
      if (app) {
        requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            app.classList.remove("page-enter");
            app.classList.add("page-enter-active");
          });
        });
      }
    } catch (error) {
      console.error("[Router Error]", error);
      if (app) {
        app.innerHTML = `
          <div class="error" style="margin-top: 40px; text-align: center;">
            <h2>${t("router.load_error")}</h2>
            <p>${escapeHtml(error.message || t("router.load_error_note"))}</p>
            <button class="button primary" onclick="location.reload()" style="margin-top: 16px;">${t("common.reload")}</button>
          </div>
        `;
        app.classList.remove("page-enter");
        app.classList.add("page-enter-active");
      }
    }
    return;
  }
  if (app) {
    app.innerHTML = `
      <div class="error" style="margin-top: 40px; text-align: center;">
        <h2>${t("router.not_found")}</h2>
        <p>${t("router.not_found_note")}</p>
        <a class="button primary" href="#/" style="margin-top: 16px;">${t("router.back_dashboard")}</a>
      </div>
    `;
    app.classList.remove("page-enter");
    app.classList.add("page-enter-active");
  }
}

export function startRouter() {
  window.addEventListener("hashchange", renderRoute);
  window.addEventListener("languagechange", renderRoute);
  renderRoute();
}
