// Shared UI state builders: loading skeletons, empty states, error states.
//
// They return HTML strings to match the project's render idiom (views compose
// strings and assign innerHTML once), and they only emit classes that already
// exist in style.css (.grid/.skeleton, .empty, .error, .page-state).
//
// Parameter conventions: `title`/`label`-style arguments are expected to be
// i18n strings (trusted). Anything derived from user or server data must be
// escaped by the caller with escapeHtml() before being passed in.

import { escapeHtml } from "./utils.js";

/** A grid of skeleton placeholder cards shown while data loads. */
export function skeletonGrid(count = 4) {
  return `<div class="grid">${'<div class="skeleton"></div>'.repeat(count)}</div>`;
}

/** A page/panel-level loading placeholder. */
export function loadingState(label) {
  return `<div class="loading">${label}</div>`;
}

/**
 * An empty-state panel.
 * - title/body: i18n strings (trusted).
 * - contentHtml/actionHtml: optional extra trusted markup (steps, buttons).
 */
export function emptyState({ title, body = "", className = "", contentHtml = "", actionHtml = "" }) {
  return `
    <div class="empty ${className}">
      <strong>${title}</strong>
      ${body ? `<p>${body}</p>` : ""}
      ${contentHtml}
      ${actionHtml}
    </div>`;
}

/**
 * An error panel with an optional retry button.
 * - message: plain text by default.
 * - trustedHtml: opt in only for markup produced by a trusted local renderer.
 * - retryLabel: when set, renders a button with `data-retry` for delegation.
 */
export function errorState(message, { retryLabel = "", trustedHtml = false } = {}) {
  const content = trustedHtml ? String(message || "") : escapeHtml(message || "");
  const retry = retryLabel ? ` <button class="button" type="button" data-retry>${escapeHtml(retryLabel)}</button>` : "";
  return `<div class="error">${content}${retry}</div>`;
}

/**
 * A centered, route-level state (load errors, 404). Replaces the previous
 * inline-styled markup in router.js.
 */
export function pageState({ title, messageHtml = "", actionHtml = "" }) {
  return `
    <div class="page-state error">
      <h2>${title}</h2>
      ${messageHtml ? `<p>${messageHtml}</p>` : ""}
      ${actionHtml}
    </div>`;
}

/** Convenience for building the action link/button of a pageState. */
export function pageAction(label, { href = "", id = "" } = {}) {
  if (href) return `<a class="button primary" href="${escapeHtml(href)}">${label}</a>`;
  return `<button class="button primary" type="button" id="${escapeHtml(id)}">${label}</button>`;
}
