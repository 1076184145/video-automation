import { t } from "./i18n.js";
import { escapeHtml } from "./utils.js";

const HINTS = [
  {
    key: "error_hint.gpu",
    action: { href: "#/settings", label: "error_hint.open_settings" },
    match: (text) => /\b(cuda|oom|out of memory|vram|gpu memory|ctranslate2)\b/i.test(text),
  },
  {
    key: "error_hint.whisper",
    action: { href: "#/health", label: "error_hint.open_health" },
    match: (text) => /faster[-_ ]?whisper|whispermodel|whisper|funasr/i.test(text),
  },
  {
    key: "error_hint.ffmpeg",
    action: { href: "#/health", label: "error_hint.open_health" },
    match: (text) => /ffmpeg|ffprobe|winerror 2|no such file or directory.*ff/i.test(text),
  },
  {
    key: "error_hint.api_key",
    action: { href: "#/settings", label: "error_hint.open_settings" },
    match: (text) => /api[_ -]?key|openai|openrouter|cover_api_key|openai_api_key/i.test(text),
  },
  {
    key: "error_hint.service",
    action: { href: "#/health", label: "error_hint.open_health" },
    match: (text) => /failed to fetch|networkerror|econnrefused|connection refused|service.*running/i.test(text),
  },
  {
    key: "error_hint.demucs",
    action: { href: "#/settings", label: "error_hint.open_settings" },
    match: (text) => /demucs|audio separation|uvr/i.test(text),
  },
  {
    key: "error_hint.media",
    action: { href: "#/new", label: "error_hint.import_again" },
    match: (text) => /corrupt|decode|invalid data|range not satisfiable|416|moov atom/i.test(text),
  },
];

export function errorHintHtml(message) {
  const raw = String(message || "").trim();
  const lower = raw.toLowerCase();
  const hint = HINTS.find((entry) => entry.match(lower));
  const hintText = hint ? t(hint.key) : t("error_hint.generic");
  return `
    <div class="error-hint">
      <div class="error-hint-title">${t("error_hint.title")}</div>
      <div>${escapeHtml(hintText)}</div>
      ${hint?.action ? `<a class="button compact-button" href="${hint.action.href}">${t(hint.action.label)}</a>` : ""}
      ${raw ? `<pre class="error-hint-raw">${escapeHtml(raw)}</pre>` : ""}
    </div>`;
}
