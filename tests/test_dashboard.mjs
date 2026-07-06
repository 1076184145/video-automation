import assert from "node:assert/strict";
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

const { renderDashboardJobsForTest } = await import("../web/js/dashboard.js");

const jobs = [
  {
    job_dir: "D:/jobs/done-job",
    source_path: "D:/videos/done.mp4",
    status: "done",
    created_at: "2026-06-01T10:00:00Z",
    updated_at: "2026-06-01T11:00:00Z",
    files: [],
  },
  {
    job_dir: "D:/jobs/review-job",
    source_path: "D:/videos/review.mp4",
    status: "needs_review",
    created_at: "2026-06-02T10:00:00Z",
    updated_at: "2026-06-02T11:00:00Z",
    files: [],
  },
  {
    job_dir: "D:/jobs/failed-job",
    source_path: "D:/videos/failed.mp4",
    status: "failed",
    created_at: "2026-06-03T10:00:00Z",
    updated_at: "2026-06-03T11:00:00Z",
    files: [],
  },
];

test("dashboard defaults to actionable work and collapses completed history", () => {
  const html = renderDashboardJobsForTest(jobs);

  assert.match(html, /class="dashboard-actionable"/);
  assert.match(html, /待你审核/);
  assert.match(html, /review\.mp4/);
  assert.match(html, /failed\.mp4/);
  assert.match(html, /<details class="dashboard-history">/);
  assert.match(html, /done\.mp4/);
});

test("dashboard status filters keep a flat focused result list", () => {
  const html = renderDashboardJobsForTest(jobs, { filter: "done" });

  assert.match(html, /done\.mp4/);
  assert.doesNotMatch(html, /review\.mp4/);
  assert.doesNotMatch(html, /dashboard-history/);
});

test("dashboard cards use creator actions instead of raw pipeline status codes", () => {
  const html = renderDashboardJobsForTest(jobs);

  assert.match(html, /去审核/);
  assert.match(html, /查看并修复/);
  assert.match(html, /查看成片/);
  assert.doesNotMatch(html, /当前阶段/);
  assert.doesNotMatch(html, />needs_review</);
});

test("dashboard only renders delete controls for completed jobs", () => {
  const html = renderDashboardJobsForTest(jobs);

  assert.match(html, /data-delete-job="done-job"/);
  assert.doesNotMatch(html, /data-delete-job="review-job"/);
  assert.doesNotMatch(html, /data-delete-job="failed-job"/);
  assert.match(html, /aria-label="删除已完成任务"/);
});

test("dashboard empty actionable state still exposes completed history", () => {
  const html = renderDashboardJobsForTest([jobs[0]]);

  assert.match(html, /当前没有待处理任务/);
  assert.match(html, /dashboard-history/);
  assert.match(html, /done\.mp4/);
});

test("dashboard groups jobs from the same batch behind one summary", () => {
  const batchedJobs = [
    {
      ...jobs[1],
      job_dir: "D:/jobs/batch-review",
      source_path: "D:/videos/batch-review.mp4",
      batch_id: "batch-20260612-demo",
      batch_index: 1,
      batch_size: 2,
    },
    {
      ...jobs[2],
      job_dir: "D:/jobs/batch-failed",
      source_path: "D:/videos/batch-failed.mp4",
      batch_id: "batch-20260612-demo",
      batch_index: 2,
      batch_size: 2,
    },
  ];

  const html = renderDashboardJobsForTest(batchedJobs);

  assert.match(html, /<details class="dashboard-batch"/);
  assert.match(html, /批量任务/);
  assert.match(html, /2 个视频/);
  assert.match(html, /batch-review\.mp4/);
  assert.match(html, /batch-failed\.mp4/);
});

test("dashboard leaves singleton batch metadata as a normal job card", () => {
  const html = renderDashboardJobsForTest([
    {
      ...jobs[1],
      batch_id: "batch-single",
      batch_index: 1,
      batch_size: 1,
    },
  ]);

  assert.doesNotMatch(html, /dashboard-batch/);
  assert.match(html, /review\.mp4/);
});

test("review inbox separates processing work and shows project context", () => {
  const html = renderDashboardJobsForTest([
    jobs[1],
    {
      ...jobs[1],
      job_dir: "D:/jobs/processing-job",
      source_path: "D:/videos/processing.mp4",
      status: "transcribing",
      project_id: "project-one",
      stage_progress: 42,
    },
    jobs[0],
  ], {
    projects: [{ id: "project-one", name: "每周直播精选" }],
  });

  assert.match(html, /class="dashboard-processing"/);
  assert.match(html, /processing\.mp4/);
  assert.match(html, /每周直播精选/);
  assert.match(html, /最近完成/);
});
