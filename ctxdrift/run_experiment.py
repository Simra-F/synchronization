"""Run the MAS (Agent 1) on Feature A while the modifier (Agent 2) changes the same repository in parallel,
for every scenario x mode x run. At the end the MAS's pull request is auto-merged into main (three-way merge
against everything contributors pushed meanwhile) and the repository's tests are run on the result.

  python -m ctxdrift.run_experiment --runs 3                                  # in-memory repo, Ollama qwen3:1.7b
  python -m ctxdrift.run_experiment --github OWNER/NAME                       # real GitHub (site updates live)
  python -m ctxdrift.run_experiment --backend sim --runs 30 --lockstep        # rough prediction, no GPU
  python -m ctxdrift.run_experiment --backend mock                            # offline smoke run
"""
from __future__ import annotations
import argparse
import json
import os
import shutil
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

from github_modifier.modifier import HopClock, Modifier, Truth
from github_modifier.scenarios import LEVELS

from . import plots
from .llm import LLM
from .pipeline import MODES, parse_files, run_chain
from .repo import LocalRepo
from .symbols import extract, mentions
from .task import CONTRIBUTORS, FACTS, FOCUS_FILES, HELPERS, KEYWORDS, REQUIREMENTS, TASK, load_seed


def run_node_tests(files: dict):
    """True/False = `node --test` passed/failed on these files; None = node not installed."""
    if not shutil.which("node"):
        return None
    with tempfile.TemporaryDirectory() as d:
        for path, text in files.items():
            f = Path(d) / path
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text)
        try:
            return subprocess.run(["node", "--test"], cwd=d, capture_output=True, timeout=120).returncode == 0
        except subprocess.TimeoutExpired:
            return False


def parse_s(value: str) -> float:
    return float("inf") if value.lower() in ("inf", "none", "off") else float(value)


def run_one(scenario, mode, run, cfg, mas_llm, mod_llm, seed_files):
    """cfg keys: s, t_max, rounds, lockstep, lookup, github, merge."""
    if cfg.get("github"):
        from github_modifier.github_repo import GitHubRepo
        repo = GitHubRepo(cfg["github"])
        repo.reset(seed_files)
    else:
        repo = LocalRepo(seed_files)
    truth = Truth(extract(seed_files), FACTS)
    clock = HopClock(0.05 if mas_llm.backend in ("mock", "sim") else 20.0)
    mas_llm.sim_seed = f"{scenario}-{run}"
    modifier = Modifier(repo, truth, LEVELS[scenario], REQUIREMENTS, mod_llm, seed=run, clock=clock,
                        helpers=HELPERS, contributors=CONTRIBUTORS)
    base = repo.checkpoint()                      # what the MAS's PR is written against
    if not cfg.get("lockstep"):
        modifier.start()
    try:
        rows, final = run_chain(mas_llm, repo, TASK, truth, KEYWORDS, FOCUS_FILES, mode=mode, s=cfg.get("s", 0),
                                t_max=cfg.get("t_max"), rounds=cfg.get("rounds", 2), clock=clock,
                                lockstep=modifier if cfg.get("lockstep") else None, lookup=cfg.get("lookup", True),
                                drift_threshold=cfg.get("drift_threshold", 0))
    finally:
        modifier.stop()

    # The MAS's pull request: auto-merged into main like any other contributor's
    feature = {"files": [], "merged": None, "tests_pass": None, "stale_in_feature": []}
    changes = parse_files(final, repo.files())
    if changes:
        feature["files"] = sorted(changes)
        stale = truth.snapshot()["stale"]
        feature["stale_in_feature"] = sorted(n for n in stale if mentions(n, "\n".join(changes.values())))
        pr = repo.open_pr(f"Feature A: export button ({scenario}, mode={mode}, run {run})",
                          "Opened by the multi-agent system.", changes, author="taskbox-mas", base=base)
        if cfg.get("merge", True):
            feature["merged"] = repo.merge_pr(pr)
            if feature["merged"]:
                feature["tests_pass"] = run_node_tests(repo.files())
    for r in rows:
        r.update(scenario=scenario, mode=mode, run=run)
    return rows, {"scenario": scenario, "mode": mode, "run": run, "modifier_log": modifier.log,
                  "repo_events": len(repo.events), "feature": feature, "final_text": final}


def summarize(rows, meta):
    print(f"\n{'scenario':<10}{'mode':<6}{'retained':>9}{'final':>7}{'staleness':>10}{'refreshes':>10}"
          f"{'prompt tok':>11}{'recomputed':>11}{'merged':>8}{'tests ok':>9}")
    for sc, mode in dict.fromkeys((r["scenario"], r["mode"]) for r in rows):
        rs = [r for r in rows if r["scenario"] == sc and r["mode"] == mode]
        ms = [m["feature"] for m in meta if m["scenario"] == sc and m["mode"] == mode]
        n_runs = len({r["run"] for r in rs})
        last = max(r["hop"] for r in rs)
        pct = lambda key: (f"{100 * sum(bool(f[key]) for f in ms) / len(ms):.0f}%"
                           if any(f[key] is not None for f in ms) else "-")
        print(f"{sc:<10}{mode:<6}{statistics.mean(1 - r['info_loss'] for r in rs):>9.2f}"
              f"{statistics.mean(1 - r['info_loss'] for r in rs if r['hop'] == last):>7.2f}"
              f"{statistics.mean(r['unseen_relevant'] for r in rs):>10.2f}"
              f"{sum(r['refreshed'] for r in rs) / n_runs:>10.1f}"
              f"{sum(r['prompt_tokens'] for r in rs) / n_runs:>11.0f}{sum(r['recomputed_tokens'] for r in rs) / n_runs:>11.0f}"
              f"{pct('merged'):>8}{pct('tests_pass'):>9}")


def add_model_args(ap):
    ap.add_argument("--backend", choices=["ollama", "openai", "sim", "mock"])
    ap.add_argument("--base-url")
    ap.add_argument("--mas-model", default=None, help="default: $MODEL or qwen3:1.7b")
    ap.add_argument("--modifier-model", default=None)
    ap.add_argument("--sim-preset", choices=["small", "medium", "large"], default=None)


def make_llms(a):
    if a.sim_preset:
        os.environ["SIM_PRESET"] = a.sim_preset
    return (LLM(a.mas_model, a.backend, a.base_url),
            LLM(a.modifier_model or a.mas_model, a.backend, a.base_url, max_tokens=300))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenarios", default="static,light,moderate,heavy,churn")
    ap.add_argument("--modes", default="none,full,v,lean", help="also: drift, hybrid")
    ap.add_argument("--drift-threshold", type=int, default=0, help="drift mode: re-run a stage when its output contradicts more than this many V facts")
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--s", type=parse_s, default=0.0, help="count bound: refresh when relevant unseen changes > s ('inf' = off)")
    ap.add_argument("--t-max", type=float, default=1.0, help="time bound in agent calls a relevant change may wait (-1 = off)")
    ap.add_argument("--rounds", type=int, default=2, help="coder/reviewer rounds")
    ap.add_argument("--lockstep", action="store_true", help="modifier acts between stages (reproducible) instead of in parallel")
    ap.add_argument("--no-lookup", action="store_true", help="V mode without the lookup tool")
    ap.add_argument("--no-merge", action="store_true", help="open the MAS's PR but do not auto-merge it")
    ap.add_argument("--github", nargs="?", const=os.getenv("GITHUB_REPO", "Simra-F/sample_repository"),
                    help="run against the real GitHub sandbox; bare --github = $GITHUB_REPO or Simra-F/sample_repository "
                         "(default without the flag: in-memory repo)")
    ap.add_argument("--out", default="runs")
    add_model_args(ap)
    a = ap.parse_args(argv)

    modes = [m.strip() for m in a.modes.split(",") if m.strip()]
    bad = [m for m in modes if m not in MODES]
    if bad:
        ap.error(f"unknown mode(s) {bad}; choose from {', '.join(MODES)}")
    mas_llm, mod_llm = make_llms(a)
    cfg = {"s": a.s, "t_max": None if a.t_max < 0 else a.t_max, "rounds": a.rounds, "lockstep": a.lockstep,
           "lookup": not a.no_lookup, "github": a.github, "merge": not a.no_merge,
           "drift_threshold": a.drift_threshold}
    seed_files = load_seed()
    out = Path(a.out) / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)

    rows, meta = [], []
    for scenario in a.scenarios.split(","):
        for run in range(a.runs):
            for mode in modes:
                print(f"\n=== {scenario} | {mode} | run {run}")
                r, m = run_one(scenario, mode, run, cfg, mas_llm, mod_llm, seed_files)
                rows += r
                meta.append(m)
                (out / "results.json").write_text(json.dumps(
                    {"config": vars(a), "mas_model": mas_llm.model, "rows": rows, "runs": meta,
                     "usage": {"mas": mas_llm.usage, "modifier": mod_llm.usage}}, indent=1, default=str))
    summarize(rows, meta)
    print("\nGraphs:", ", ".join(plots.make_all(out)))
    print("Saved to", out)


if __name__ == "__main__":
    main()
