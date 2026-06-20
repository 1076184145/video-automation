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

const { renderTimelineLegend, seekPreview } = await import("../web/js/detail-layout.js");

test("renderTimelineLegend includes all timeline signal kinds", () => {
  const html = renderTimelineLegend();

  for (const kind of ["keep", "invalid", "scene", "transcript", "waveform"]) {
    assert.match(html, new RegExp(`legend-swatch ${kind}`));
  }
});

test("seekPreview clamps negative time and focuses the preview", () => {
  let focused = false;
  const video = {
    currentTime: 12,
    focus() {
      focused = true;
    },
  };
  globalThis.document = {
    querySelector() {
      return video;
    },
  };

  seekPreview(-3);

  assert.equal(video.currentTime, 0);
  assert.equal(focused, true);
});
