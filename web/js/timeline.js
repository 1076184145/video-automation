import { t } from "./i18n.js";
import { formatTime } from "./utils.js";

const cssCache = new Map();
window.addEventListener("languagechange", () => cssCache.clear());

export function renderTimeline(canvas, data, options = {}) {
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(600, Math.floor(rect.width * devicePixelRatio));
  canvas.height = Math.floor(180 * devicePixelRatio);
  const ctx = canvas.getContext("2d");
  ctx.scale(devicePixelRatio, devicePixelRatio);
  const width = canvas.width / devicePixelRatio;
  const height = canvas.height / devicePixelRatio;
  const duration = Math.max(1, data.duration || 1);
  const view = normalizeView(options.viewStart, options.viewEnd, duration);
  const marks = [];

  ctx.clearRect(0, 0, width, height);
  drawAxis(ctx, width, height, view);
  drawWaveform(ctx, width, data.waveform, view, duration);
  drawSegments(ctx, width, view, data.invalid || [], 72, 40, getCss("--timeline-invalid"), t("timeline.invalid"), marks);
  drawSegments(ctx, width, view, data.clips || [], 72, 40, getCss("--timeline-keep"), t("timeline.keep"), marks);
  drawSegments(ctx, width, view, data.transcript || [], 128, 10, getCss("--timeline-transcript"), t("timeline.transcript"), marks);
  drawScenes(ctx, width, view, data.scenes || [], marks);
  drawPlayhead(ctx, width, height, view, Number(options.currentTime ?? data.currentTime ?? 0));
  attachTimelineInteractions(canvas, marks, width, duration, view, options);
}

function drawWaveform(ctx, width, waveform, view, duration) {
  const data = waveform?.data;
  if (!Array.isArray(data) || data.length < 2) return;
  const top = 60;
  const height = 82;
  const mid = top + height / 2;
  const usableWidth = width - 40;
  const pairs = Math.floor(data.length / 2);
  const startIndex = Math.max(0, Math.floor((view.start / duration) * pairs));
  const endIndex = Math.min(pairs - 1, Math.ceil((view.end / duration) * pairs));
  const visiblePairs = Math.max(1, endIndex - startIndex + 1);
  const step = Math.max(1, Math.ceil(visiblePairs / usableWidth));
  ctx.fillStyle = "rgba(255,255,255,0.18)";
  for (let i = startIndex; i <= endIndex; i += step) {
    const min = Number(data[i * 2]) || 0;
    const max = Number(data[i * 2 + 1]) || 0;
    const seconds = (i / Math.max(1, pairs - 1)) * duration;
    const x = toX(seconds, width, view);
    const y1 = mid - (max / 128) * (height / 2);
    const y2 = mid - (min / 128) * (height / 2);
    ctx.fillRect(x, Math.min(y1, y2), Math.max(1, usableWidth / visiblePairs * step), Math.max(1, Math.abs(y2 - y1)));
  }
}

function drawAxis(ctx, width, height, view) {
  ctx.strokeStyle = "rgba(255,255,255,0.12)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(20, 150);
  ctx.lineTo(width - 20, 150);
  ctx.stroke();
  ctx.fillStyle = "rgba(255,255,255,0.5)";
  ctx.font = "12px Inter, sans-serif";
  for (let i = 0; i <= 5; i++) {
    const x = 20 + (width - 40) * (i / 5);
    const seconds = view.start + view.span * (i / 5);
    ctx.fillText(formatTime(seconds), x - 14, height - 12);
  }
}

function drawSegments(ctx, width, view, segments, y, h, color, label, marks) {
  for (const segment of segments) {
    const start = Number(segment.start ?? segment.time ?? 0);
    const end = Number(segment.end ?? start);
    if (end < view.start || start > view.end) continue;
    const visibleStart = Math.max(start, view.start);
    const visibleEnd = Math.min(end, view.end);
    const x = toX(visibleStart, width, view);
    const w = Math.max(2, toX(visibleEnd, width, view) - x);
    ctx.fillStyle = color;
    roundRect(ctx, x, y, w, h, 6);
    ctx.fill();
    marks.push({ x, y, w, h, label, text: segment.text || segment.transcript_text || "", start, end });
  }
}

function drawScenes(ctx, width, view, scenes, marks) {
  ctx.fillStyle = getCss("--timeline-scene");
  for (const scene of scenes) {
    const time = Number(scene.time ?? 0);
    if (time < view.start || time > view.end) continue;
    const x = toX(time, width, view);
    ctx.beginPath();
    ctx.moveTo(x, 32);
    ctx.lineTo(x - 8, 50);
    ctx.lineTo(x + 8, 50);
    ctx.closePath();
    ctx.fill();
    marks.push({ x: x - 8, y: 32, w: 16, h: 18, label: t("timeline.scene"), start: time, end: time });
  }
}

function drawPlayhead(ctx, width, height, view, currentTime) {
  if (!Number.isFinite(currentTime) || currentTime < view.start || currentTime > view.end) return;
  const x = toX(currentTime, width, view);
  const accent = getCss("--accent") || "#00d4aa";
  ctx.save();
  ctx.strokeStyle = accent;
  ctx.fillStyle = accent;
  ctx.lineWidth = 2;
  ctx.shadowColor = accent;
  ctx.shadowBlur = 10;
  ctx.beginPath();
  ctx.moveTo(x, 24);
  ctx.lineTo(x, height - 28);
  ctx.stroke();
  ctx.beginPath();
  ctx.arc(x, 22, 6, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

function toX(seconds, width, view) {
  return 20 + (width - 40) * ((Math.max(view.start, Math.min(view.end, seconds)) - view.start) / view.span);
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.roundRect(x, y, w, h, r);
}

function attachTimelineInteractions(canvas, marks, width, duration, view, options) {
  let tooltip = document.querySelector(".tooltip");
  if (!tooltip) {
    tooltip = document.createElement("div");
    tooltip.className = "tooltip";
    tooltip.hidden = true;
    document.body.appendChild(tooltip);
  }
  
  const lookupMarks = prepareMarksForLookup(marks);

  canvas.onmousemove = (event) => {
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const drag = canvas.__timelineDrag || null;

    if (drag) {
      const deltaSeconds = ((event.clientX - drag.lastX) / Math.max(1, width - 40)) * drag.view.span;
      if (Math.abs(event.clientX - drag.startX) > 2) drag.moved = true;
      drag.lastX = event.clientX;
      const nextView = panView(drag.view, duration, -deltaSeconds);
      drag.view = normalizeView(nextView.viewStart, nextView.viewEnd, duration);
      canvas.__timelineDrag = drag;
      emitViewChange(options, nextView);
      tooltip.hidden = true;
      return;
    }

    const found = findMark(lookupMarks, x, y);

    if (!found) {
      tooltip.hidden = true;
      return;
    }
    tooltip.hidden = false;
    tooltip.style.left = `${event.clientX + 14}px`;
    tooltip.style.top = `${event.clientY + 14}px`;
    tooltip.textContent = `${found.label} ${formatTime(found.start)}${found.end > found.start ? ` - ${formatTime(found.end)}` : ""} ${found.text || ""}`;
    
    const tooltipRect = tooltip.getBoundingClientRect();
    if (tooltipRect.right > window.innerWidth) {
      tooltip.style.left = `${Math.max(8, event.clientX - tooltipRect.width - 14)}px`;
    }
    if (tooltipRect.bottom > window.innerHeight) {
      tooltip.style.top = `${Math.max(8, event.clientY - tooltipRect.height - 14)}px`;
    }
  };
  canvas.onmouseleave = () => { tooltip.hidden = true; };
  canvas.onmousedown = (event) => {
    if (event.button !== 0) return;
    canvas.__timelineDrag = { startX: event.clientX, lastX: event.clientX, moved: false, view };
    canvas.classList.add("is-panning");
  };
  canvas.onwheel = (event) => {
    if (!options.onViewChange) return;
    event.preventDefault();
    const rect = canvas.getBoundingClientRect();
    const anchor = fromX(event.clientX - rect.left, width, view);
    const factor = event.deltaY < 0 ? 0.82 : 1.22;
    emitViewChange(options, zoomView(view, duration, anchor, factor));
  };
  canvas.ondblclick = () => {
    emitViewChange(options, { viewStart: 0, viewEnd: duration });
  };
  window.onmouseup = () => {
    const drag = canvas.__timelineDrag || null;
    if (!drag) return;
    window.setTimeout(() => { canvas.__timelineDrag = null; }, 0);
    canvas.classList.remove("is-panning");
  };
  canvas.onclick = (event) => {
    const drag = canvas.__timelineDrag || null;
    if (drag?.moved) return;
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const found = findMark(lookupMarks, x, y);
    const seconds = found ? Number(found.start || 0) : fromX(x, width, view);
    seekPreview(seconds);
  };
}

export function findTimelineMarkForTest(marks, x, y) {
  return findMark(prepareMarksForLookup(marks), x, y);
}

function prepareMarksForLookup(marks) {
  return marks
    .map((mark, index) => ({ ...mark, __order: index }))
    .sort((a, b) => a.x - b.x || a.__order - b.__order);
}

function findMark(marks, x, y) {
  let found = null;
  let foundOrder = -1;
  const end = firstMarkStartingAfter(marks, x);
  for (let i = end - 1; i >= 0; i--) {
    const item = marks[i];
    if (x >= item.x && x <= item.x + item.w && y >= item.y && y <= item.y + item.h) {
      const order = Number(item.__order ?? i);
      if (order >= foundOrder) {
        found = item;
        foundOrder = order;
      }
    }
  }
  return found;
}

function firstMarkStartingAfter(marks, x) {
  let lo = 0;
  let hi = marks.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    const item = marks[mid];
    if (item.x <= x) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

function fromX(x, width, view) {
  const ratio = (x - 20) / Math.max(1, width - 40);
  return Math.max(view.start, Math.min(view.end, view.start + ratio * view.span));
}

function seekPreview(seconds) {
  const video = document.querySelector("#section-preview video");
  if (!video || !Number.isFinite(seconds)) return;
  video.currentTime = Math.max(0, seconds);
  video.focus({ preventScroll: true });
}

function getCss(name) {
  if (!cssCache.has(name)) {
    cssCache.set(name, getComputedStyle(document.documentElement).getPropertyValue(name).trim());
  }
  return cssCache.get(name);
}

function normalizeView(start, end, duration) {
  const minSpan = Math.min(duration, 1);
  let viewStart = Number.isFinite(Number(start)) ? Number(start) : 0;
  let viewEnd = Number.isFinite(Number(end)) ? Number(end) : duration;
  if (viewEnd <= viewStart) {
    viewStart = 0;
    viewEnd = duration;
  }
  const span = Math.max(minSpan, Math.min(duration, viewEnd - viewStart));
  viewStart = Math.max(0, Math.min(duration - span, viewStart));
  return { start: viewStart, end: viewStart + span, span };
}

function panView(view, duration, deltaSeconds) {
  const start = Math.max(0, Math.min(duration - view.span, view.start + deltaSeconds));
  return { viewStart: start, viewEnd: start + view.span };
}

function zoomView(view, duration, anchor, factor) {
  const minSpan = Math.min(duration, 1);
  const span = Math.max(minSpan, Math.min(duration, view.span * factor));
  const ratio = (anchor - view.start) / Math.max(0.001, view.span);
  const start = Math.max(0, Math.min(duration - span, anchor - span * ratio));
  return { viewStart: start, viewEnd: start + span };
}

function emitViewChange(options, nextView) {
  if (typeof options.onViewChange === "function") options.onViewChange(nextView);
}
