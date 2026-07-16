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

const { isJobEventForName, isTypingTarget, parseEventPayload, shouldApplyLiveJobEvent } = await import("../web/js/job-detail-data.js");

test("parseEventPayload returns parsed data and tolerates malformed events", () => {
  assert.deepEqual(parseEventPayload({ data: '{"status":"done"}' }), { status: "done" });
  assert.deepEqual(parseEventPayload({ data: "{invalid" }), {});
});

test("isTypingTarget recognizes interactive descendants", () => {
  assert.equal(isTypingTarget({ closest: () => ({ tagName: "INPUT" }) }), true);
  assert.equal(isTypingTarget({ closest: () => null }), false);
  assert.equal(isTypingTarget(null), false);
});

test("isJobEventForName matches SSE jobs without relying on page globals", () => {
  assert.equal(isJobEventForName({ job_dir: "D:/jobs/example-job" }, "example-job"), true);
  assert.equal(isJobEventForName({ job_dir: "D:/jobs/other-job" }, "example-job"), false);
  assert.equal(isJobEventForName(null, "example-job"), false);
});

test("stale runtime state ignores legacy SSE snapshots without runtime metadata", () => {
  assert.equal(shouldApplyLiveJobEvent(true), false);
  assert.equal(shouldApplyLiveJobEvent(false), true);
});
