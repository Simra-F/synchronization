# Architecture

Taskbox is a static page. Scripts are plain globals loaded in order by `index.html`:

1. `src/utils/format.js` - `escapeHtml`, `formatDate`, `pluralize`
2. `src/storage.js` - the storage API (`loadTasks`, `saveTasks`, `addTask`, `toggleTask`, `removeTask`), see storage-api.md
3. `src/filters.js` - `FILTERS`, `applyFilter`, `countRemaining`
4. `src/theme.js` - `THEMES`, `applyTheme`, `nextTheme`
5. `src/components/taskList.js` - `renderTasks`, `renderItem`, `mountFilters`
6. `src/components/toolbar.js` - `BUTTONS` registry, `mountToolbar`, `handleAdd`, `handleClear`
7. `src/app.js` - `init` wires everything on `DOMContentLoaded`

To add a toolbar button: write a handler in `src/components/toolbar.js` and register it in `BUTTONS`.
