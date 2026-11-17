# Contributing to Taskbox

Thanks for helping! Small, focused pull requests are easiest to review.

## Rules
- No external libraries, frameworks or CDNs: plain HTML, CSS and JavaScript only.
- All task persistence goes through the functions in src/storage.js (loadTasks, saveTasks, addTask, toggleTask, removeTask). Never read or write task data in localStorage anywhere else.
- Every toolbar button must be registered in BUTTONS in src/components/toolbar.js.
- New scripts must be added to index.html in dependency order.
- Tests use node's built-in test runner (`node --test`) and live in tests/. Each module ends with `if (typeof module !== "undefined") { module.exports = { ... }; }` and tests load it with `require("../src/...")`; keep DOM code out of the functions you test.
- Add new tests in a new test file; never delete or rewrite existing functions or tests.

## Workflow
1. Open or pick an issue (look for `good first issue`).
2. Branch from `main`, keep the change focused, update docs/ if you change an API.
3. Run `node --test` and open a pull request using the template.
4. A maintainer reviews; CI must be green before merge.
