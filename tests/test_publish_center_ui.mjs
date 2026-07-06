import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

globalThis.localStorage = {
  getItem() { return "zh"; },
  setItem() {},
};

Object.defineProperty(globalThis, "navigator", {
  configurable: true,
  value: { language: "zh-CN", platform: "test" },
});

globalThis.window = { addEventListener() {} };

const { API } = await import("../web/js/api.js");
const { renderPublishCenter } = await import("../web/js/publish-center.js");

test("publish center shows resumable progress, platform error, and manual fallback", () => {
  const html = renderPublishCenter({
    targets: [{ id: "bilibili", name: "Bilibili", manual_fallback: true, requires_platform_approval: true }],
    packages: [{ job_name: "job-one", source_name: "demo.mp4" }],
    attempts: [{
      id: "publish-one",
      job_name: "job-one",
      provider: "bilibili",
      status: "failed",
      uploaded_bytes: 5242880,
      total_bytes: 10485760,
      retryable: true,
      error: "rate limited",
      action: "open_manual_package",
    }],
  });

  assert.match(html, /Bilibili/);
  assert.match(html, /50%/);
  assert.match(html, /rate limited/);
  assert.match(html, /data-publish-action="retry"/);
  assert.match(html, /publish_package\.json/);
  assert.match(html, /id="publish-credential-form"/);
});

test("API client exposes publish target, credential, and attempt controls", () => {
  for (const method of [
    "getPublishTargets", "savePublishCredentials", "deletePublishCredentials",
    "getPublishAttempts", "createPublishAttempt", "startPublishAttempt",
    "retryPublishAttempt", "syncPublishAttempt", "getPublishPackages",
  ]) {
    assert.equal(typeof API[method], "function", method);
  }
});

test("main navigation promotes publish while health remains a secondary route", async () => {
  const source = await readFile(new URL("../web/js/app.js", import.meta.url), "utf8");
  assert.match(source, /\["#\/publish", "nav\.publish"/);
  assert.doesNotMatch(source, /\["#\/health", "nav\.health"/);
  assert.match(source, /addRoute\(\/\^\\\/health/);
});
