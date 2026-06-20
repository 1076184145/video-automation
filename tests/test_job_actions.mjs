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
  addEventListener() {},
};

const { renderJobActions, renderJobError, renderSourceWarning } = await import(
  "../web/js/job-actions.js"
);

test("renderJobActions exposes rerun stage controls and delete action", () => {
  const html = renderJobActions();

  assert.match(html, /id="rerun-stage"/);
  assert.match(html, /value="transcribe"/);
  assert.match(html, /id="delete-job"/);
});

test("renderSourceWarning keeps decoder errors inside collapsed technical details", () => {
  const html = renderSourceWarning({
    status: "corrupt",
    error_count: 2,
    first_error_at_seconds: 12,
    errors: ["bad <frame>"],
  });

  assert.match(html, /source-warning/);
  assert.match(html, /2 decode or timestamp warnings/);
  assert.match(html, /First issue near 0:12/);
  assert.match(html, /<details class="source-warning-details">/);
  assert.doesNotMatch(html, /<details class="source-warning-details" open/);
  assert.match(html, /<summary>Technical details<\/summary>[\s\S]*bad &lt;frame&gt;/);
});

test("renderJobError renders encoded recovery actions", () => {
  const html = renderJobError({
    error: "CUDA out of memory",
    error_advice: {
      title: "GPU memory is insufficient",
      summary: "Use a smaller model.",
      actions: [{ type: "rerun_stage", stage: "transcribe", label: "Retry" }],
    },
  });

  assert.match(html, /GPU memory is insufficient/);
  assert.match(html, /data-error-action=/);
  assert.match(html, /Retry/);
});
