import assert from "node:assert/strict";
import test from "node:test";

globalThis.localStorage = {
  getItem() {
    return "en";
  },
  setItem() {},
};

Object.defineProperty(globalThis, "navigator", {
  configurable: true,
  value: { language: "en", platform: "test" },
});

globalThis.window = {
  confirm() {
    return true;
  },
  addEventListener() {},
};

globalThis.document = {
  querySelectorAll() {
    return [];
  },
  getElementById() {
    return null;
  },
};

const { coverKeyStatus, renderCovers } = await import("../web/js/cover-panel.js");

test("cover key status follows the selected provider", () => {
  assert.deepEqual(
    coverKeyStatus({ covers: { provider: "openai", openai_api_key_configured: true } }),
    { missing: false, messageKey: "cover.key_missing_openai" }
  );

  assert.deepEqual(
    coverKeyStatus({ covers: { provider: "openrouter", openai_api_key_configured: true } }),
    { missing: true, messageKey: "cover.key_missing_openrouter" }
  );

  assert.deepEqual(
    coverKeyStatus({ covers: { provider: "openrouter", cover_api_key_configured: true } }),
    { missing: false, messageKey: "cover.key_missing_openrouter" }
  );

  assert.deepEqual(
    coverKeyStatus({
      covers: { provider: "google" },
      optional_modules: { google_api_key_configured: true },
    }),
    { missing: false, messageKey: "cover.key_missing_google" }
  );

  assert.deepEqual(
    coverKeyStatus({ covers: { provider: "google" } }),
    { missing: true, messageKey: "cover.key_missing_google" }
  );

  assert.deepEqual(
    coverKeyStatus({ covers: { provider: "local" } }),
    { missing: false, messageKey: "" }
  );
});

test("local cover mode is enabled without an API key and shows a local-only notice", () => {
  const html = renderCovers(
    "job",
    new Map(),
    null,
    {},
    {},
    {},
    { settings: { covers: { provider: "local" } } },
  );

  assert.match(html, /processed by the configured image model on this machine/i);
  assert.doesNotMatch(html, /allowed to leave the local machine/i);
  assert.doesNotMatch(html, /id="generate-covers"[^>]*disabled/);
});
