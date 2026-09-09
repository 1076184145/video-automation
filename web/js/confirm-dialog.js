import { escapeHtml } from "./utils.js";
import { removeWithMotion } from "./motion.js";

export function confirmAction(message, options = {}) {
  const existing = document.getElementById("confirm-dialog");
  existing?.remove();
  const previousFocus = document.activeElement;
  const tone = options.tone === "primary" ? "primary" : "danger";
  const modal = document.createElement("div");
  modal.className = "modal-backdrop confirm-modal";
  modal.id = "confirm-dialog";
  modal.innerHTML = `
    <section class="modal-card confirm-card" role="dialog" aria-modal="true" aria-labelledby="confirm-dialog-title" aria-describedby="confirm-dialog-message">
      <div class="confirm-icon ${tone}" aria-hidden="true">${tone === "primary" ? "→" : "!"}</div>
      <div>
        <h2 id="confirm-dialog-title">${escapeHtml(options.title || "")}</h2>
        <p id="confirm-dialog-message"${options.scrollMessage ? ' class="scrollable-confirm-message"' : ""}>${escapeHtml(message)}</p>
      </div>
      <div class="confirm-actions">
        <button class="button" type="button" data-confirm-cancel>${escapeHtml(options.cancelLabel || "Cancel")}</button>
        <button class="button ${tone}" type="button" data-confirm-accept>${escapeHtml(options.confirmLabel || "Confirm")}</button>
      </div>
    </section>
  `;

  return new Promise((resolve) => {
    let settled = false;
    const finish = async (accepted) => {
      if (settled) return;
      settled = true;
      document.removeEventListener("keydown", handleKeydown);
      await removeWithMotion(modal, { kind: "overlay" });
      previousFocus?.focus?.({ preventScroll: true });
      resolve(accepted);
    };
    const handleKeydown = (event) => {
      if (event.key === "Escape") finish(false);
    };
    modal.addEventListener("click", (event) => {
      if (event.target === modal || event.target.closest("[data-confirm-cancel]")) finish(false);
      if (event.target.closest("[data-confirm-accept]")) finish(true);
    });
    document.addEventListener("keydown", handleKeydown);
    document.body.appendChild(modal);
    modal.querySelector("[data-confirm-cancel]")?.focus({ preventScroll: true });
  });
}

export function promptAction(message, options = {}) {
  document.getElementById("prompt-dialog")?.remove();
  const previousFocus = document.activeElement;
  const modal = document.createElement("div");
  modal.className = "modal-backdrop prompt-modal";
  modal.id = "prompt-dialog";
  modal.innerHTML = `
    <section class="modal-card prompt-card" role="dialog" aria-modal="true" aria-labelledby="prompt-dialog-title" aria-describedby="prompt-dialog-message">
      <div>
        <h2 id="prompt-dialog-title">${escapeHtml(options.title || "")}</h2>
        <p id="prompt-dialog-message">${escapeHtml(message)}</p>
      </div>
      <form data-prompt-form>
        <input class="prompt-input" name="value" type="text" maxlength="${Number(options.maxlength || 120)}" value="${escapeHtml(options.value || "")}" autocomplete="off" />
        <div class="confirm-actions">
          <button class="button" type="button" data-prompt-cancel>${escapeHtml(options.cancelLabel || "Cancel")}</button>
          <button class="button primary" type="submit">${escapeHtml(options.confirmLabel || "Save")}</button>
        </div>
      </form>
    </section>
  `;

  return new Promise((resolve) => {
    let settled = false;
    const finish = async (value) => {
      if (settled) return;
      settled = true;
      document.removeEventListener("keydown", handleKeydown);
      await removeWithMotion(modal, { kind: "overlay" });
      previousFocus?.focus?.({ preventScroll: true });
      resolve(value);
    };
    const handleKeydown = (event) => {
      if (event.key === "Escape") finish(null);
    };
    modal.addEventListener("click", (event) => {
      if (event.target === modal || event.target.closest("[data-prompt-cancel]")) finish(null);
    });
    modal.querySelector("[data-prompt-form]")?.addEventListener("submit", (event) => {
      event.preventDefault();
      finish(new FormData(event.currentTarget).get("value")?.toString() || "");
    });
    document.addEventListener("keydown", handleKeydown);
    document.body.appendChild(modal);
    const input = modal.querySelector(".prompt-input");
    input?.focus({ preventScroll: true });
    input?.select();
  });
}
