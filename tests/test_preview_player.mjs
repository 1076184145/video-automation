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
globalThis.window = { addEventListener() {} };

const { renderVideoControls } = await import("../web/js/preview-player.js");

test("renderVideoControls exposes stable control hooks", () => {
  const html = renderVideoControls();
  assert.match(html, /data-video-controls/);
  assert.match(html, /data-video-play/);
  assert.match(html, /data-video-scrubber/);
  assert.match(html, /data-video-volume/);
  assert.match(html, /data-video-fullscreen/);
});
