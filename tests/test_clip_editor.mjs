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

const { collectEditedClipsForTest, renderClips } = await import("../web/js/clip-editor.js");

test("renderClips creates editable rows and applies feedback state", () => {
  const html = renderClips(
    {
      clips: [
        {
          start: 0,
          end: 6.5,
          duration: 6.5,
          keep: true,
          reason: "before silence",
          transcript_text: "字幕 <x>",
          final_score: 45.9,
        },
      ],
    },
    { items: [{ clip_key: "0.000-6.500", action: "accepted" }] },
  );

  assert.match(html, /class="table clip-editor"/);
  assert.match(html, /data-clip-key="0\.000-6\.500"/);
  assert.match(html, /active accepted/);
  assert.match(html, /字幕 &lt;x&gt;/);
});

test("collectEditedClips returns backend-ready clips with subtitle overrides", () => {
  const row = {
    querySelector(selector) {
      const elements = {
        '[data-field="start"]': { value: "1.5" },
        '[data-field="end"]': { value: "4" },
        '[data-field="keep"]': { checked: false },
        '[data-field="reason"]': { value: "trimmed" },
        '[data-field="content"]': {
          value: " edited subtitle ",
          dataset: { original: "original subtitle", subtitleOverride: "0" },
        },
      };
      return elements[selector] || null;
    },
  };
  const root = {
    querySelectorAll() {
      return [row];
    },
  };

  assert.deepEqual(collectEditedClipsForTest(root), [
    {
      start: 1.5,
      end: 4,
      keep: false,
      reason: "trimmed",
      transcript_text: "edited subtitle",
      subtitle_text: "edited subtitle",
      subtitle_override: true,
    },
  ]);
});
