export const THEME_STORAGE_KEY = "videoAutomationTheme";
export const THEME_PREFERENCES = ["system", "light", "dark"];

export function resolveTheme(preference, prefersDark = false) {
  if (preference === "light" || preference === "dark") return preference;
  return prefersDark ? "dark" : "light";
}

export function nextThemePreference(preference) {
  const index = THEME_PREFERENCES.indexOf(preference);
  return THEME_PREFERENCES[(index < 0 ? 0 : index + 1) % THEME_PREFERENCES.length];
}

export function savedThemePreference(storage = globalThis.localStorage) {
  const value = storage?.getItem?.(THEME_STORAGE_KEY);
  return THEME_PREFERENCES.includes(value) ? value : "system";
}

export function systemPrefersDark(media = globalThis.matchMedia?.("(prefers-color-scheme: dark)")) {
  return Boolean(media?.matches);
}

export function applyTheme(
  preference = savedThemePreference(),
  root = globalThis.document?.documentElement,
  media = globalThis.matchMedia?.("(prefers-color-scheme: dark)"),
) {
  const resolved = resolveTheme(preference, systemPrefersDark(media));
  if (root) {
    root.dataset.theme = resolved;
    root.dataset.themePreference = preference;
    root.style.colorScheme = resolved;
  }
  return resolved;
}

export function saveThemePreference(preference, storage = globalThis.localStorage) {
  const value = THEME_PREFERENCES.includes(preference) ? preference : "system";
  storage?.setItem?.(THEME_STORAGE_KEY, value);
  applyTheme(value);
  return value;
}

export function watchSystemTheme(onChange) {
  const media = globalThis.matchMedia?.("(prefers-color-scheme: dark)");
  if (!media?.addEventListener) return () => {};
  const listener = () => {
    if (savedThemePreference() !== "system") return;
    applyTheme("system", globalThis.document?.documentElement, media);
    onChange?.();
  };
  media.addEventListener("change", listener);
  return () => media.removeEventListener("change", listener);
}
