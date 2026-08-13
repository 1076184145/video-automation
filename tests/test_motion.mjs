import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

globalThis.window = {
  matchMedia(query) {
    return { matches: query === "(prefers-reduced-motion: reduce)" };
  },
};

const {
  MOTION_TIMINGS,
  motionDelayForIndex,
  motionKeyFor,
  prefersReducedMotion,
  removeWithMotion,
} = await import("../web/js/motion.js");

test("motion staggering stays brief and capped", () => {
  assert.equal(motionDelayForIndex(0), 0);
  assert.equal(motionDelayForIndex(3), MOTION_TIMINGS.stagger * 3);
  assert.equal(motionDelayForIndex(100), MOTION_TIMINGS.stagger * 6);
  assert.equal(motionDelayForIndex(-4), 0);
});

test("dynamic collection items receive stable motion identities", () => {
  assert.equal(motionKeyFor({ dataset: { motionKey: "job:demo" } }), "job:demo");
  assert.equal(motionKeyFor({ dataset: { queueId: "42" } }), "queue:42");
  assert.equal(motionKeyFor({ dataset: { clipKey: "intro" } }), "clip:intro");
  assert.equal(motionKeyFor({ dataset: { publishId: "attempt-7" } }), "publish:attempt-7");
  assert.equal(motionKeyFor({ dataset: { path: "D:/video.mp4" } }), "recording:D:/video.mp4");
});

test("reduced-motion preference removes stale UI immediately", async () => {
  const element = {
    isConnected: true,
    removed: false,
    remove() {
      this.removed = true;
    },
  };

  assert.equal(prefersReducedMotion(), true);
  await removeWithMotion(element);
  assert.equal(element.removed, true);
});

test("motion CSS includes overlay exits and an accessibility fallback", () => {
  const css = readFileSync(new URL("../web/css/style.css", import.meta.url), "utf8");
  assert.match(css, /\.modal-backdrop\.ui-exit\s*\{/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);
  assert.match(css, /@keyframes ui-item-in/);
});

test("application flows do not fall back to native confirm or prompt dialogs", () => {
  const files = [
    "clip-editor.js",
    "cover-panel.js",
    "dashboard.js",
    "job-actions.js",
    "job-detail.js",
    "new-job.js",
    "projects.js",
    "settings.js",
  ];
  const source = files
    .map((name) => readFileSync(new URL(`../web/js/${name}`, import.meta.url), "utf8"))
    .join("\n");

  assert.doesNotMatch(source, /window\.(?:confirm|prompt)\s*\(/);
});
