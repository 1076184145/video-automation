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
  selectRecordingPaths,
  deleteRecordingSelection,
} = await import("../web/js/new-job.js");
const { batchListHtml } = await import("../web/js/new-job-view.js");

test("upload progress reuses its notice and updates text, width and accessibility value", async () => {
  const { updateUploadProgress } = await import("../web/js/new-job-view.js");
  let replacements = 0;
  let markup = "";
  const label = { textContent: "" };
  const value = { textContent: "" };
  const bar = { setAttribute(name, text) { this[name] = text; } };
  const fill = { style: {} };
  const container = {
    set innerHTML(html) { replacements++; markup = html; },
    querySelector(selector) {
      return {
        "[data-upload-progress-notice]": replacements ? bar : null,
        ".upload-progress-head span": label,
        ".upload-progress-head strong": value,
        '[role="progressbar"]': bar,
        ".upload-progress > span": fill,
      }[selector];
    },
  };
  for (const percent of [0, 1, 38, 38, 70, 100]) updateUploadProgress(container, "0/1", percent);
  assert.equal(replacements, 1);
  assert.match(markup, /data-motion-seen="1"/);
  assert.equal(value.textContent, "100%");
  assert.equal(fill.style.width, "100%");
  assert.equal(bar["aria-valuenow"], "100");
  updateUploadProgress(container, "1/2", 50);
  assert.match(label.textContent, /1\/2/);
  assert.equal(replacements, 1);
  // A terminal message replaces the progress notice; the next upload creates a new one.
  replacements = 0;
  updateUploadProgress(container, "0/1", 0);
  assert.equal(replacements, 1);
});

test("batch deletion runs serially and retains individual failures", async () => {
  const files = ["a", "b", "c"].map((name) => ({ relative_path: `${name}.mp4`, path: name }));
  let active = 0;
  const calls = [];
  const result = await deleteRecordingSelection(files, async (path) => {
    assert.equal(active++, 0);
    calls.push(path);
    await Promise.resolve();
    active--;
    if (path === "b.mp4") throw new Error("referenced by job");
  });
  assert.deepEqual(calls, ["a.mp4", "b.mp4", "c.mp4"]);
  assert.deepEqual(result.deleted, [files[0], files[2]]);
  assert.deepEqual(result.failed, [{ file: files[1], error: "referenced by job" }]);
});

test("batch deletion stops sending requests when leaving the page", async () => {
  const result = await deleteRecordingSelection([{ relative_path: "a.mp4" }], () => assert.fail("must not delete"), () => true);
  assert.deepEqual(result, { deleted: [], failed: [] });
});

test("select all includes collapsed recordings, deduplicates and preserves existing selections", () => {
  const recordings = Array.from({ length: 20 }, (_, i) => ({ path: `video-${i}.mp4` }));
  const result = selectRecordingPaths(["other.mp4", "video-0.mp4"], recordings, 30);
  assert.equal(result.paths.length, 21);
  assert.equal(result.added, 19);
  assert.equal(result.skipped, 0);
  assert.equal(selectRecordingPaths(result.paths, recordings, 30).added, 0);
});

test("select all respects batch capacity and reports omitted files", () => {
  const result = selectRecordingPaths(["existing.mp4"], [{ path: "a.mp4" }, { path: "b.mp4" }, { path: "b.mp4" }], 2);
  assert.deepEqual(result, { paths: ["existing.mp4", "a.mp4"], added: 1, skipped: 1 });
  assert.deepEqual(selectRecordingPaths([], [], 30), { paths: [], added: 0, skipped: 0 });
});

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
  assert.equal((html.match(/class="wizard-step-index">[123]</g) || []).length, 3);
  assert.doesNotMatch(html, /class="wizard-step-index">0[123]</);
  assert.doesNotMatch(html, /id="new-step-ai"/);
});

test("batch items use an accessible one-click close control", () => {
  const html = batchListHtml(["D:\\recordings\\sample.mp4"], 30);

  assert.match(html, /class="batch-remove-button"/);
  assert.match(html, /data-remove-batch=/);
  assert.match(html, /aria-label="移除"/);
  assert.match(html, />×<\/button>/);
  assert.doesNotMatch(html, />移除<\/button>/);
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
