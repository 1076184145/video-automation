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

const { collectEditedTranscriptForTest, renderTranscript, showTranscriptSaveState } = await import(
  "../web/js/transcript-editor.js"
);
const { t } = await import("../web/js/i18n.js");

test("showTranscriptSaveState reports a held, failed or applied deferred save in the message box", () => {
  const box = { innerHTML: "", get textContent() { return this.innerHTML.replace(/<[^>]+>/g, ""); } };
  globalThis.document = { getElementById: (id) => (id === "transcript-editor-message" ? box : null) };
  try {
    showTranscriptSaveState(null);
    assert.equal(box.innerHTML, "");
    showTranscriptSaveState({ state: "pending", error: null });
    assert.equal(box.textContent, t("job.save_transcript_pending"));
    showTranscriptSaveState({ state: "applied", error: null }); // replaces only the pending notice
    assert.equal(box.textContent, t("job.save_transcript_preview"));
    showTranscriptSaveState({ state: "applied", error: null }); // leaves any other message alone
    assert.equal(box.textContent, t("job.save_transcript_preview"));
    showTranscriptSaveState({ state: "failed", error: "<b>forced</b> failure" });
    assert.match(box.innerHTML, /^<div class="error">/);
    assert.match(box.innerHTML, /&lt;b&gt;forced&lt;\/b&gt; failure/);
    assert.ok(box.innerHTML.includes(t("job.save_transcript_failed")));
  } finally {
    delete globalThis.document;
  }
});

test("renderTranscript creates editable transcript rows with escaped text", () => {
  const html = renderTranscript({
    backend: "funasr",
    model: "paraformer-zh",
    device: "cuda:0",
    segments: [{ start: 1.25, end: 3.5, text: "<hello> & world" }],
  });

  assert.match(html, /class="transcript-editor"/);
  assert.match(html, /data-start="1.25"/);
  assert.match(html, /data-end="3.5"/);
  assert.match(html, /&lt;hello&gt; &amp; world/);
  assert.match(html, /FunASR/);
  assert.match(html, /paraformer-zh/);
  assert.match(html, /cuda:0/);
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
