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

const { providerErrorCode, providerErrorMessageKey } = await import("../web/js/provider-errors.js");
const { renderCoverProviderError } = await import("../web/js/cover-panel.js");
const { llmConfigurationStatus } = await import("../web/js/enhancement-panel.js");

test("provider errors prefer stable backend codes and recognize legacy responses", () => {
  assert.equal(
    providerErrorCode("OpenRouter failed: User not found."),
    "credentials_invalid"
  );
  assert.equal(
    providerErrorCode("anything", "quota_exhausted"),
    "quota_exhausted"
  );
  assert.equal(
    providerErrorMessageKey("OpenAI failed [model_missing]: missing"),
    "ai.error.model_missing"
  );
});

test("cover provider errors show actionable text with collapsible technical details", () => {
  const html = renderCoverProviderError({
    error_code: "credentials_invalid",
    error: "OpenRouter image generation failed [credentials_invalid] (HTTP 401): User not found.",
  });

  assert.match(html, /provider rejected the current credentials/i);
  assert.match(html, /Technical details/);
  assert.match(html, /User not found/);
});

test("LLM preflight distinguishes missing model, provider key, and ready state", () => {
  assert.deepEqual(
    llmConfigurationStatus({
      optional_modules: { llm_provider: "openai", llm_model: "" },
      covers: { openai_api_key_configured: true },
    }),
    { configured: false, code: "model_missing", messageKey: "ai.error.llm_model_missing" }
  );
  assert.deepEqual(
    llmConfigurationStatus({
      optional_modules: { llm_provider: "openai", llm_model: "gpt-test" },
      covers: { openai_api_key_configured: false },
    }),
    { configured: false, code: "credentials_missing", messageKey: "ai.error.credentials_missing_openai" }
  );
  assert.deepEqual(
    llmConfigurationStatus({
      optional_modules: { llm_provider: "google", llm_model: "gemini-test", google_api_key_configured: true },
    }),
    { configured: true, code: "", messageKey: "" }
  );
  assert.deepEqual(
    llmConfigurationStatus({
      optional_modules: {
        llm_provider: "local",
        llm_model: "local-text-model",
        local_llm_model_path: "models\\local-text\\model.gguf",
      },
    }),
    { configured: true, code: "", messageKey: "", local: true }
  );
});
