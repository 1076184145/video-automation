const MOTION_ITEM_SELECTOR = "[data-motion-item], .notice, .error, .empty";
const seenMotionKeys = new Set();
const removalPromises = new WeakMap();

export const MOTION_TIMINGS = Object.freeze({
  item: 240,
  exit: 180,
  overlay: 200,
  routeExit: 150,
  stagger: 28,
});

export function prefersReducedMotion() {
  return Boolean(globalThis.window?.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches);
}

export function motionDelayForIndex(index) {
  const order = Math.max(0, Math.min(6, Number(index) || 0));
  return order * MOTION_TIMINGS.stagger;
}

export function motionKeyFor(element) {
  const data = element?.dataset || {};
  if (data.motionKey) return String(data.motionKey);
  if (data.queueId) return `queue:${data.queueId}`;
  if (data.clipKey) return `clip:${data.clipKey}`;
  if (data.publishId) return `publish:${data.publishId}`;
  if (data.path) return `recording:${data.path}`;
  return "";
}

export function installMotionSystem(root = globalThis.document) {
  const body = root?.body || root;
  if (!body || typeof globalThis.MutationObserver !== "function") return () => {};

  const observer = new globalThis.MutationObserver((records) => {
    const targets = [];
    const unique = new Set();
    for (const record of records) {
      for (const node of record.addedNodes || []) {
        for (const target of motionTargetsWithin(node)) {
          if (unique.has(target)) continue;
          unique.add(target);
          targets.push(target);
        }
      }
    }
    animateAddedTargets(targets, root);
  });

  const handleDisclosureClick = (event) => {
    const summary = event.target?.closest?.("summary");
    const details = summary?.parentElement;
    if (!summary || details?.tagName !== "DETAILS") return;
    if (event.target !== summary && event.target?.closest?.("a, button, input, select, textarea")) return;
    if (event.button != null && event.button !== 0) return;
    event.preventDefault();
    toggleDisclosure(details, summary);
  };

  observer.observe(body, { childList: true, subtree: true });
  root.addEventListener?.("click", handleDisclosureClick);
  return () => {
    observer.disconnect();
    root.removeEventListener?.("click", handleDisclosureClick);
  };
}

export function animateContentRefresh(element) {
  if (!element || prefersReducedMotion() || typeof element.animate !== "function") return;
  const app = globalThis.document?.getElementById?.("app");
  if (app?.contains?.(element) && (app.classList.contains("page-enter") || app.classList.contains("page-exit"))) return;
  element.getAnimations?.().forEach((animation) => {
    if (animation.id === "content-refresh") animation.cancel();
  });
  const animation = element.animate([
    { opacity: 0.62, translate: "0 3px" },
    { opacity: 1, translate: "0 0" },
  ], {
    duration: 180,
    easing: "cubic-bezier(0.2, 0.8, 0.2, 1)",
  });
  animation.id = "content-refresh";
}

export function waitForMotion(duration = MOTION_TIMINGS.item) {
  if (prefersReducedMotion()) return Promise.resolve();
  return new Promise((resolve) => globalThis.setTimeout(resolve, duration));
}

export function removeWithMotion(element, options = {}) {
  if (!element) return Promise.resolve();
  const existing = removalPromises.get(element);
  if (existing) return existing;
  const promise = performRemoval(element, options);
  removalPromises.set(element, promise);
  return promise;
}

async function performRemoval(element, options) {
  if (!element.isConnected || prefersReducedMotion()) {
    element.remove?.();
    return;
  }

  const kind = options.kind || "item";
  const duration = Number(options.duration || (kind === "overlay" ? MOTION_TIMINGS.overlay : MOTION_TIMINGS.exit));
  element.dataset.motionRemoving = "1";
  element.setAttribute?.("aria-hidden", "true");
  element.style.pointerEvents = "none";

  if (kind === "overlay" || kind === "toast" || typeof element.animate !== "function") {
    element.classList?.add("ui-exit");
    await waitForMotion(duration);
    element.remove?.();
    return;
  }

  const tagName = String(element.tagName || "").toUpperCase();
  const collapse = options.collapse !== false && tagName !== "TR";
  const rect = element.getBoundingClientRect?.() || { height: 0 };
  const computed = globalThis.getComputedStyle?.(element);
  element.style.overflow = "hidden";
  element.classList?.add("ui-exit");

  const start = {
    opacity: Number(computed?.opacity || 1),
    translate: "0 0",
  };
  const end = {
    opacity: 0,
    translate: "0 -6px",
  };
  if (collapse && rect.height > 0) {
    Object.assign(start, {
      height: `${rect.height}px`,
      marginTop: computed?.marginTop || "0px",
      marginBottom: computed?.marginBottom || "0px",
      paddingTop: computed?.paddingTop || "0px",
      paddingBottom: computed?.paddingBottom || "0px",
    });
    Object.assign(end, {
      height: "0px",
      marginTop: "0px",
      marginBottom: "0px",
      paddingTop: "0px",
      paddingBottom: "0px",
    });
  }

  try {
    await element.animate([start, end], {
      duration,
      easing: "cubic-bezier(0.4, 0, 1, 1)",
      fill: "forwards",
    }).finished;
  } catch {
    // A canceled animation still removes the stale UI node.
  }
  element.remove?.();
}

function motionTargetsWithin(node) {
  if (!node || node.nodeType !== 1) return [];
  const targets = [];
  if (node.matches?.(MOTION_ITEM_SELECTOR)) targets.push(node);
  node.querySelectorAll?.(MOTION_ITEM_SELECTOR).forEach((target) => targets.push(target));
  return targets;
}

function animateAddedTargets(targets, root) {
  const app = root?.getElementById?.("app") || globalThis.document?.getElementById?.("app");
  const routeChanging = Boolean(app?.classList?.contains("page-enter") || app?.classList?.contains("page-exit"));
  let order = 0;

  for (const target of targets) {
    if (!target?.isConnected || target.dataset.motionSeen === "1") continue;
    target.dataset.motionSeen = "1";
    const key = motionKeyFor(target);
    const alreadySeen = Boolean(key && seenMotionKeys.has(key));
    if (key) seenMotionKeys.add(key);
    if (alreadySeen || prefersReducedMotion() || (routeChanging && app?.contains?.(target))) continue;

    target.style.setProperty("--motion-delay", `${motionDelayForIndex(order)}ms`);
    target.classList.add("ui-enter");
    const cleanup = () => target.classList?.remove("ui-enter");
    target.addEventListener?.("animationend", cleanup, { once: true });
    globalThis.setTimeout(cleanup, MOTION_TIMINGS.item + motionDelayForIndex(order) + 80);
    order += 1;
  }
}

async function toggleDisclosure(details, summary) {
  if (details.dataset.motionBusy === "1") return;
  if (prefersReducedMotion() || typeof details.animate !== "function") {
    details.open = !details.open;
    return;
  }

  details.dataset.motionBusy = "1";
  const opening = !details.open;
  const startHeight = details.getBoundingClientRect().height;
  if (opening) details.open = true;
  const computed = globalThis.getComputedStyle?.(details);
  const collapsedHeight = summary.getBoundingClientRect().height
    + numberValue(computed?.paddingTop)
    + numberValue(computed?.paddingBottom)
    + numberValue(computed?.borderTopWidth)
    + numberValue(computed?.borderBottomWidth);
  const endHeight = opening ? details.getBoundingClientRect().height : collapsedHeight;
  const content = Array.from(details.children || []).filter((child) => child !== summary);

  details.style.height = `${startHeight}px`;
  details.style.overflow = "hidden";
  details.classList.toggle("is-opening", opening);
  details.classList.toggle("is-closing", !opening);

  const animations = content.map((child) => child.animate([
    opening ? { opacity: 0, translate: "0 -4px" } : { opacity: 1, translate: "0 0" },
    opening ? { opacity: 1, translate: "0 0" } : { opacity: 0, translate: "0 -4px" },
  ], {
    duration: opening ? 220 : 140,
    delay: opening ? 30 : 0,
    easing: opening ? "cubic-bezier(0.22, 1, 0.36, 1)" : "cubic-bezier(0.4, 0, 1, 1)",
    fill: "both",
  }));
  animations.push(details.animate([
    { height: `${startHeight}px` },
    { height: `${Math.max(collapsedHeight, endHeight)}px` },
  ], {
    duration: opening ? 240 : 170,
    easing: opening ? "cubic-bezier(0.22, 1, 0.36, 1)" : "cubic-bezier(0.4, 0, 1, 1)",
  }));

  await Promise.allSettled(animations.map((animation) => animation.finished));
  if (!opening) details.open = false;
  details.style.removeProperty("height");
  details.style.removeProperty("overflow");
  details.classList.remove("is-opening", "is-closing");
  delete details.dataset.motionBusy;
}

function numberValue(value) {
  const number = Number.parseFloat(value || "0");
  return Number.isFinite(number) ? number : 0;
}
