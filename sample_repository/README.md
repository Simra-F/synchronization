# Taskbox

A tiny task list that lives in your browser: plain HTML, CSS and JavaScript, data in `localStorage`, no build step.

**Live site:** served by GitHub Pages from `main`. The footer shows the latest change.

## Features
- Add, complete and clear tasks
- Filters: all / active / done
- Light and dark themes

## Project layout
| Path | What it is |
|---|---|
| `index.html` | the page |
| `src/storage.js` | storage API (all task persistence goes here) - see `docs/storage-api.md` |
| `src/components/` | task list and toolbar |
| `src/filters.js`, `src/theme.js`, `src/utils/` | filters, themes, helpers |
| `styles/` | CSS |
| `tests/` | `node --test` |
| `docs/` | architecture, storage API, roadmap |

## Development
```bash
npx serve .        # or any static server, or just open index.html
node --test        # run the tests
```

## Contributing
See [CONTRIBUTING.md](CONTRIBUTING.md) and the [roadmap](docs/roadmap.md). Please follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## License
MIT

> This repository is also the sandbox for a research project on context drift in multi-agent coding systems:
> while a multi-agent system implements a feature, simulated contributors keep pushing commits, issues and PRs.
