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

const { renderDownloadsSection } = await import("../web/js/download-section.js");

test("large project output groups keep a short primary list and collapse overflow", () => {
  const files = new Map();
  for (let index = 1; index <= 12; index += 1) {
    const name = `segments/douyin_part_${String(index).padStart(2, "0")}.mp4`;
    files.set(name, { name, path: `D:\\jobs\\example\\${name}` });
  }

  const html = renderDownloadsSection("example", files);

  assert.match(html, /class="download-more"/);
  assert.match(html, /Show all \(12\)/);
  assert.ok(html.indexOf("douyin_part_06.mp4") < html.indexOf("class=\"download-more\""));
  assert.ok(html.indexOf("douyin_part_12.mp4") > html.indexOf("class=\"download-more\""));
});
