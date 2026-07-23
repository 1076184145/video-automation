import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

globalThis.localStorage = {
  getItem() {
    return "zh";
  },
  setItem() {},
};

Object.defineProperty(globalThis, "navigator", {
  configurable: true,
  value: { language: "zh-CN", platform: "test" },
});

globalThis.window = {
  addEventListener() {},
};

const { renderHealthPayloadForTest } = await import("../web/js/health.js");

const requiredChecks = [
  { name: "ffmpeg_path", path: "ffmpeg", exists: true, optional: false, status: "ok", version: "7.1" },
  { name: "ffprobe_path", path: "ffprobe", exists: true, optional: false, status: "ok", version: "7.1" },
  { name: "faster_whisper", path: "python:faster_whisper", exists: true, optional: false, status: "ok" },
];

test("healthy environment leads with a ready summary and start action", () => {
  const html = renderHealthPayloadForTest({
    ok: true,
    checks: [
      ...requiredChecks,
      { name: "demucs", path: "demucs", exists: false, optional: true, status: "optional_missing" },
    ],
  });

  assert.match(html, /health-overview ready/);
  assert.match(html, /环境已就绪/);
  assert.match(html, /href="#\/new"/);
  assert.match(html, /开始新任务/);
  assert.doesNotMatch(html, /<details class="panel health-details" open>/);
});

test("missing required tools lead with a repair summary and auto-fix action", () => {
  const html = renderHealthPayloadForTest({
    ok: false,
    checks: [
      { ...requiredChecks[0], exists: false, status: "missing" },
      requiredChecks[1],
    ],
  });

  assert.match(html, /health-overview needs-attention/);
  assert.match(html, /需要修复 1 项/);
  assert.match(html, /FFmpeg/);
  assert.match(html, /id="install-health-tools"/);
  assert.match(html, /一键修复环境/);
});

test("missing transcription modules prioritize Faster-Whisper installation with CLI as temporary fallback", () => {
  const html = renderHealthPayloadForTest({
    ok: false,
    checks: [
      requiredChecks[0],
      requiredChecks[1],
      { name: "whisper_bin", path: "whisper", exists: true, optional: true, status: "ok" },
      { name: "faster_whisper", path: "python:faster_whisper", exists: false, optional: false, status: "missing" },
      { name: "ctranslate2_cuda", path: "python:ctranslate2", exists: false, optional: false, status: "missing" },
    ],
    settings: { whisper: { backend: "faster-whisper" } },
  });

  assert.match(html, /health-recovery-panel/);
  assert.match(html, /转写依赖缺失/);
  assert.match(html, /优先安装 Faster-Whisper/);
  assert.match(html, /medium 主模型与 small 回退模型/);
  assert.match(html, /id="switch-whisper-cli"/);
  assert.match(html, /临时切换到 Whisper CLI/);
  assert.match(html, /python -m pip install -r requirements-transcription-faster\.txt/);
});

test("optional components do not block the ready state", () => {
  const html = renderHealthPayloadForTest({
    ok: true,
    checks: [
      ...requiredChecks,
      { name: "demucs", path: "demucs", exists: false, optional: true, status: "optional_missing" },
    ],
  });

  assert.match(html, /health-overview ready/);
  assert.match(html, /可选组件缺失 1/);
  assert.doesNotMatch(html, /needs-attention/);
});

test("complete diagnostics stay in a collapsed advanced section with friendly labels", () => {
  const html = renderHealthPayloadForTest({
    ok: true,
    checks: requiredChecks,
  });

  assert.match(html, /<details class="panel health-details">/);
  assert.match(html, /完整环境详情/);
  assert.match(html, />FFmpeg</);
  assert.match(html, />FFprobe</);
  assert.doesNotMatch(html, />ffmpeg_path</);
  assert.doesNotMatch(html, />ffprobe_path</);
});

test("health diagnostics keep wide tables inside their own mobile scroller", () => {
  const css = readFileSync(new URL("../web/css/style.css", import.meta.url), "utf8");

  assert.match(css, /\.main\s*\{[^}]*min-width:\s*0/s);
  assert.match(css, /\.health-details\s*\{[^}]*min-width:\s*0/s);
  assert.match(css, /\.health-table-wrap\s*\{[^}]*max-width:\s*100%[^}]*overflow-x:\s*auto/s);
  assert.match(css, /\.health-table-wrap \.table\s*\{[^}]*min-width:\s*720px/s);
});

test("health page keeps remote exposure and disk warnings visible", () => {
  const html = renderHealthPayloadForTest({
    checks: [{ name: "ffmpeg_path", exists: true, optional: false }],
    warnings: [
      { code: "remote_api_exposed", severity: "warning" },
      { code: "low_disk_space", severity: "warning" },
    ],
    storage: {
      available: true,
      path: "D:/video-automation/processing/jobs",
      total_bytes: 100 * 1024 ** 3,
      free_bytes: 512 * 1024 ** 2,
      min_free_bytes: 1024 ** 3,
      low_space: true,
    },
  });

  assert.match(html, /health-warning-panel/);
  assert.match(html, /API_ALLOW_REMOTE/);
  assert.match(html, /health-storage needs-attention/);
  assert.match(html, /512 MiB/);
  assert.match(html, /href="#\/new"/);
});
