import { t } from "./i18n.js";

export function renderAiDisclosure(kind = "text") {
  const key = kind === "image" ? "ai.disclosure_image" : "ai.disclosure_text";
  const usageKey = kind === "image" ? "ai.usage_image" : "ai.usage_text";
  return `<div class="notice ai-disclosure">${t(key)}<small>${t(usageKey)}</small></div>`;
}
