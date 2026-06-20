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

const { builtInProfileForTest, renderNewJobFormForTest } = await import("../web/js/new-job.js");

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
  for (const profile of ["douyin", "bilibili", "youtube_shorts"]) {
    const payload = builtInProfileForTest(profile);
    assert.equal(payload.render_final, true);
    assert.equal(payload.render_review, false);
  }
});
