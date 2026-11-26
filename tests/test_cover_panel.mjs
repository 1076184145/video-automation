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

test("ready frame fallback is offered when the backend has no provider key", () => {
  const settings = { covers: { provider: "openai" } };
  const checks = [{ name: "cover_api_key", exists: false }, { name: "pillow", exists: true }];
  const render = (health, cover = null) => renderCovers("job", new Map(), cover, {}, {}, {}, health);

  const fallback = render({ settings, checks });
  assert.match(fallback, /<h2>Frame covers<\/h2>/);
  assert.match(fallback, /id="generate-covers"[^>]*data-frame-only="true"[^>]*>Generate frame covers/);
  assert.doesNotMatch(fallback, /id="generate-covers"[^>]*disabled|allowed to leave the local machine/i);

  for (const unavailable of [
    checks.map((check) => ({ ...check, exists: check.name === "cover_api_key" })),
    [...checks, { name: "local_cover_model", exists: true }],
  ]) {
    assert.match(render({ settings, checks: unavailable }), /id="generate-covers"[^>]*disabled/);
  }
  const previouslyGenerated = render(
    { settings: { covers: { provider: "openai", cover_api_key_configured: true } } },
    { status: "ready", generator: "fallback_frame_composite" },
  );
  assert.match(previouslyGenerated, /<h2>Frame covers<\/h2>/);
  assert.match(previouslyGenerated, /id="generate-covers"[^>]*>Generate covers/);
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
