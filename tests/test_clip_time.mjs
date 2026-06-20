import assert from "node:assert/strict";
import test from "node:test";

const { formatClipTimeInput, parseClipTime } = await import("../web/js/clip-time.js");

test("formatClipTimeInput keeps short values readable in seconds", () => {
  assert.equal(formatClipTimeInput(0), "0秒");
  assert.equal(formatClipTimeInput(6.5), "6.5秒");
});

test("formatClipTimeInput formats minute values using Chinese time units", () => {
  assert.equal(formatClipTimeInput(65.2), "1分5.2秒");
  assert.equal(formatClipTimeInput(3605), "1时0分5秒");
});

test("parseClipTime accepts seconds, Chinese units, and clock notation", () => {
  assert.equal(parseClipTime("6.5秒"), 6.5);
  assert.equal(parseClipTime("1分5.2秒"), 65.2);
  assert.equal(parseClipTime("01:05.2"), 65.2);
  assert.equal(parseClipTime("1:00:05"), 3605);
});
