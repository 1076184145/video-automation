import { language, setLanguage, t } from "./i18n.js";
import { addRoute, lazyView, startRouter } from "./router.js";
import { icon } from "./icons.js";
import { installBrowserNotifications } from "./notifications.js";
import { installShortcutHelp } from "./shortcut-help.js";
import { applyTheme, nextThemePreference, savedThemePreference, saveThemePreference, watchSystemTheme } from "./theme.js";
import { showToast } from "./toast.js";

// Application shell: sidebar navigation, language/theme switching, and route
// registration. Page logic lives in the lazily imported view modules; feature
// extras live in shortcut-help.js / notifications.js.

const navItems = [
  ["#/", "nav.dashboard", "dashboard"],
  ["#/projects", "nav.projects", "projects"],
  ["#/new", "nav.new", "new"],
  ["#/publish", "nav.publish", "publish"],
  ["#/settings", "nav.settings", "settings"]
];

function renderNav() {
  const nav = document.getElementById("nav");
  nav.innerHTML = navItems.map(([href, key, iconName]) => `
    <a class="nav-link ${active(href)}" href="${href}" title="${t(key)}" aria-label="${t(key)}">${icon(iconName)}<span class="nav-label">${t(key)}</span></a>
  `).join("");
  const switcher = document.getElementById("language-switch");
  switcher.innerHTML = `
    <button class="lang-button theme-button" type="button" data-theme-toggle title="${t("theme.toggle")}" aria-label="${t("theme.toggle")}">${themeLabel()}</button>
    <button class="lang-button ${language() === "zh" ? "active" : ""}" data-lang="zh">中</button>
    <button class="lang-button ${language() === "en" ? "active" : ""}" data-lang="en">EN</button>
  `;
  switcher.querySelector("[data-theme-toggle]")?.addEventListener("click", toggleTheme);
  switcher.querySelectorAll("[data-lang]").forEach((button) => {
    button.addEventListener("click", () => setLanguage(button.dataset.lang));
  });
}

function active(href) {
  const current = location.hash || "#/";
  if (href === "#/") return (current === "#/" || current === "") ? "active" : "";
  return current.startsWith(href) ? "active" : "";
}

function themeLabel() {
  return { system: "A", light: "☀", dark: "☾" }[savedThemePreference()] || "A";
}

function toggleTheme() {
  saveThemePreference(nextThemePreference(savedThemePreference()));
  renderNav();
  showToast(t("theme.changed"), "success");
}

window.addEventListener("hashchange", renderNav);
window.addEventListener("languagechange", renderNav);

addRoute(/^\/$/, lazyView(() => import("./dashboard.js"), "renderDashboard"), "nav.dashboard");
addRoute(/^\/projects$/, lazyView(() => import("./projects.js"), "renderProjects"), "nav.projects");
addRoute(/^\/jobs\/([^/]+)$/, lazyView(() => import("./job-detail.js?v=20260606-4"), "renderJobDetail"), (match) => decodeURIComponent(match[1] || ""));
addRoute(/^\/new(?:\?.*)?$/, lazyView(() => import("./new-job.js"), "renderNewJob"), "nav.new");
addRoute(/^\/publish$/, lazyView(() => import("./publish-center.js"), "renderPublishCenterPage"), "nav.publish");
addRoute(/^\/settings$/, lazyView(() => import("./settings.js"), "renderSettings"), "nav.settings");
addRoute(/^\/health$/, lazyView(() => import("./health.js"), "renderHealth"), "nav.health");

applyTheme();
watchSystemTheme(renderNav);
renderNav();
startRouter();
installShortcutHelp();
installBrowserNotifications();
