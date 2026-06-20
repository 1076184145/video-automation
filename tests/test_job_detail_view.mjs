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
  renderJobDetailShell,
  selectPreviewName,
} = await import("../web/js/job-detail-view.js");

test("renderJobDetailShell exposes stable mount points for detail panels", () => {
  const html = renderJobDetailShell();

  for (const id of [
    "section-head",
    "section-preview",
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
