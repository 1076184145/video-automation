import { t } from "./i18n.js";
export function setPreviewOrientation(video) {
  if (!video) return;
  const apply = () => {
    const portrait = video.videoHeight > video.videoWidth;
    video.classList.toggle("is-portrait", portrait);
    video.classList.toggle("is-landscape", !portrait);
  };
  video.removeEventListener("loadedmetadata", apply);
  video.addEventListener("loadedmetadata", apply, { once: true });
  if (video.readyState >= 1) apply();
}


export function bindTimelineActions(root, resetTimelineView) {
  const handler = (event) => {
    if (event.target?.id !== "timeline-reset") return;
    resetTimelineView();
  };
  root.addEventListener("click", handler);
  return () => root.removeEventListener("click", handler);
}

export function bindDetailResizer(root) {
  const grid = root.querySelector("#detail-split");
  const handle = root.querySelector("#detail-resizer");
  if (!grid || !handle) return () => {};
  const storageKey = "videoAutomationDetailSplit";
  const clamp = (value) => Math.max(28, Math.min(72, value));
  const apply = (value) => {
    const percent = clamp(Number(value) || 42);
    grid.style.setProperty("--detail-left", `${percent}%`);
    return percent;
  };
  apply(localStorage.getItem(storageKey));

  let dragging = false;
  const updateFromPointer = (event) => {
    const rect = grid.getBoundingClientRect();
    if (!rect.width) return;
    const percent = apply(((event.clientX - rect.left) / rect.width) * 100);
    localStorage.setItem(storageKey, String(Math.round(percent * 10) / 10));
  };
  const pointerMove = (event) => {
    if (!dragging) return;
    event.preventDefault();
    updateFromPointer(event);
  };
  const pointerUp = () => {
    dragging = false;
    document.body.classList.remove("is-resizing-detail");
    handle.classList.remove("active");
    window.removeEventListener("pointermove", pointerMove);
    window.removeEventListener("pointerup", pointerUp);
  };
  const pointerDown = (event) => {
    if (window.matchMedia("(max-width: 860px)").matches) return;
    event.preventDefault();
    dragging = true;
    document.body.classList.add("is-resizing-detail");
    handle.classList.add("active");
    updateFromPointer(event);
    window.addEventListener("pointermove", pointerMove);
    window.addEventListener("pointerup", pointerUp, { once: true });
  };
  const keydown = (event) => {
    const current = parseFloat(localStorage.getItem(storageKey) || "42");
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      const next = apply(current + (event.key === "ArrowLeft" ? -4 : 4));
      localStorage.setItem(storageKey, String(next));
    }
    if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      const next = apply(event.key === "Home" ? 28 : 72);
      localStorage.setItem(storageKey, String(next));
    }
  };
  const doubleClick = () => {
    localStorage.removeItem(storageKey);
    apply(42);
  };

  handle.addEventListener("pointerdown", pointerDown);
  handle.addEventListener("keydown", keydown);
  handle.addEventListener("dblclick", doubleClick);
  return () => {
    pointerUp();
    handle.removeEventListener("pointerdown", pointerDown);
    handle.removeEventListener("keydown", keydown);
    handle.removeEventListener("dblclick", doubleClick);
  };
}


export function renderTimelineLegend() {
  const items = [
    ["keep", "timeline.keep"],
    ["invalid", "timeline.invalid"],
    ["scene", "timeline.scene"],
    ["transcript", "timeline.transcript"],
    ["waveform", "timeline.waveform"]
  ];
  return `<div class="timeline-legend">${items.map(([kind, key]) => `
    <span class="legend-item"><span class="legend-swatch ${kind}"></span>${t(key)}</span>
  `).join("")}</div>`;
}

export function seekPreview(seconds) {
  const video = document.querySelector("#section-preview video");
  if (!video || !Number.isFinite(seconds)) return;
  video.currentTime = Math.max(0, seconds);
  video.focus({ preventScroll: true });
}
