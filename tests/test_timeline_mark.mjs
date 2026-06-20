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

const { findTimelineMarkForTest } = await import("../web/js/timeline.js");

test("findTimelineMarkForTest detects a wide mark beyond narrower neighbors", () => {
  const wide = { x: 0, y: 20, w: 300, h: 40, label: "wide", start: 0, end: 30 };
  const narrow = { x: 80, y: 80, w: 20, h: 20, label: "narrow", start: 8, end: 9 };
  const found = findTimelineMarkForTest([wide, narrow], 250, 30);
  assert.equal(found.label, "wide");
});

test("findTimelineMarkForTest keeps draw-order priority for overlapping marks", () => {
  const bottom = { x: 0, y: 20, w: 300, h: 40, label: "bottom", start: 0, end: 30 };
  const top = { x: 180, y: 20, w: 80, h: 40, label: "top", start: 18, end: 22 };
  const found = findTimelineMarkForTest([bottom, top], 200, 30);
  assert.equal(found.label, "top");
});
