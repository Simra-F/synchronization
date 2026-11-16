# Storage API (`src/storage.js`)

Tasks are stored as JSON under `STORAGE_KEY` in localStorage. Each task is
`{ id, title, done, createdAt }`.

| Function | Returns | Notes |
|---|---|---|
| `loadTasks()` | array of tasks | the only way to read tasks |
| `saveTasks(tasks)` | - | replaces all tasks |
| `addTask(title)` | updated array | assigns the next id |
| `toggleTask(id)` | updated array | flips `done` |
| `removeTask(id)` | updated array | |
