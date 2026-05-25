import { language, setLanguage, t } from "./i18n.js";
import { addRoute, startRouter } from "./router.js";
import { renderDashboard } from "./dashboard.js";
import { renderJobDetail } from "./job-detail.js";
import { renderNewJob } from "./new-job.js";
import { renderSettings } from "./settings.js";
import { renderHealth } from "./health.js";

const navItems = [
  ["#/", "nav.dashboard", iconGrid()],
  ["#/new", "nav.new", iconPlus()],
  ["#/health", "nav.health", iconHeart()],
  ["#/settings", "nav.settings", iconGear()]
];

function renderNav() {
  const nav = document.getElementById("nav");
  nav.innerHTML = navItems.map(([href, key, icon]) => `
    <a class="nav-link ${active(href)}" href="${href}" title="${t(key)}" aria-label="${t(key)}">${icon}</a>
  `).join("");
  const switcher = document.getElementById("language-switch");
  switcher.innerHTML = `
    <button class="lang-button ${language() === "zh" ? "active" : ""}" data-lang="zh">中</button>
    <button class="lang-button ${language() === "en" ? "active" : ""}" data-lang="en">EN</button>
  `;
  switcher.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => setLanguage(button.dataset.lang));
  });
}

function active(href) {
  const current = location.hash || "#/";
  if (href === "#/") return (current === "#/" || current === "") ? "active" : "";
  return current.startsWith(href) ? "active" : "";
}

window.addEventListener("hashchange", renderNav);
window.addEventListener("languagechange", renderNav);

addRoute(/^\/$/, renderDashboard);
addRoute(/^\/jobs\/([^/]+)$/, renderJobDetail);
addRoute(/^\/new$/, renderNewJob);
addRoute(/^\/settings$/, renderSettings);
addRoute(/^\/health$/, renderHealth);

renderNav();
startRouter();

function iconGrid() {
  return `<svg viewBox="0 0 24 24"><path d="M4 4h7v7H4V4Zm9 0h7v7h-7V4ZM4 13h7v7H4v-7Zm9 0h7v7h-7v-7Z"/></svg>`;
}
function iconPlus() {
  return `<svg viewBox="0 0 24 24"><path d="M11 4h2v7h7v2h-7v7h-2v-7H4v-2h7V4Z"/></svg>`;
}
function iconHeart() {
  return `<svg viewBox="0 0 24 24"><path d="M12 21 4.6 13.6a5.2 5.2 0 0 1 7.4-7.3 5.2 5.2 0 0 1 7.4 7.3L12 21Zm0-2.9 5.9-5.9a3.2 3.2 0 0 0-4.5-4.5L12 9.1l-1.4-1.4a3.2 3.2 0 0 0-4.5 4.5l5.9 5.9Z"/></svg>`;
}
function iconGear() {
  return `<svg viewBox="0 0 24 24"><path d="m19.4 13.5 1.7 1.3-2 3.5-2-.8a7.8 7.8 0 0 1-1.8 1l-.3 2.1h-4l-.3-2.1a7.8 7.8 0 0 1-1.8-1l-2 .8-2-3.5 1.7-1.3a7 7 0 0 1 0-2.1L4.9 10l2-3.5 2 .8a7.8 7.8 0 0 1 1.8-1l.3-2.1h4l.3 2.1a7.8 7.8 0 0 1 1.8 1l2-.8 2 3.5-1.7 1.3a7 7 0 0 1 0 2.1ZM13 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z"/></svg>`;
}
