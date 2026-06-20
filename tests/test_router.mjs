import assert from "node:assert/strict";
import test from "node:test";

globalThis.localStorage = {
  getItem() {
    return "zh";
  },
  setItem() {},
};

Object.defineProperty(globalThis, "navigator", {
  configurable: true,
  value: { language: "zh-CN", platform: "test" },
});

const scrollCalls = [];
globalThis.window = {
  addEventListener() {},
  scrollTo(options) {
    scrollCalls.push(options);
  },
};

globalThis.document = {
  body: { scrollTop: 2400 },
  documentElement: { lang: "zh-CN", scrollTop: 2400 },
};

const { resetRouteScroll } = await import("../web/js/router.js");

test("resetRouteScroll returns a newly rendered route to the top", () => {
  resetRouteScroll();

  assert.equal(document.body.scrollTop, 0);
  assert.equal(document.documentElement.scrollTop, 0);
  assert.deepEqual(scrollCalls.at(-1), { top: 0, left: 0, behavior: "auto" });
});
