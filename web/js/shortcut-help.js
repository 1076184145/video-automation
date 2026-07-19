import { t } from "./i18n.js";

let returnFocus = null;

// Keyboard-shortcut cheat sheet: a floating "?" button plus the ? keyboard
// shortcut open a modal listing the clip-editor shortcuts. Extracted from
// app.js so the shell only wires features instead of implementing them.

export function installShortcutHelp() {
  const button = document.createElement("button");
  button.className = "shortcut-help-button";
  button.type = "button";
  button.setAttribute("aria-label", t("shortcuts.open"));
  button.textContent = "?";
  document.body.appendChild(button);
  button.addEventListener("click", openShortcutHelp);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if (document.getElementById("shortcut-modal")) {
        event.preventDefault();
        closeShortcutHelp();
      }
      return;
    }
    if (event.defaultPrevented || isTypingTarget(event.target)) return;
    if (event.key === "?" || (event.shiftKey && event.key === "/")) {
      event.preventDefault();
      openShortcutHelp();
    }
  });
  window.addEventListener("languagechange", () => {
    button.setAttribute("aria-label", t("shortcuts.open"));
    if (document.getElementById("shortcut-modal")) openShortcutHelp();
  });
}

function openShortcutHelp() {
  const replacing = Boolean(document.getElementById("shortcut-modal"));
  if (!replacing) returnFocus = document.activeElement;
  closeShortcutHelp(false);
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

function closeShortcutHelp(restoreFocus = true) {
  document.getElementById("shortcut-modal")?.remove();
  if (restoreFocus) {
    if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true });
    returnFocus = null;
  }
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
