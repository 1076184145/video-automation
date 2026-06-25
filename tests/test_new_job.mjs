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

const {
  builtInProfileForTest,
  localPathFromFileForTest,
  renderNewJobFormForTest,
  shouldConfirmBrowserUploadForTest,
} = await import("../web/js/new-job.js");

test("new job keeps the primary path visible and collapses secondary input methods", () => {
  const html = renderNewJobFormForTest();

  assert.match(html, /id="upload-dropzone"/);
  assert.match(html, /id="source-path"/);
  assert.match(html, /<details class="new-job-disclosure source-tools" id="new-job-source-tools">/);
  assert.match(html, /id="recording-picker"/);
  assert.doesNotMatch(html, /id="download-box"/);
  assert.doesNotMatch(html, /id="live-box"/);
  assert.doesNotMatch(html, /id="download-url"/);
  assert.doesNotMatch(html, /id="live-url"/);
});

test("new job collapses low-frequency processing controls and uses a three-step rail", () => {
  const html = renderNewJobFormForTest();

  assert.match(html, /<details class="new-job-disclosure processing-options" id="new-job-processing-options">/);
  assert.match(html, /name="detect_silence"/);
  assert.match(html, /id="save-current-profile"/);
  assert.equal((html.match(/data-wizard-target=/g) || []).length, 3);
  assert.doesNotMatch(html, /id="new-step-ai"/);
});

test("new job restores disclosure preferences without changing the main workflow", () => {
  const html = renderNewJobFormForTest({
    sourceTools: true,
    processingOptions: true,
  });

  assert.match(html, /id="new-job-source-tools" open>/);
  assert.match(html, /id="new-job-processing-options" open>/);
  assert.match(html, /id="workflow-profile"/);
  assert.match(html, /id="new-job-summary"/);
  assert.match(html, /type="submit"/);
});

test("new job uses a compact two-column desktop flow and returns to one column on mobile", () => {
  const css = readFileSync(new URL("../web/css/style.css", import.meta.url), "utf8");

  assert.match(css, /\.new-job-wizard\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1\.08fr\)\s+minmax\(0,\s*0\.92fr\)/s);
  assert.match(css, /\.wizard-step-run\s*\{[^}]*grid-column:\s*1\s*\/\s*-1/s);
  assert.match(css, /\.wizard-summary-grid\s*\{[^}]*grid-template-columns:\s*repeat\(4,\s*minmax\(0,\s*1fr\)\)/s);
  assert.match(css, /@media\s*\(max-width:\s*860px\)[\s\S]*?\.new-job-wizard\s*\{[^}]*grid-template-columns:\s*1fr/s);
});

test("one-click profiles avoid a redundant review render", () => {
  for (const profile of ["fast", "douyin", "bilibili", "youtube_shorts"]) {
    const payload = builtInProfileForTest(profile);
    assert.equal(payload.render_final, true);
    assert.equal(payload.render_review, false);
  }
});

test("fast profile favors speed over optional deep analysis", () => {
  const html = renderNewJobFormForTest();
  const payload = builtInProfileForTest("fast");

  assert.match(html, /value="fast"/);
  assert.equal(payload.source_integrity_scan, false);
  assert.equal(payload.detect_silence, true);
  assert.equal(payload.detect_scenes, true);
  assert.equal(payload.detect_freeze, false);
  assert.equal(payload.plan_crop, false);
  assert.equal(payload.burn_subtitles, true);
});

test("large browser drag uploads ask for confirmation before copying", () => {
  const largeBrowserFile = { name: "recording.mp4", size: 2 * 1024 * 1024 * 1024, type: "video/mp4" };
  const desktopFile = { name: "recording.mp4", size: 2 * 1024 * 1024 * 1024, type: "video/mp4", path: "D:\\recordings\\recording.mp4" };

  assert.equal(shouldConfirmBrowserUploadForTest([largeBrowserFile]), true);
  assert.equal(shouldConfirmBrowserUploadForTest([desktopFile]), false);
  assert.equal(shouldConfirmBrowserUploadForTest([desktopFile, largeBrowserFile]), true);
  assert.equal(localPathFromFileForTest(desktopFile), "D:\\recordings\\recording.mp4");
});

test("small browser drag can copy without an extra confirmation step", () => {
  const smallBrowserFile = { name: "clip.mp4", size: 30 * 1024 * 1024, type: "video/mp4" };

  assert.equal(shouldConfirmBrowserUploadForTest([smallBrowserFile]), false);
});
