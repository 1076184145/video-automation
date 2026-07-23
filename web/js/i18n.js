import { en } from "./i18n-en.js";
import { zh } from "./i18n-zh.js";

const dictionaries = { zh, en };
let currentLanguage = resolveLanguage();

function resolveLanguage() {
  const stored = localStorage.getItem("videoAutomationLanguage");
  if (stored === "zh" || stored === "en") return stored;
  return navigator.language && navigator.language.toLowerCase().startsWith("zh") ? "zh" : "en";
}

export function language() {
  return currentLanguage;
}

export function setLanguage(nextLanguage) {
  currentLanguage = nextLanguage === "en" ? "en" : "zh";
  localStorage.setItem("videoAutomationLanguage", currentLanguage);
  document.documentElement.lang = currentLanguage === "zh" ? "zh-CN" : "en";
  window.dispatchEvent(new CustomEvent("languagechange"));
}

export function t(key) {
  return dictionaries[currentLanguage][key] || en[key] || key;
}

export function localizedErrorMessage(error, fallback = "") {
  const code = String(error?.payload?.error?.code || "").trim();
  if (code) {
    const key = `error.${code}`;
    const message = t(key);
    if (message !== key) return message;
  }
  return String(error?.message || fallback || "").trim();
}
