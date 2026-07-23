import assert from "node:assert/strict";
import test from "node:test";

globalThis.localStorage = {
  getItem() { return "zh"; },
  setItem() {},
  removeItem() {},
};

Object.defineProperty(globalThis, "navigator", {
  configurable: true,
  value: { language: "zh-CN", platform: "test" },
});

globalThis.window = { addEventListener() {} };

const { API } = await import("../web/js/api.js");
const {
  legacyProfilesToRecipes,
  renderQueuePanel,
} = await import("../web/js/automation.js");
const { renderNewJobFormForTest } = await import("../web/js/new-job.js");
const { shouldUpdateQueueForTest } = await import("../web/js/dashboard.js");

test("queue panel exposes global and per-item recovery controls", () => {
  const html = renderQueuePanel({
    paused: false,
    items: [
      {
        id: "queue-one",
        job_name: "job-one",
        status: "failed",
        priority: 8,
        retry_stage: null,
        error: "GPU memory exhausted",
      },
    ],
  });

  assert.match(html, /智能队列/);
  assert.match(html, /data-queue-global="pause"/);
  assert.match(html, /data-queue-action="retry-stage"/);
  assert.match(html, /data-queue-action="cancel"/);
  assert.match(html, /GPU memory exhausted/);
});

test("running queue item shows canceling feedback after cancellation is requested", () => {
  const html = renderQueuePanel({
    paused: false,
    items: [{
      id: "queue-running",
      job_name: "job-running",
      status: "running",
      cancel_requested: true,
    }],
  });

  assert.match(html, /正在取消/);
  assert.doesNotMatch(html, /data-queue-action="cancel"/);
});

test("legacy browser profiles convert to idempotent server recipes", () => {
  const recipes = legacyProfilesToRecipes([
    {
      id: "old-profile-1",
      name: "旧 B站配置",
      payload: { detect_silence: true, render_final: true, vertical: false },
    },
  ]);

  assert.equal(recipes[0].client_id, "old-profile-1");
  assert.equal(recipes[0].name, "旧 B站配置");
  assert.equal(recipes[0].options.detect_silence, true);
  assert.ok(recipes[0].stages.includes("detect_silence"));
  assert.ok(recipes[0].stages.includes("refine_cuts"));
  assert.ok(recipes[0].stages.includes("render_final"));
});

test("new job selector renders server recipes and submits stable recipe ids", () => {
  const html = renderNewJobFormForTest({}, {
    projects: [],
    kits: [],
    recipes: [{ id: "recipe-one", name: "服务器配方", options: {} }],
  });

  assert.match(html, /value="recipe:recipe-one"/);
  assert.match(html, /服务器配方/);
});

test("API client exposes recipe and smart queue controls", () => {
  for (const method of [
    "getRecipes", "createRecipe", "deleteRecipe", "importRecipes",
    "getQueue", "pauseQueue", "resumeQueue", "pauseQueueItem",
    "resumeQueueItem", "cancelQueueItem", "retryQueueStage", "reorderQueue",
    "cancelJob",
  ]) {
    assert.equal(typeof API[method], "function", method);
  }
});

test("queue rerenders after returning to an empty dashboard mount", () => {
  assert.equal(shouldUpdateQueueForTest("same", "same", false), true);
  assert.equal(shouldUpdateQueueForTest("same", "same", true), false);
});
