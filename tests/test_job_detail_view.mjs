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

const {
  normalizeJobDetailPayload,
  renderStageTimings,
  renderJobDetailShell,
  selectPreviewName,
} = await import("../web/js/job-detail-view.js");

test("renderJobDetailShell exposes stable mount points for detail panels", () => {
  const html = renderJobDetailShell();

  for (const id of [
    "section-head",
    "section-preview",
    "section-stage-timings",
    "section-covers",
    "section-enhancements",
    "section-transcript",
    "section-clips",
    "section-downloads",
  ]) {
    assert.match(html, new RegExp(`id="${id}"`));
  }

  for (const tab of ["review", "enhance", "export", "advanced"]) {
    assert.match(html, new RegExp(`id="job-workspace-${tab}"`));
    assert.match(html, new RegExp(`data-job-workspace-panel="${tab}"`));
  }
});

test("selectPreviewName prefers lightweight preview before review and final", () => {
  assert.equal(
    selectPreviewName(new Map([
      ["final.mp4", {}],
      ["review.mp4", {}],
      ["web_preview.mp4", {}],
    ])),
    "web_preview.mp4",
  );
  assert.equal(selectPreviewName(new Map([["final.mp4", {}], ["review.mp4", {}]])), "review.mp4");
  assert.equal(selectPreviewName(new Map([["final.mp4", {}]])), "final.mp4");
  assert.equal(selectPreviewName(new Map()), "");
});

test("normalizeJobDetailPayload tolerates a newly submitted job without artifacts", () => {
  const payload = normalizeJobDetailPayload({
    manifest: null,
    cuts: null,
    transcript: null,
  });

  assert.deepEqual(payload.manifest, {});
  assert.deepEqual(payload.cuts, {});
  assert.deepEqual(payload.transcript, {});
});

test("renderStageTimings shows the slowest completed stages first", () => {
  const html = renderStageTimings({
    stages: [
      { stage: "probe", status: "complete", duration_seconds: 1.2 },
      {
        stage: "transcribe",
        status: "complete",
        duration_seconds: 92.4,
        resource_wait_seconds: 12,
        execution_seconds: 80.4,
      },
      { stage: "detect_freeze", status: "skipped", reason: "disabled" },
      { stage: "render_final", status: "complete", duration_seconds: 31.1 },
    ],
  });

  assert.match(html, /Transcribe/);
  assert.match(html, /Render Final/);
  assert.doesNotMatch(html, /Detect Freeze/);
  assert.ok(html.indexOf("Transcribe") < html.indexOf("Render Final"));
  assert.match(html, /Resource wait 0:12/);
  assert.match(html, /Execution 1:20/);
});

test("renderStageTimings escapes unknown stage labels", () => {
  const html = renderStageTimings({
    stages: [
      { stage: `"><img src=x onerror=alert(1)>`, status: "complete", duration_seconds: 1 },
    ],
  });

  assert.doesNotMatch(html, /<img/);
  assert.match(html, /&lt;img/);
});
