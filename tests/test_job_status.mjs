import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
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

const { renderLiveProgress, renderStage } = await import("../web/js/job-status.js");

test("renderStage marks an existing output as completed", () => {
  const html = renderStage("transcribe", { status: "processing" }, new Map([["transcript.json", {}]]));

  assert.match(html, /class="stage done"/);
  assert.match(html, /stage-dot">✓/);
});

test("renderStage marks the active pipeline stage as current", () => {
  const html = renderStage(
    "transcribe",
    { status: "processing", current_stage: "transcribe" },
    new Map(),
  );

  assert.match(html, /class="stage current"/);
});

test("renderLiveProgress exposes percentage and accessible progress state", () => {
  const html = renderLiveProgress({
    status: "processing",
    current_stage: "render_review",
    stage_progress: 42.4,
    stage_message: "Rendering preview",
  });

  assert.match(html, /42%/);
  assert.match(html, /aria-valuenow="42"/);
  assert.match(html, /Rendering preview/);
});

test("renderLiveProgress hides completed progress for terminal job states", () => {
  for (const status of ["needs_review", "done", "failed"]) {
    const html = renderLiveProgress({
      status,
      current_stage: "render_final",
      stage_progress: 100,
      stage_message: "render_final progress 100.0%.",
    });

    assert.equal(html, "", `${status} should not render a completed progress panel`);
  }
});

test("empty live progress mount does not leave a blank panel", () => {
  const css = readFileSync(new URL("../web/css/style.css", import.meta.url), "utf8");

  assert.match(css, /\.live-progress:empty\s*\{\s*display:\s*none;\s*\}/);
});
