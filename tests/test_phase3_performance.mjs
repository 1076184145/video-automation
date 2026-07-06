import assert from "node:assert/strict";
import test from "node:test";

globalThis.localStorage = { getItem() { return "en"; }, setItem() {}, removeItem() {} };
Object.defineProperty(globalThis, "navigator", { configurable: true, value: { language: "en", platform: "test" } });
globalThis.window = { addEventListener() {} };

const { renderTranscript, TRANSCRIPT_PAGE_SIZE } = await import("../web/js/transcript-editor.js");
const { renderDashboardJobsForTest, MAX_RENDERED_JOBS } = await import("../web/js/dashboard.js");

test("5000 subtitle entries render a bounded DOM window", () => {
  const segments = Array.from({ length: 5000 }, (_, index) => ({
    start: index,
    end: index + 1,
    text: `subtitle ${index}`,
  }));
  const started = performance.now();
  const html = renderTranscript({ segments });
  const elapsed = performance.now() - started;
  const renderedRows = (html.match(/data-transcript-row/g) || []).length;

  assert.equal(TRANSCRIPT_PAGE_SIZE, 200);
  assert.equal(renderedRows, 200);
  assert.match(html, /data-transcript-data/);
  assert.ok(elapsed < 100, `render took ${elapsed.toFixed(1)}ms`);
});

test("1000 task dashboard search stays bounded by a 100-node render window", () => {
  const jobs = Array.from({ length: 1000 }, (_, index) => ({
    job_dir: `job-${index}`,
    source_path: `C:/recordings/video-${index}.mp4`,
    status: "done",
    updated_at: `2026-07-05T10:${String(index % 60).padStart(2, "0")}:00`,
  }));
  const started = performance.now();
  const html = renderDashboardJobsForTest(jobs, { filter: "all", search: "" });
  const elapsed = performance.now() - started;
  assert.equal(MAX_RENDERED_JOBS, 100);
  assert.ok((html.match(/class="card job-card/g) || []).length <= 100);
  assert.ok(elapsed < 100, `dashboard render took ${elapsed.toFixed(1)}ms`);

  const searchStarted = performance.now();
  const searched = renderDashboardJobsForTest(jobs, { filter: "all", search: "video-999" });
  assert.match(searched, /video-999/);
  assert.ok(performance.now() - searchStarted < 100);
});
