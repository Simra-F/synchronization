// Renders the task list, the filter buttons and the "N tasks left" counter.
let currentFilter = "all";

function renderItem(task) {
  const item = document.createElement("li");
  item.innerHTML = `${escapeHtml(task.title)} <small>${formatDate(task.createdAt)}</small>`;
  item.className = task.done ? "done" : "";
  item.onclick = () => {
    toggleTask(task.id);
    renderTasks();
  };
  return item;
}

function renderTasks() {
  const list = document.getElementById("task-list");
  list.innerHTML = "";
  const tasks = loadTasks();
  for (const task of applyFilter(tasks, currentFilter)) {
    list.appendChild(renderItem(task));
  }
  document.getElementById("remaining").textContent = `${pluralize(countRemaining(tasks), "task")} left`;
}

function mountFilters() {
  const bar = document.getElementById("filters");
  for (const filterName of Object.keys(FILTERS)) {
    const button = document.createElement("button");
    button.textContent = filterName;
    button.onclick = () => {
      currentFilter = filterName;
      renderTasks();
    };
    bar.appendChild(button);
  }
}
