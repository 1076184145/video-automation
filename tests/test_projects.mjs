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

globalThis.window = { addEventListener() {} };

const { renderProjectsView } = await import("../web/js/projects.js");
const { renderNewJobFormForTest } = await import("../web/js/new-job.js");
const { API } = await import("../web/js/api.js");

const kits = [
  { id: "kit-one", name: "B站横屏", platform: "bilibili", aspect: "16:9" },
];
const projects = [
  {
    id: "project-one",
    name: "每周直播精选",
    description: "直播切片系列",
    tags: ["直播", "周更"],
    default_kit_id: "kit-one",
  },
];

test("projects page renders project and creator-kit workflows", () => {
  const html = renderProjectsView({ projects, kits });

  assert.match(html, /项目库/);
  assert.match(html, /每周直播精选/);
  assert.match(html, /直播切片系列/);
  assert.match(html, /创作者套件/);
  assert.match(html, /B站横屏/);
  assert.match(html, /id="create-project-form"/);
  assert.match(html, /id="create-kit-form"/);
});

test("new job can bind a project and immutable creator-kit snapshot", () => {
  const html = renderNewJobFormForTest({}, { projects, kits });

  assert.match(html, /id="project-id"/);
  assert.match(html, /value="project-one"/);
  assert.match(html, /id="creator-kit-id"/);
  assert.match(html, /value="kit-one"/);
});

test("API client exposes versioned project and creator-kit methods", () => {
  for (const method of [
    "getCapabilities",
    "getProjects",
    "createProject",
    "getCreatorKits",
    "createCreatorKit",
  ]) {
    assert.equal(typeof API[method], "function", method);
  }
});
