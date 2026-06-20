export function formatClipTimeInput(value) {
  const total = Math.max(0, Number(value) || 0);
  const whole = Math.floor(total);
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const seconds = whole % 60;
  const fraction = total - whole;
  const secondsText = fraction > 0.0005 ? (seconds + fraction).toFixed(1).replace(/\.0$/, "") : String(seconds);
  if (hours > 0) return `${hours}时${minutes}分${secondsText}秒`;
  return minutes > 0 ? `${minutes}分${secondsText}秒` : `${secondsText}秒`;
}

export function parseClipTime(value) {
  const raw = String(value ?? "").trim();
  if (!raw) return NaN;
  const normalized = raw
    .replace(/[：]/g, ":")
    .replace(/\s+/g, "")
    .toLowerCase();
  const chinese = normalized.match(/^(?:(\d+(?:\.\d+)?)小时|(\d+(?:\.\d+)?)时)?(?:(\d+(?:\.\d+)?)分)?(?:(\d+(?:\.\d+)?)秒?)?$/);
  if (chinese && (chinese[1] || chinese[2] || chinese[3] || chinese[4])) {
    return (Number(chinese[1] || chinese[2] || 0) * 3600) + (Number(chinese[3] || 0) * 60) + Number(chinese[4] || 0);
  }
  const compact = normalized.match(/^(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m)?(?:(\d+(?:\.\d+)?)s?)?$/);
  if (compact && (compact[1] || compact[2] || compact[3])) {
    return (Number(compact[1] || 0) * 3600) + (Number(compact[2] || 0) * 60) + Number(compact[3] || 0);
  }
  if (normalized.includes(":")) {
    const parts = normalized.split(":").map(Number);
    if (parts.length >= 2 && parts.length <= 3 && parts.every(Number.isFinite)) {
      return parts.reduce((total, part) => total * 60 + part, 0);
    }
  }
  return Number(normalized.replace(/秒$/, ""));
}
