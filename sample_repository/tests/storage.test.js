const test = require("node:test");
const assert = require("node:assert");

globalThis.localStorage = {
  data: {},
  getItem(key) { return key in this.data ? this.data[key] : null; },
  setItem(key, value) { this.data[key] = value; },
};

const { loadTasks, addTask, toggleTask, removeTask } = require("../src/storage.js");

test("addTask stores a task", () => {
  addTask("write report");
  assert.deepStrictEqual(loadTasks().map((t) => t.title), ["write report"]);
});

test("toggleTask and removeTask", () => {
  const [task] = loadTasks();
  toggleTask(task.id);
  assert.strictEqual(loadTasks()[0].done, true);
  removeTask(task.id);
  assert.deepStrictEqual(loadTasks(), []);
});
