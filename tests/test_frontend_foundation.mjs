import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
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

globalThis.window = {
  addEventListener() {},
  scrollTo() {},
};

globalThis.document = {
  body: { scrollTop: 0 },
  documentElement: { lang: "zh-CN", scrollTop: 0 },
};

const themeModule = new URL("../web/js/theme.js", import.meta.url);
const eventHubModule = new URL("../web/js/event-hub.js", import.meta.url);

test("theme preference follows the system until the user chooses an override", async () => {
  const { resolveTheme } = await import(themeModule);

  assert.equal(resolveTheme("system", false), "light");
  assert.equal(resolveTheme("system", true), "dark");
  assert.equal(resolveTheme("light", true), "light");
  assert.equal(resolveTheme("dark", false), "dark");
  assert.equal(resolveTheme("unknown", true), "dark");
});

test("theme preference cycles through system, light, and dark", async () => {
  const { nextThemePreference } = await import(themeModule);

  assert.equal(nextThemePreference("system"), "light");
  assert.equal(nextThemePreference("light"), "dark");
  assert.equal(nextThemePreference("dark"), "system");
});

test("event hub shares one event source and releases it after the last subscriber", async () => {
  const { createEventHub } = await import(eventHubModule);
  let opens = 0;
  let closed = 0;
  const listeners = new Map();
  const source = {
    addEventListener(type, listener) {
      listeners.set(type, listener);
    },
    removeEventListener(type) {
      listeners.delete(type);
    },
    close() {
      closed += 1;
    },
  };
  const hub = createEventHub(() => {
    opens += 1;
    return source;
  });
  const received = [];

  const unsubscribeA = hub.subscribe("job", (payload) => received.push(["a", payload.id]));
  const unsubscribeB = hub.subscribe("job", (payload) => received.push(["b", payload.id]));
  listeners.get("job")({ data: JSON.stringify({ id: "job-1" }) });

  assert.equal(opens, 1);
  assert.deepEqual(received, [["a", "job-1"], ["b", "job-1"]]);

  unsubscribeA();
  assert.equal(closed, 0);
  unsubscribeB();
  assert.equal(closed, 1);
});

test("event hub isolates a failing subscriber from later subscribers", async () => {
  const { createEventHub } = await import(eventHubModule);
  const listeners = new Map();
  const source = {
    addEventListener(type, listener) {
      listeners.set(type, listener);
    },
    removeEventListener() {},
    close() {},
  };
  const hub = createEventHub(() => source);
  const received = [];
  const originalError = console.error;
  console.error = () => {};
  try {
    hub.subscribe("job", () => {
      throw new Error("subscriber failed");
    });
    hub.subscribe("job", (payload) => received.push(payload.id));
    listeners.get("job")({ data: JSON.stringify({ id: "job-after-error" }) });
  } finally {
    console.error = originalError;
    hub.close();
  }
  assert.deepEqual(received, ["job-after-error"]);
});

test("structured API errors expose their readable message", async () => {
  const { apiErrorMessage } = await import("../web/js/api.js");

  assert.equal(
    apiErrorMessage({ error: { code: "validation_error", message: "Project not found" } }, "400 Bad Request"),
    "Project not found",
  );
  assert.equal(apiErrorMessage({ error: "legacy error" }, "fallback"), "legacy error");
});

test("route-owned API cancellation stays an AbortError and is never retried", async () => {
  const { requestJson } = await import("../web/js/api.js");
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = (_url, { signal }) => new Promise((_resolve, reject) => {
    calls += 1;
    signal.addEventListener("abort", () => reject(signal.reason || new DOMException("Aborted", "AbortError")), { once: true });
  });
  const controller = new AbortController();
  try {
    const request = requestJson("/slow-route", { signal: controller.signal, timeout: 0, retries: 3 });
    controller.abort();
    await assert.rejects(request, (error) => error?.name === "AbortError");
    assert.equal(calls, 1);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("route cancellation aborts an in-flight recording XHR", async () => {
  const { API } = await import("../web/js/api.js");
  const OriginalXHR = globalThis.XMLHttpRequest;
  let instance = null;
  class FakeXHR {
    constructor() {
      this.upload = {};
      instance = this;
    }
    open() {}
    setRequestHeader() {}
    send() {
      this.sent = true;
    }
    abort() {
      this.aborted = true;
      this.onabort?.();
    }
  }
  globalThis.XMLHttpRequest = FakeXHR;
  const controller = new AbortController();
  try {
    const upload = API.uploadRecording({ name: "clip.mp4", type: "video/mp4" }, null, { signal: controller.signal });
    controller.abort();
    await assert.rejects(upload, (error) => error?.name === "AbortError");
    assert.equal(instance.sent, true);
    assert.equal(instance.aborted, true);
  } finally {
    globalThis.XMLHttpRequest = OriginalXHR;
  }
});

test("shared error states escape server text unless trusted markup is explicit", async () => {
  const { errorState } = await import("../web/js/ui-states.js");

  assert.match(errorState('<img src=x onerror="boom">'), /&lt;img/);
  assert.doesNotMatch(errorState('<img src=x onerror="boom">'), /<img/);
  assert.match(errorState("<strong>local</strong>", { trustedHtml: true }), /<strong>local<\/strong>/);
});

test("lazy routes load a page module once and forward route arguments", async () => {
  const { lazyView } = await import("../web/js/router.js");
  let imports = 0;
  const calls = [];
  const render = lazyView(async () => {
    imports += 1;
    return {
      renderPage(...args) {
        calls.push(args);
        return "cleanup";
      },
    };
  }, "renderPage");

  assert.equal(await render(["first"]), "cleanup");
  assert.equal(await render(["second"]), "cleanup");
  assert.equal(imports, 1);
  assert.deepEqual(calls, [[["first"]], [["second"]]]);
});

test("app routes use dynamic imports instead of eagerly loading every page", async () => {
  const source = await readFile(new URL("../web/js/app.js", import.meta.url), "utf8");

  assert.doesNotMatch(source, /import \{ renderDashboard \} from/);
  assert.doesNotMatch(source, /import \{ renderJobDetail \} from/);
  assert.match(source, /lazyView\(\(\) => import\("\.\/dashboard\.js"\)/);
  assert.match(source, /lazyView\(\(\) => import\("\.\/job-detail\.js/);
});

test("design system exposes true light and dark themes without transition-all", async () => {
  const source = await readFile(new URL("../web/css/style.css", import.meta.url), "utf8");

  assert.match(source, /--canvas:\s*#f5f5f7/i);
  assert.match(source, /--surface:\s*#ffffff/i);
  assert.match(source, /:root\[data-theme="dark"\]/);
  assert.match(source, /--canvas:\s*#000000/i);
  assert.match(source, /--sidebar-width:\s*216px/);
  assert.doesNotMatch(source, /transition:\s*all\b/);
});

test("glass effects stay on navigation and overlays instead of ordinary panels", async () => {
  const source = await readFile(new URL("../web/css/style.css", import.meta.url), "utf8");

  assert.match(source, /\.sidebar[\s\S]*backdrop-filter:\s*blur\(24px\)\s+saturate\(140%\)/);
  assert.doesNotMatch(source, /\.card,\s*\.panel[^{}]*backdrop-filter/);
});

test("mobile shortcut help stays above the bottom navigation and handles Escape first", async () => {
  const [css, shortcut] = await Promise.all([
    readFile(new URL("../web/css/style.css", import.meta.url), "utf8"),
    readFile(new URL("../web/js/shortcut-help.js", import.meta.url), "utf8"),
  ]);

  assert.match(css, /@media\s*\(max-width:\s*860px\)[\s\S]*?\.shortcut-help-button\s*\{[^}]*bottom:\s*calc\(84px/);
  assert.match(css, /@media\s*\(max-width:\s*480px\)[\s\S]*?\.shortcut-row\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/);
  assert.ok(shortcut.indexOf('event.key === "Escape"') < shortcut.indexOf("isTypingTarget(event.target)"));
});
