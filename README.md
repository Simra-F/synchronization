# ctxdrift: dynamic context synchronization for software-engineering multi-agent systems

Two open-weight LLM agents (no proprietary APIs) work on the same GitHub repository at the same time:

```
Agent 2: github_modifier (single Qwen agent, many contributor personas)
         commits / issues / PRs / new helper modules on main, in parallel, at random times
                                │                       (GitHub Pages redeploys: the site shows every change)
                         Repository (GitHub, or in-memory for fast runs)
                                │ events ("webhook") + periodic reconciliation
                       Source of truth V  (versioned facts: symbols, rules, issues, PRs)
                                │ snapshot + net patches when count > s OR a change waited t_max calls
Agent 1: ctxdrift MAS (Qwen)  planner -> researcher -> (coder -> reviewer) x2 -> lead
                                │
                         PR for Feature A, auto-merged (three-way) into main, then `node --test`
```

Feature A: an Export button that downloads all tasks (see `ctxdrift/task.py`).

## Folders
```
sample_repository/   Taskbox, an open-source-style web app to upload as your sandbox repo (Pages, CI, docs, templates)
github_modifier/     Agent 2: modifier agent, scenarios (modification levels), real GitHub backend
ctxdrift/            Agent 1 + the study: MAS pipeline, source of truth V, info-loss tracker, simulated agent, plots
s_values_testing/    sweep and decision rule for V's staleness bounds (s, t_max), plus simulated predictions
colab/               notebook that runs everything on a Colab GPU with Ollama
tests/               offline tests (mock and simulated agents)
```

## Quick start
- **No GPU, seconds:** `python3 -m s_values_testing.sweep --backend sim --runs 20` (rough prediction)
- **Colab:** open `colab/ctxdrift_colab.ipynb`, upload this folder as a zip, run the cells.
- **Tests:** `python3 -m pytest -q` (or `python3 tests/test_smoke.py`)

## Models
Ollama (default, `qwen3:1.7b`), any OpenAI-compatible server (`--backend openai --base-url ...`, e.g. vLLM), `sim`
(behavioural model, `--sim-preset small|medium|large`) or `mock`. Pick models with `--mas-model` / `--modifier-model`.

## Real GitHub sandbox: Simra-F/sample_repository
Site: https://simra-f.github.io/sample_repository/ (GitHub Pages from `main`). `--github` with no value and the
modifier's `--repo` default to `$GITHUB_REPO`, else `Simra-F/sample_repository`.
```bash
python3 -m github_modifier --level heavy --minutes 5 --reset            # watch the repo and site change
python3 -m ctxdrift.run_experiment --github --runs 1 --scenarios moderate
```
Zip for Colab (from `~/Downloads`): `zip -r ctxdrift-baseline.zip ctxdrift-baseline -x "*/.git/*" "*/runs/*" "*/results/*" "*__pycache__*"`
Each run resets `main` to the seed files (normal commit), closes what the tools opened, and auto-merges the MAS's PR.
Token: `$GITHUB_TOKEN` or `gh auth token`. Use a sandbox repo.

## Experiment
```bash
python3 -m ctxdrift.run_experiment --runs 3 --s 0 --t-max 1          # none, full, v, lean
```
Options: `--scenarios static,light,moderate,heavy,churn`, `--modes none,full,v,lean` (also `drift`, `hybrid`), `--drift-threshold`, `--s` (count bound, `inf` = off),
`--t-max` (agent calls, `-1` = off), `--lockstep` (reproducible), `--no-lookup`, `--no-merge`.
Prints retained information, staleness, refreshes, tokens, merge rate and test pass rate per scenario and mode, and
writes `runs/<timestamp>/` with `results.json` and the graphs: `info_loss_vs_hop.png`, `staleness_vs_hop.png`,
`stale_rate_vs_hop.png`, `per_agent_loss.png`, `loss_vs_changes.png`, `cost.png`.

## Scenarios (`github_modifier/scenarios.py`)
`static` (control), `light` (mostly cosmetic), `moderate` (mixed, contributors add helpers), `heavy` (frequent breaking
renames and requirement issues), `churn` (reverts, withdrawn issues, rejected PRs). Every change comes from a randomly
chosen contributor persona.

## Modes (`ctxdrift/pipeline.py`)
The MAS is a chain of subagents: each receives the previous subagent's output (ending in a `FACTS:` list) plus a view
of the repository, and V corrects stale facts at every handoff.
- `none`, the regular system: every subagent sees the raw snapshot from task start.
- `full`: re-read the whole repository before every subagent.
- `v`: snapshot + net fact changes from V, delivered when more than `s` relevant changes are unseen or one has waited
  `t_max` agent calls; the task is restated with each update; `lookup` tool; reconciliation every 3 subagents.
- `drift`: no updates up front; each output is checked against V by code and re-run only if it diverges.
- `hybrid`: `v` + the output check.
- **`lean`, the proposed system:** `hybrid`, plus (#1) role briefs written by V from code (planner/researcher get the
  fact list only; coder/reviewer/lead get the fact list + focus files) and (#4) code-fixed corrections (stale or
  PR-only names replaced by code; other divergences sent to the next subagent; only the final lead is re-run).
Relevance, in priority order: task issues and rules, symbols in focus files (task files + files the agents edit),
symbols the agents named, new task-related modules. Default comparison: `--modes none,full,v,lean`.

## Information loss (`ctxdrift/tracker.py`)
Graded against ground truth **when the agent started**, from the modifier's own log (never from V): required facts
(task constraints, open requirement issues, rules from added helpers, with current symbol names) and the share of
stale symbol mentions. `info_loss = 1 - retention * (1 - stale_rate)`; "retained" = 1 - info_loss.
Checks are regexes, so compare modes rather than trusting absolute values.
