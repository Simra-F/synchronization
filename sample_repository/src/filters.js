// Task list filters (all / active / done).
const FILTERS = {
  all: () => true,
  active: (task) => !task.done,
  done: (task) => task.done,
};

function applyFilter(tasks, filterName) {
  return tasks.filter(FILTERS[filterName] || FILTERS.all);
}

function countRemaining(tasks) {
  return tasks.filter((task) => !task.done).length;
}

if (typeof module !== "undefined") {
  module.exports = { FILTERS, applyFilter, countRemaining };
}
