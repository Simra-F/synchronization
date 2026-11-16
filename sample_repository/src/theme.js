// Light/dark theme switching. The preference (not task data) is remembered in localStorage.
const THEME_KEY = "taskbox.theme";
const THEMES = ["light", "dark"];

function applyTheme(themeName) {
  document.documentElement.dataset.theme = themeName;
  localStorage.setItem(THEME_KEY, themeName);
}

function nextTheme() {
  const current = localStorage.getItem(THEME_KEY) || THEMES[0];
  applyTheme(THEMES[(THEMES.indexOf(current) + 1) % THEMES.length]);
}
