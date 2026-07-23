import test from "node:test";
import assert from "node:assert/strict";

import { en } from "../web/js/i18n-en.js";
import { zh } from "../web/js/i18n-zh.js";


test("English and Chinese dictionaries expose the same translation keys", () => {
  assert.deepEqual(Object.keys(en).sort(), Object.keys(zh).sort());
});
