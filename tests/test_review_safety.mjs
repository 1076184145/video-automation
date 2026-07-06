import assert from "node:assert/strict";
import test from "node:test";

globalThis.localStorage = {
  getItem() {
    return "en";
  },
  setItem() {},
  removeItem() {},
};

Object.defineProperty(globalThis, "navigator", {
  configurable: true,
  value: { language: "en", platform: "test" },
});

globalThis.window = { addEventListener() {} };

const {
  clearReviewDraft,
  loadReviewDraft,
  saveReviewDraft,
} = await import("../web/js/review-drafts.js");
const { renderRevisionHistory } = await import("../web/js/revision-history.js");

function memoryStorage() {
  const values = new Map();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
}

test("review drafts survive reload and can be cleared after a successful save", () => {
  const storage = memoryStorage();
  saveReviewDraft("job-one", "transcript", [{ start: 0, end: 1, text: "draft" }], storage);

  const draft = loadReviewDraft("job-one", "transcript", storage);
  assert.equal(draft.data[0].text, "draft");
  assert.match(draft.updated_at, /^\d{4}-/);

  clearReviewDraft("job-one", "transcript", storage);
  assert.equal(loadReviewDraft("job-one", "transcript", storage), null);
});

test("revision history exposes explicit restore actions without rendering payload data", () => {
  const html = renderRevisionHistory([
    {
      id: "revision-one",
      revision: 2,
      kind: "transcript",
      summary: "Saved transcript edits",
      created_at: "2026-07-04T12:00:00",
    },
  ]);

  assert.match(html, /Revision 2/);
  assert.match(html, /Transcript/);
  assert.match(html, /data-restore-revision="revision-one"/);
  assert.doesNotMatch(html, /payload/);
});
