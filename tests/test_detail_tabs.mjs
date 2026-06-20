import assert from "node:assert/strict";
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

const {
  defaultJobDetailTab,
  jobDetailTabStorageKey,
  renderJobDetailTabs,
} = await import("../web/js/detail-tabs.js");

test("completed jobs open export while review jobs open the editor", () => {
  assert.equal(defaultJobDetailTab("done"), "export");
  assert.equal(defaultJobDetailTab("needs_review"), "review");
  assert.equal(defaultJobDetailTab("transcribing"), "review");
});

test("job detail tab selection is persisted per job", () => {
  assert.equal(
    jobDetailTabStorageKey("20260612-example"),
    "videoAutomationJobDetailTab:20260612-example",
  );
});

test("job detail workspace renders four accessible tabs", () => {
  const html = renderJobDetailTabs();

  for (const tab of ["review", "enhance", "export", "advanced"]) {
    assert.match(html, new RegExp(`data-job-workspace-tab="${tab}"`));
    assert.match(html, new RegExp(`aria-controls="job-workspace-${tab}"`));
  }
  assert.match(html, /role="tablist"/);
});
