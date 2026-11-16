// All task persistence goes through these functions (see CONTRIBUTING.md and docs/storage-api.md).
const STORAGE_KEY = "taskbox.tasks";

function loadTasks() {
  const raw = localStorage.getItem(STORAGE_KEY);
  return raw ? JSON.parse(raw) : [];
}

function saveTasks(tasks) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(tasks));
}

function addTask(title) {
  const tasks = loadTasks();
  const id = tasks.reduce((max, t) => Math.max(max, t.id), 0) + 1;
  tasks.push({ id: id, title: title, done: false, createdAt: Date.now() });
  saveTasks(tasks);
  return tasks;
}

function toggleTask(id) {
  const tasks = loadTasks().map((t) => (t.id === id ? { ...t, done: !t.done } : t));
  saveTasks(tasks);
  return tasks;
}

function removeTask(id) {
  const tasks = loadTasks().filter((t) => t.id !== id);
  saveTasks(tasks);
  return tasks;
}

if (typeof module !== "undefined") {
  module.exports = { STORAGE_KEY, loadTasks, saveTasks, addTask, toggleTask, removeTask };
}
