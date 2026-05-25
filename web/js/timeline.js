import { t } from "./i18n.js";
import { formatTime } from "./utils.js";

const cssCache = new Map();
window.addEventListener("languagechange", () => cssCache.clear());

export function renderTimeline(canvas, data) {
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(600, Math.floor(rect.width * devicePixelRatio));
  canvas.height = Math.floor(180 * devicePixelRatio);
  const ctx = canvas.getContext("2d");
  ctx.scale(devicePixelRatio, devicePixelRatio);
  const width = canvas.width / devicePixelRatio;
  const height = canvas.height / devicePixelRatio;
  const duration = Math.max(1, data.duration || 1);
  const marks = [];

  ctx.clearRect(0, 0, width, height);
  drawAxis(ctx, width, height, duration);
  drawWaveform(ctx, width, data.waveform);
  drawSegments(ctx, width, duration, data.invalid || [], 72, 40, getCss("--timeline-invalid"), t("timeline.invalid"), marks);
  drawSegments(ctx, width, duration, data.clips || [], 72, 40, getCss("--timeline-keep"), t("timeline.keep"), marks);
  drawSegments(ctx, width, duration, data.transcript || [], 128, 10, getCss("--timeline-transcript"), t("timeline.transcript"), marks);
  drawScenes(ctx, width, duration, data.scenes || [], marks);
  attachTooltip(canvas, marks);
}

function drawWaveform(ctx, width, waveform) {
  const data = waveform?.data;
  if (!Array.isArray(data) || data.length < 2) return;
  const top = 60;
  const height = 82;
  const mid = top + height / 2;
  const usableWidth = width - 40;
  const pairs = Math.floor(data.length / 2);
  const step = Math.max(1, Math.ceil(pairs / usableWidth));
  ctx.fillStyle = "rgba(255,255,255,0.18)";
  for (let i = 0; i < pairs; i += step) {
    const min = Number(data[i * 2]) || 0;
    const max = Number(data[i * 2 + 1]) || 0;
    const x = 20 + (i / Math.max(1, pairs - 1)) * usableWidth;
    const y1 = mid - (max / 128) * (height / 2);
    const y2 = mid - (min / 128) * (height / 2);
    ctx.fillRect(x, Math.min(y1, y2), Math.max(1, usableWidth / pairs * step), Math.max(1, Math.abs(y2 - y1)));
  }
}

function drawAxis(ctx, width, height, duration) {
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
    const seconds = duration * (i / 5);
    ctx.fillText(formatTime(seconds), x - 14, height - 12);
  }
}

function drawSegments(ctx, width, duration, segments, y, h, color, label, marks) {
  for (const segment of segments) {
    const start = Number(segment.start ?? segment.time ?? 0);
    const end = Number(segment.end ?? start);
    const x = toX(start, width, duration);
    const w = Math.max(2, toX(end, width, duration) - x);
    ctx.fillStyle = color;
    roundRect(ctx, x, y, w, h, 6);
    ctx.fill();
    marks.push({ x, y, w, h, label, text: segment.text || segment.transcript_text || "", start, end });
  }
}

function drawScenes(ctx, width, duration, scenes, marks) {
  ctx.fillStyle = getCss("--timeline-scene");
  for (const scene of scenes) {
    const time = Number(scene.time ?? 0);
    const x = toX(time, width, duration);
    ctx.beginPath();
    ctx.moveTo(x, 32);
    ctx.lineTo(x - 8, 50);
    ctx.lineTo(x + 8, 50);
    ctx.closePath();
    ctx.fill();
    marks.push({ x: x - 8, y: 32, w: 16, h: 18, label: t("timeline.scene"), start: time, end: time });
  }
}

function toX(seconds, width, duration) {
  return 20 + (width - 40) * (Math.max(0, Math.min(duration, seconds)) / duration);
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.roundRect(x, y, w, h, r);
}

function attachTooltip(canvas, marks) {
  let tooltip = document.querySelector(".tooltip");
  if (!tooltip) {
    tooltip = document.createElement("div");
    tooltip.className = "tooltip";
    tooltip.hidden = true;
    document.body.appendChild(tooltip);
  }
  
  // 优化：按 x 坐标排序以提升查找性能
  marks.sort((a, b) => a.x - b.x);

  canvas.onmousemove = (event) => {
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    
    let found = null;
    for (let i = 0; i < marks.length; i++) {
      const item = marks[i];
      if (x < item.x) break; // 因为按 x 升序排列，后续不可能再匹配
      if (x >= item.x && x <= item.x + item.w && y >= item.y && y <= item.y + item.h) {
        found = item;
      }
    }
    
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
}

function getCss(name) {
  if (!cssCache.has(name)) {
    cssCache.set(name, getComputedStyle(document.documentElement).getPropertyValue(name).trim());
  }
  return cssCache.get(name);
}
