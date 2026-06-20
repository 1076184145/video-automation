import { language, setLanguage, t } from "./i18n.js";
import { addRoute, startRouter } from "./router.js";
import { renderDashboard } from "./dashboard.js";
import { renderJobDetail } from "./job-detail.js?v=20260606-4";
import { renderNewJob } from "./new-job.js";
import { renderSettings } from "./settings.js";
import { renderHealth } from "./health.js";
import { showToast } from "./toast.js";
import { API } from "./api.js";
import { basename, isTerminal, jobName, statusGroup } from "./utils.js";

const THEME_STORAGE_KEY = "videoAutomationTheme";

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

const THEMES = ["default", "deep", "sunset", "purple"];

function savedTheme() {
  const theme = localStorage.getItem(THEME_STORAGE_KEY);
  return THEMES.includes(theme) ? theme : "default";
}

function applyTheme(theme = savedTheme()) {
  document.documentElement.dataset.theme = theme;
}

function themeLabel() {
  return t(`theme.${savedTheme()}`);
}

function toggleTheme() {
  const current = savedTheme();
  const next = THEMES[(THEMES.indexOf(current) + 1) % THEMES.length];
  localStorage.setItem(THEME_STORAGE_KEY, next);
  applyTheme(next);
  renderNav();
  showToast(`${t("theme.changed")} ${t(`theme.${next}`)}`, "success");
}

window.addEventListener("hashchange", renderNav);
window.addEventListener("languagechange", renderNav);

addRoute(/^\/$/, renderDashboard, "nav.dashboard");
addRoute(/^\/jobs\/([^/]+)$/, renderJobDetail, (match) => decodeURIComponent(match[1] || ""));
addRoute(/^\/new$/, renderNewJob, "nav.new");
addRoute(/^\/settings$/, renderSettings, "nav.settings");
addRoute(/^\/health$/, renderHealth, "nav.health");

applyTheme();
renderNav();
startRouter();
installShortcutHelp();
installBrowserNotifications();

function installShortcutHelp() {
  const button = document.createElement("button");
  button.className = "shortcut-help-button";
  button.type = "button";
  button.setAttribute("aria-label", t("shortcuts.open"));
  button.textContent = "?";
  document.body.appendChild(button);
  button.addEventListener("click", openShortcutHelp);
  document.addEventListener("keydown", (event) => {
    if (event.defaultPrevented || isTypingTarget(event.target)) return;
    if (event.key === "?" || (event.shiftKey && event.key === "/")) {
      event.preventDefault();
      openShortcutHelp();
    }
    if (event.key === "Escape") closeShortcutHelp();
  });
  window.addEventListener("languagechange", () => {
    button.setAttribute("aria-label", t("shortcuts.open"));
    if (document.getElementById("shortcut-modal")) openShortcutHelp();
  });
}

function openShortcutHelp() {
  closeShortcutHelp();
  const modal = document.createElement("div");
  modal.className = "modal-backdrop shortcut-modal";
  modal.id = "shortcut-modal";
  modal.innerHTML = `
    <section class="modal-card shortcut-card" role="dialog" aria-modal="true" aria-label="${t("shortcuts.title")}">
      <div class="modal-head">
        <div>
          <h2>${t("shortcuts.title")}</h2>
          <p>${t("shortcuts.subtitle")}</p>
        </div>
        <button class="button compact-button" type="button" data-close-shortcuts>${t("common.cancel")}</button>
      </div>
      <div class="shortcut-grid">
        ${shortcutRows().map(([keys, label]) => `
          <div class="shortcut-row">
            <div class="shortcut-keys">${keys.map((key) => `<kbd>${key}</kbd>`).join("")}</div>
            <div>${label}</div>
          </div>
        `).join("")}
      </div>
    </section>
  `;
  modal.addEventListener("click", (event) => {
    if (event.target === modal || event.target.closest("[data-close-shortcuts]")) closeShortcutHelp();
  });
  document.body.appendChild(modal);
  modal.querySelector("[data-close-shortcuts]")?.focus({ preventScroll: true });
}

function closeShortcutHelp() {
  document.getElementById("shortcut-modal")?.remove();
}

function shortcutRows() {
  const mod = navigator.platform.toLowerCase().includes("mac") ? "Cmd" : "Ctrl";
  return [
    [["Space"], t("shortcuts.play_pause")],
    [["Left", "Right"], t("shortcuts.seek")],
    [["["], t("shortcuts.mark_in")],
    [["]"], t("shortcuts.mark_out")],
    [["S"], t("shortcuts.split")],
    [[mod, "Z"], t("shortcuts.undo")],
    [[mod, "Shift", "Z"], t("shortcuts.redo")],
    [["Shift", t("shortcuts.click")], t("shortcuts.range_select")],
    [["?"], t("shortcuts.open")]
  ];
}

function isTypingTarget(target) {
  return Boolean(target?.closest?.("input, textarea, select, button, [contenteditable='true']"));
}

function installBrowserNotifications() {
  let events = null;
  let hiddenTerminalCount = 0;
  const notified = new Set();
  const originalTitle = () => {
    const appTitle = t("app.title");
    return document.title.replace(/^\(\d+\)\s+/, "") || appTitle;
  };

  const updateBadge = () => {
    const cleanTitle = originalTitle();
    document.title = hiddenTerminalCount > 0 ? `(${hiddenTerminalCount}) ${cleanTitle}` : cleanTitle;
  };

  const resetBadge = () => {
    hiddenTerminalCount = 0;
    updateBadge();
  };

  const parsePayload = (event) => {
    try {
      return JSON.parse(event.data || "{}");
    } catch {
      return {};
    }
  };

  const notifyJob = (job) => {
    if (!job || !job.job_dir || !isTerminal(job.status)) return;
    const key = `${job.job_dir}|${job.status}|${job.updated_at || ""}`;
    if (notified.has(key)) return;
    notified.add(key);
    if (notified.size > 200) {
      const first = notified.values().next().value;
      if (first) notified.delete(first);
    }

    if (document.visibilityState === "visible") return;
    hiddenTerminalCount += 1;
    updateBadge();

    if (!("Notification" in window) || Notification.permission !== "granted") return;
    const group = statusGroup(job.status);
    const title = t(`notify.job_${group}`);
    const body = basename(job.source_path) || jobName(job);
    const notification = new Notification(title, {
      body,
      tag: `video-automation-${job.job_dir}`,
      silent: true
    });
    notification.onclick = () => {
      window.focus();
      location.hash = `#/jobs/${encodeURIComponent(jobName(job))}`;
      notification.close();
    };
  };

  const start = () => {
    if (events) return;
    events = API.openEvents();
    events.addEventListener("job", (event) => notifyJob(parsePayload(event)));
    events.onerror = () => {
      // EventSource reconnects automatically.
    };
  };

  start();
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") resetBadge();
  });
  window.addEventListener("languagechange", updateBadge);
}

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
