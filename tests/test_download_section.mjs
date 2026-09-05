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

const { renderDownloadsSection } = await import("../web/js/download-section.js");
const stylesheet = readFileSync(new URL("../web/css/style.css", import.meta.url), "utf8");

test("primary video download keeps contrasting text after file-kind styles", () => {
  const files = new Map([
    ["final.mp4", { name: "final.mp4", path: "D:\\jobs\\example\\final.mp4" }],
  ]);

  const html = renderDownloadsSection("example", files);
  const fileVideoRuleIndex = stylesheet.indexOf(".download-link.file-video");
  const primaryRuleIndex = stylesheet.indexOf(".button.download-link.primary");
  const primaryRuleEnd = stylesheet.indexOf("}", primaryRuleIndex);

  assert.match(html, /class="button download-link file-video primary"/);
  assert.ok(fileVideoRuleIndex >= 0);
  assert.ok(primaryRuleIndex > fileVideoRuleIndex);
  assert.match(
    stylesheet.slice(primaryRuleIndex, primaryRuleEnd + 1),
    /color:\s*var\(--bg-base\)/,
  );
});

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
