import test from "node:test";
import assert from "node:assert/strict";
import { excludeDamagedRange } from "../web/js/damaged-range.js";
import { formatClipTimeInput, parseClipTime } from "../web/js/clip-time.js";

const clip = { start: 0, end: 10, keep: true, reason: "original", subtitle_override: true, subtitle_text: "manual" };
const ranges = (result) => result.clips.map(({ start, end, keep }) => [start, end, keep]);
test("splits the middle, preserves source object and resets split subtitle overrides", () => {
  const result = excludeDamagedRange([clip], 3, 7);
  assert.deepEqual(ranges(result), [[0, 3, true], [3, 7, false], [7, 10, true]]);
  assert.equal(result.affected, 1);
  assert.ok(result.clips.every((c) => !c.subtitle_override));
  assert.equal(clip.subtitle_text, "manual");
  assert.equal(clip.end, 10);
});
test("handles boundaries, full coverage and no overlap", () => {
  assert.deepEqual(ranges(excludeDamagedRange([clip], 0, 3)), [[0, 3, false], [3, 10, true]]);
  assert.deepEqual(ranges(excludeDamagedRange([clip], 7, 20)), [[0, 7, true], [7, 10, false]]);
  assert.deepEqual(ranges(excludeDamagedRange([clip], 0, 20)), [[0, 10, false]]);
  assert.equal(excludeDamagedRange([clip], 10, 20).affected, 0);
});
test("handles multiple clips and gaps without re-enabling dropped clips; repeated exclusion is idempotent", () => {
  const input = [clip, { start: 10, end: 12, keep: false }, { start: 15, end: 20, keep: true }];
  const result = excludeDamagedRange(input, 5, 17);
  assert.equal(result.affected, 2);
  assert.deepEqual(ranges(result), [[0, 5, true], [5, 10, false], [10, 12, false], [15, 17, false], [17, 20, true]]);
  assert.equal(excludeDamagedRange(result.clips, 5, 17).affected, 0);
});
test("rejects invalid input", () => {
  for (const [a, b] of [[-1, 2], [3, 2], [2, 2], [NaN, 4], [0, Infinity]]) {
    assert.throws(() => excludeDamagedRange([clip], a, b), RangeError);
  }
});
test("source-time precision survives rendering and parsing", () => {
  for (const value of [0.015, 3.257, 60.015, 3600.125, 10.01]) {
    assert.equal(parseClipTime(formatClipTimeInput(value, 3)), value);
  }
});
