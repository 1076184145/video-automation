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

const { coverKeyStatus } = await import("../web/js/cover-panel.js");

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
});
