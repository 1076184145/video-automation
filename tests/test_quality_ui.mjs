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
const { localizedErrorMessage } = await import("../web/js/i18n.js");
const { renderLocalPreferences } = await import("../web/js/settings.js");

test("quality gate renders blocking and advisory checks with accessible status", () => {
  const html = renderQualityGate({
    status: "blocked",
    blocking: [{ code: "render_missing", message: "A final or review video is required." }],
    advisory: [{ code: "audio_loudness_missing", message: "Audio loudness metadata is unavailable; listen before publishing." }],
    passed: [],
  });
  assert.match(html, /role="alert"/);
  assert.match(html, /需要先生成最终视频或审核预览视频/);
  assert.match(html, /缺少音量响度数据/);
  assert.doesNotMatch(html, /A final or review video is required/);
});

test("quality gate preserves unknown backend messages", () => {
  const html = renderQualityGate({
    status: "blocked",
    blocking: [{ code: "custom_check", message: "自定义检查失败" }],
    advisory: [],
    passed: [],
  });
  assert.match(html, /自定义检查失败/);
});

test("structured approval errors use the active interface language", () => {
  const error = new Error("Quality checks must be resolved before approval");
  error.payload = { error: { code: "quality_gate_failed" } };
  assert.equal(localizedErrorMessage(error), "请先处理所有质量检查阻塞项，再通过审核。");
});

test("unknown structured errors preserve the backend message", () => {
  const error = new Error("服务暂时不可用");
  error.payload = { error: { code: "unknown_error" } };
  assert.equal(localizedErrorMessage(error), "服务暂时不可用");
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
