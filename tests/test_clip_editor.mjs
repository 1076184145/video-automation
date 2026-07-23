import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
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

const { bindClipHorizontalScroll, collectEditedClipsForTest, renderClips } = await import("../web/js/clip-editor.js");

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
  assert.match(html, /data-clip-horizontal-scroll/);
  assert.match(html, /data-clip-editor-scroll/);
  assert.match(html, /data-clip-key="0\.000-6\.500"/);
  assert.match(html, /active accepted/);
  assert.match(html, /字幕 &lt;x&gt;/);
});

test("narrow clip editor wraps controls and avoids a sticky action overlay", () => {
  const css = readFileSync(new URL("../web/css/style.css", import.meta.url), "utf8");
  assert.match(css, /\.clip-toolbar\s*\{[^}]*flex-wrap:\s*wrap/s);
  assert.match(css, /container:\s*clip-editor\s*\/\s*inline-size/);
  assert.match(css, /@container\s+clip-editor\s*\(min-width:\s*760px\)/);
  assert.match(css, /\.clip-editor-horizontal-scroll\s*\{[^}]*overflow-x:\s*auto/s);
});

test("top horizontal scrollbar mirrors the clip table and removes listeners", () => {
  const listeners = new Map();
  const element = (selector) => ({
    scrollLeft: 0,
    matches(candidate) { return candidate === selector; },
  });
  const rail = element("[data-clip-horizontal-scroll]");
  const editor = element("[data-clip-editor-scroll]");
  const root = {
    addEventListener(type, listener) { listeners.set(type, listener); },
    removeEventListener(type, listener) {
      if (listeners.get(type) === listener) listeners.delete(type);
    },
    querySelector(selector) {
      return selector === "[data-clip-horizontal-scroll]" ? rail : editor;
    },
  };

  const dispose = bindClipHorizontalScroll(root);
  rail.scrollLeft = 320;
  listeners.get("scroll")({ target: rail });
  assert.equal(editor.scrollLeft, 320);
  editor.scrollLeft = 140;
  listeners.get("scroll")({ target: editor });
  assert.equal(rail.scrollLeft, 140);

  dispose();
  assert.equal(listeners.size, 0);
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
