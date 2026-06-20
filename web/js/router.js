import { t } from "./i18n.js";
import { escapeHtml } from "./utils.js";

const routes = [];
let cleanup = null;

export function addRoute(pattern, render, title) {
  routes.push({ pattern, render, title });
}

export function navigate(hash) {
  location.hash = hash;
}

export async function renderRoute(event) {
  if (typeof cleanup === "function") cleanup();
  cleanup = null;
  if (!event || event.type === "hashchange") {
    resetRouteScroll();
  }
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
      updateTitle(route.title, match);
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
  updateTitle("router.not_found");
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

export function resetRouteScroll() {
  document.documentElement.scrollTop = 0;
  document.body.scrollTop = 0;
  window.scrollTo({ top: 0, left: 0, behavior: "auto" });
}

function updateTitle(title, match = []) {
  const appTitle = t("app.title");
  let value = "";
  if (typeof title === "function") {
    value = title(match);
  } else if (typeof title === "string") {
    value = t(title);
  }
  document.title = value && value !== appTitle ? `${value} - ${appTitle}` : appTitle;
  announceRoute(value || appTitle);
}

function announceRoute(value) {
  let live = document.getElementById("route-live-region");
  if (!live) {
    live = document.createElement("div");
    live.id = "route-live-region";
    live.className = "sr-only";
    live.setAttribute("aria-live", "polite");
    live.setAttribute("aria-atomic", "true");
    document.body.appendChild(live);
  }
  live.textContent = value;
}

export function startRouter() {
  window.addEventListener("hashchange", renderRoute);
  window.addEventListener("languagechange", renderRoute);
  window.addEventListener("online", renderRoute);
  renderRoute();
}
