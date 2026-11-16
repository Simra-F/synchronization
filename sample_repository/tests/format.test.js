const test = require("node:test");
const assert = require("node:assert");
const { escapeHtml, pluralize } = require("../src/utils/format.js");

test("escapeHtml", () => {
  assert.strictEqual(escapeHtml("<b>&</b>"), "&lt;b&gt;&amp;&lt;/b&gt;");
});

test("pluralize", () => {
  assert.strictEqual(pluralize(1, "task"), "1 task");
  assert.strictEqual(pluralize(3, "task"), "3 tasks");
});
