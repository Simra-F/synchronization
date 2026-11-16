// Toolbar. Every toolbar button must be registered in BUTTONS (see CONTRIBUTING.md).
function handleAdd() {
  const input = document.getElementById("new-task");
  const title = input.value.trim();
  if (title) {
    addTask(title);
    input.value = "";
    renderTasks();
  }
}

function handleClear() {
  saveTasks([]);
  renderTasks();
}

const BUTTONS = { "Add": handleAdd, "Clear all": handleClear, "Theme": nextTheme };

function mountToolbar() {
  const bar = document.getElementById("toolbar");
  for (const [label, handler] of Object.entries(BUTTONS)) {
    const button = document.createElement("button");
    button.textContent = label;
    button.onclick = handler;
    bar.appendChild(button);
  }
}
