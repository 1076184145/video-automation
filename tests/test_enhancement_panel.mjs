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
  metadataTemplateForTest,
  renderPlatformChecksForTest,
  renderSegmentsPanelForTest,
} = await import(
  "../web/js/enhancement-panel.js"
);

test("metadata template exposes editable metadata fields", () => {
  assert.deepEqual(metadataTemplateForTest(), {
    titles: [],
    descriptions: [],
    tags: [],
    hashtags: [],
    cover_titles: [],
    platform_notes: [],
  });
});

test("publish package platform checks default to douyin and bilibili", () => {
  const html = renderPlatformChecksForTest("publish");

  assert.match(html, /value="douyin" checked/);
  assert.match(html, /value="bilibili" checked/);
  assert.match(html, /value="youtube_shorts"/);
});

test("large segment result sets are collapsed by platform", () => {
  const files = new Map();
  const segments = Array.from({ length: 10 }, (_, index) => {
    const file = `segments/douyin_part_${String(index + 1).padStart(2, "0")}.mp4`;
    files.set(file, { name: file });
    return { file, duration: 30 };
  });

  const html = renderSegmentsPanelForTest("example", files, {
    platforms: [{ name: "douyin", segment_count: segments.length, segments }],
  });

  assert.match(html, /class="enhancement-result-details"/);
  assert.match(html, /Douyin · 10/);
  assert.match(html, /douyin_part_10.mp4/);
});
