import { escapeHtml } from "./utils.js";

export function confirmAction(message, options = {}) {
  const existing = document.getElementById("confirm-dialog");
  existing?.remove();
  const previousFocus = document.activeElement;
  const modal = document.createElement("div");
  modal.className = "modal-backdrop confirm-modal";
  modal.id = "confirm-dialog";
  modal.innerHTML = `
    <section class="modal-card confirm-card" role="dialog" aria-modal="true" aria-labelledby="confirm-dialog-title" aria-describedby="confirm-dialog-message">
      <div class="confirm-icon" aria-hidden="true">!</div>
      <div>
        <h2 id="confirm-dialog-title">${escapeHtml(options.title || "")}</h2>
        <p id="confirm-dialog-message">${escapeHtml(message)}</p>
      </div>
      <div class="confirm-actions">
        <button class="button" type="button" data-confirm-cancel>${escapeHtml(options.cancelLabel || "Cancel")}</button>
        <button class="button danger" type="button" data-confirm-accept>${escapeHtml(options.confirmLabel || "Confirm")}</button>
      </div>
    </section>
  `;

  return new Promise((resolve) => {
    const finish = (accepted) => {
      document.removeEventListener("keydown", handleKeydown);
      modal.remove();
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
