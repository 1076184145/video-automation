import assert from "node:assert/strict";
import test from "node:test";

globalThis.localStorage = { getItem() { return "zh"; }, setItem() {} };
Object.defineProperty(globalThis, "navigator", {
  configurable: true,
  value: { language: "zh-CN", platform: "test" },
});
globalThis.window = { addEventListener() {} };

const { API } = await import("../web/js/api.js");
const { renderQualityGate } = await import("../web/js/job-actions.js");
const { renderLocalPreferences } = await import("../web/js/settings.js");

test("quality gate renders blocking and advisory checks with accessible status", () => {
  const html = renderQualityGate({
    status: "blocked",
    blocking: [{ code: "aspect_ratio", message: "画幅不匹配", expected: "9:16", actual: "1920:1080" }],
    advisory: [{ code: "audio_loudness_missing", message: "建议试听音量" }],
    passed: [],
  });
  assert.match(html, /role="alert"/);
  assert.match(html, /画幅不匹配/);
  assert.match(html, /9:16/);
  assert.match(html, /建议试听音量/);
});

test("settings exposes local preference export, clear, and health entry", () => {
  const html = renderLocalPreferences({
    event_count: 4,
    clip_feedback: { accepted: 2, rejected: 1 },
    subtitle_replacements: { "old": "new" },
    platforms: { bilibili: 1 },
  });
  assert.match(html, /data-preferences-action="export"/);
  assert.match(html, /data-preferences-action="clear"/);
  assert.match(html, /#\/health/);
  assert.match(html, /Bilibili/i);
});

test("API exposes quality gate and local preference controls", () => {
  for (const method of ["getJobQuality", "getPreferences", "exportPreferences", "clearPreferences"]) {
    assert.equal(typeof API[method], "function", method);
  }
});
