import test from "node:test";
import assert from "node:assert/strict";
import { projectKeptTranscript } from "../web/js/kept-transcript.js";
const segments = [{ start: 0, end: 2, text: "first" }, { start: 2, end: 4, text: "second" }];
test("excluded sentences disappear without changing source; restored cuts restore text", () => {
  const original = structuredClone(segments);
  assert.deepEqual(projectKeptTranscript(segments, [{ start: 0, end: 2, keep: false }, { start: 2, end: 4, keep: true }]).map((s) => s.text), ["second"]);
  assert.deepEqual(projectKeptTranscript(segments, [{ start: 0, end: 4, keep: true }]).map((s) => s.text), ["first", "second"]);
  assert.deepEqual(segments, original);
  assert.deepEqual(projectKeptTranscript(segments, []), []);
});
test("partial sentences require explicit uncertainty without word timings", () => {
  const result = projectKeptTranscript(segments, [{ start: 1, end: 2 }]);
  assert.deepEqual(result, [{ start: 1, end: 2, text: "first", partial: true }]);
});
test("timed words follow kept intervals and clip overrides win", () => {
  const input = [{ start: 0, end: 2, text: "hello world", words: [{ start: 0, end: 1, word: "hello" }, { start: 1, end: 2, word: " world" }] }];
  assert.deepEqual(projectKeptTranscript(input, [{ start: 1, end: 2 }]), [{ start: 1, end: 2, text: "world", partial: false }]);
  assert.equal(projectKeptTranscript(input, [{ start: 1, end: 2, subtitle_override: true, subtitle_text: "manual" }])[0].text, "manual");
});
