const test = require("node:test");
const assert = require("node:assert");
const { applyFilter, countRemaining } = require("../src/filters.js");

const tasks = [{ id: 1, done: false }, { id: 2, done: true }];

test("applyFilter", () => {
  assert.strictEqual(applyFilter(tasks, "active").length, 1);
  assert.strictEqual(applyFilter(tasks, "unknown").length, 2);
});

test("countRemaining", () => {
  assert.strictEqual(countRemaining(tasks), 1);
});
