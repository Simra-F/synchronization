// Entry point: wires the components together once the page has loaded.
function init() {
  applyTheme(localStorage.getItem(THEME_KEY) || THEMES[0]);
  mountToolbar();
  mountFilters();
  renderTasks();
}

document.addEventListener("DOMContentLoaded", init);
