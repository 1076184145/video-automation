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

const { collectEditedTranscriptForTest, renderTranscript } = await import(
  "../web/js/transcript-editor.js"
);

test("renderTranscript creates editable transcript rows with escaped text", () => {
  const html = renderTranscript({
    segments: [{ start: 1.25, end: 3.5, text: "<hello> & world" }],
  });

  assert.match(html, /class="transcript-editor"/);
  assert.match(html, /data-start="1.25"/);
  assert.match(html, /data-end="3.5"/);
  assert.match(html, /&lt;hello&gt; &amp; world/);
});

test("collectEditedTranscript returns trimmed text and numeric timings", () => {
  const root = {
    querySelectorAll() {
      return [
        { dataset: { start: "0", end: "1.5" }, value: " hello " },
        { dataset: { start: "2", end: "4" }, value: "world" },
      ];
    },
  };

  assert.deepEqual(collectEditedTranscriptForTest(root), [
    { start: 0, end: 1.5, text: "hello" },
    { start: 2, end: 4, text: "world" },
  ]);
});
