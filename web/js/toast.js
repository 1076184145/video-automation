import { escapeHtml } from "./utils.js";

const DEFAULT_TIMEOUT_MS = 3200;

export function showToast(message, type = "info", options = {}) {
  const container = ensureToastContainer();
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.setAttribute("role", type === "error" ? "alert" : "status");
  toast.innerHTML = `<span>${escapeHtml(String(message || ""))}</span><button type="button" aria-label="Close">×</button>`;
  container.appendChild(toast);

  const close = () => {
    toast.classList.add("is-leaving");
    window.setTimeout(() => toast.remove(), 180);
  };
  toast.querySelector("button")?.addEventListener("click", close);
  window.setTimeout(close, Number(options.timeoutMs || DEFAULT_TIMEOUT_MS));
  return toast;
}

export function setButtonLoading(button, loading, label = "") {
  if (!button) return;
  if (loading) {
    if (!button.dataset.originalHtml) button.dataset.originalHtml = button.innerHTML;
    button.disabled = true;
    button.classList.add("is-loading");
    if (label) button.textContent = label;
    return;
  }
  button.disabled = false;
  button.classList.remove("is-loading");
  if (button.dataset.originalHtml) {
    button.innerHTML = button.dataset.originalHtml;
    delete button.dataset.originalHtml;
  }
}

function ensureToastContainer() {
  let container = document.getElementById("toast-container");
  if (!container) {
    container = document.createElement("div");
    container.id = "toast-container";
    container.className = "toast-container";
    document.body.appendChild(container);
  }
  return container;
}
