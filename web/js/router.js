import { t } from "./i18n.js";
import { MOTION_TIMINGS, prefersReducedMotion, waitForMotion } from "./motion.js";
import { pageAction, pageState } from "./ui-states.js";
import { escapeHtml } from "./utils.js";

const routes = [];
let cleanup = null;
let renderId = 0;
const routeLifecycle = createRouteLifecycle();

export function createRouteLifecycle() {
  let controller = null;
  return {
    next() {
      controller?.abort();
      controller = new AbortController();
      return controller;
    },
    dispose() {
      controller?.abort();
      controller = null;
    },
  };
}

export function addRoute(pattern, render, title) {
  routes.push({ pattern, render, title });
}

export function lazyView(importer, exportName) {
  let modulePromise = null;
  return async (...args) => {
    modulePromise ||= importer();
    const module = await modulePromise;
    const render = module?.[exportName];
    if (typeof render !== "function") {
      throw new Error(`Route module does not export ${exportName}`);
    }
    return render(...args);
  };
}

export function navigate(hash) {
  location.hash = hash;
}

export async function renderRoute(event) {
  if (typeof cleanup === "function") cleanup();
  cleanup = null;
  const controller = routeLifecycle.next();
  const currentRenderId = ++renderId;
  const hash = location.hash || "#/";
  const path = hash.slice(1) || "/";
  const app = document.getElementById("app");
  const shouldExit = Boolean(event && app?.childElementCount && !prefersReducedMotion());
  if (shouldExit) {
    app.classList.remove("page-enter", "page-enter-active");
    app.classList.add("page-exit");
    await waitForMotion(MOTION_TIMINGS.routeExit);
    if (currentRenderId !== renderId || controller.signal.aborted) return;
  }
  if (!event || event.type === "hashchange") resetRouteScroll();
  if (app) {
    app.classList.remove("page-enter-active", "page-exit");
    app.classList.add("page-enter");
  }

  for (const route of routes) {
    const match = path.match(route.pattern);
    if (!match) continue;
    try {
      updateTitle(route.title, match);
      const isActive = () => currentRenderId === renderId && !controller.signal.aborted;
      const routeContext = {
        signal: controller.signal,
        isActive,
        commit(callback) {
          if (!isActive() || typeof callback !== "function") return false;
          callback();
          return true;
        },
      };
      const previousMarkup = app?.innerHTML || "";
      const renderPromise = Promise.resolve(route.render(match, routeContext));
      const finishReveal = revealRouteWhenReady(app, previousMarkup, isActive);
      const nextCleanup = await renderPromise;
      if (currentRenderId !== renderId) {
        if (typeof nextCleanup === "function") nextCleanup();
        return;
      }
      cleanup = nextCleanup;
      finishReveal();
    } catch (error) {
      if (controller.signal.aborted || currentRenderId !== renderId) return;
      console.error("[Router Error]", error);
      if (app) {
        app.innerHTML = pageState({
          title: t("router.load_error"),
          messageHtml: escapeHtml(error.message || t("router.load_error_note")),
          actionHtml: pageAction(t("common.reload"), { id: "route-reload" })
        });
        app.querySelector("#route-reload")?.addEventListener("click", () => location.reload());
        app.classList.remove("page-enter");
        app.classList.add("page-enter-active");
      }
    }
    return;
  }
  updateTitle("router.not_found");
  if (app) {
    app.innerHTML = pageState({
      title: t("router.not_found"),
      messageHtml: t("router.not_found_note"),
      actionHtml: pageAction(t("router.back_dashboard"), { href: "#/" })
    });
    app.classList.remove("page-enter");
    app.classList.add("page-enter-active");
  }
}

function revealRouteWhenReady(app, previousMarkup, isActive) {
  let observer = null;
  let revealed = false;
  const reveal = () => {
    if (revealed) return;
    revealed = true;
    observer?.disconnect();
    revealRoute(app, isActive);
  };
  if (!app || app.innerHTML !== previousMarkup) {
    reveal();
  } else if (typeof globalThis.MutationObserver === "function") {
    observer = new globalThis.MutationObserver(() => {
      if (app.innerHTML !== previousMarkup) reveal();
    });
    observer.observe(app, { childList: true, subtree: true });
  }
  return reveal;
}

function revealRoute(app, isActive) {
  if (!app) return;
  const frame = globalThis.requestAnimationFrame || ((callback) => globalThis.setTimeout(callback, 0));
  frame(() => {
    frame(() => {
      if (!isActive()) return;
      app.classList.remove("page-enter", "page-exit");
      app.classList.add("page-enter-active");
    });
  });
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
