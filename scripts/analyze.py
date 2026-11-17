"""Summarize a run_experiment results.json: per-scenario table, overall per mode with standard errors, paired
differences against the regular system, retention per subagent, most-dropped facts, and the MAS's PRs.

  python3 scripts/analyze.py                       # newest runs/*/results.json
  python3 scripts/analyze.py runs/20261116-234932  # a specific run
  python3 scripts/analyze.py --tests               # also re-run node --test on seed + the MAS's files
"""
from __future__ import annotations
import argparse
import collections
import glob
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ctxdrift.pipeline import parse_files  # noqa: E402
from ctxdrift.task import load_seed  # noqa: E402

ROLES = ["planner", "researcher", "coder", "reviewer", "lead"]


def mean_se(xs):
    xs = list(xs)
    if not xs:
        return float("nan"), float("nan")
    m = statistics.mean(xs)
    return m, (statistics.stdev(xs) / len(xs) ** 0.5 if len(xs) > 1 else 0.0)


def chain_stats(rows):
    """One record per chain (scenario, mode, run)."""
    by = collections.defaultdict(list)
    for r in rows:
        by[(r["scenario"], r["mode"], r["run"])].append(r)
    out = {}
    for key, rs in by.items():
        last = max(r["hop"] for r in rs)
        out[key] = {
            "retained": statistics.mean(1 - r["info_loss"] for r in rs),
            "final": statistics.mean(1 - r["info_loss"] for r in rs if r["hop"] == last),
            "staleness": statistics.mean(r["unseen_relevant"] for r in rs),
            "prompt": sum(r["prompt_tokens"] for r in rs),
            "recomputed": sum(r["recomputed_tokens"] for r in rs),
            "calls": len(rs) + sum(r.get("extra_calls", 0) for r in rs),
        }
    return out


def node_test(files):
    if not shutil.which("node"):
        return None, "node not installed"
    with tempfile.TemporaryDirectory() as d:
        for path, text in files.items():
            f = Path(d) / path
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text)
        p = subprocess.run(["node", "--test"], cwd=d, capture_output=True, text=True, timeout=120)
        lines = [l for l in (p.stdout + p.stderr).splitlines() if "not ok" in l or "Error" in l or "error" in l]
        return p.returncode == 0, "\n      ".join(lines[:4])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run", nargs="?", help="runs/<timestamp> folder or results.json (default: newest)")
    ap.add_argument("--tests", action="store_true")
    a = ap.parse_args()
    path = a.run or sorted(glob.glob("runs/*/results.json"))[-1]
    if os.path.isdir(path):
        path = os.path.join(path, "results.json")
    data = json.load(open(path))
    rows, runs = data["rows"], data["runs"]
    print(f"{path}  model={data.get('mas_model')}  chains={len(runs)}")

    chains = chain_stats(rows)
    modes = list(dict.fromkeys(r["mode"] for r in rows))
    scenarios = list(dict.fromkeys(r["scenario"] for r in rows))
    changing = [s for s in scenarios if s != "static"]

    print("\n== Per scenario (mean over runs)")
    print(f"{'scenario':<10}{'mode':<8}{'n':>3}{'retained':>10}{'final':>8}{'stale':>7}{'prompt':>9}{'recomp':>8}{'merged':>8}{'tests':>7}")
    for sc in scenarios:
        for m in modes:
            cs = [v for (s, mm, _), v in chains.items() if s == sc and mm == m]
            fs = [x["feature"] for x in runs if x["scenario"] == sc and x["mode"] == m]
            if not cs:
                continue
            merged = sum(bool(f["merged"]) for f in fs)
            tests = sum(f["tests_pass"] is True for f in fs)
            print(f"{sc:<10}{m:<8}{len(cs):>3}{statistics.mean(c['retained'] for c in cs):>10.3f}"
                  f"{statistics.mean(c['final'] for c in cs):>8.3f}{statistics.mean(c['staleness'] for c in cs):>7.2f}"
                  f"{statistics.mean(c['prompt'] for c in cs):>9.0f}{statistics.mean(c['recomputed'] for c in cs):>8.0f}"
                  f"{merged:>5}/{len(fs):<2}{tests:>4}/{merged:<2}")

    print(f"\n== Overall across {', '.join(changing)} (mean +- standard error over chains)")
    print(f"{'mode':<8}{'n':>3}{'retained':>16}{'final (lead)':>16}{'stale':>7}{'prompt':>9}{'recomp':>8}{'calls':>7}")
    for m in modes:
        cs = [v for (s, mm, _), v in chains.items() if mm == m and s in changing]
        r, rse = mean_se(c["retained"] for c in cs)
        f, fse = mean_se(c["final"] for c in cs)
        print(f"{m:<8}{len(cs):>3}{r:>10.3f} +-{rse:.3f}{f:>10.3f} +-{fse:.3f}{statistics.mean(c['staleness'] for c in cs):>7.2f}"
              f"{statistics.mean(c['prompt'] for c in cs):>9.0f}{statistics.mean(c['recomputed'] for c in cs):>8.0f}"
              f"{statistics.mean(c['calls'] for c in cs):>7.1f}")

    if "none" in modes:
        print("\n== Paired against the regular system (same scenario and run, so the same modifier changes)")
        print(f"{'mode':<8}{'n':>3}{'retained diff':>18}{'final diff':>18}{'wins/ties/losses':>18}")
        for m in modes:
            if m == "none":
                continue
            d_ret, d_fin, w = [], [], [0, 0, 0]
            for (s, mm, run), v in chains.items():
                base = chains.get((s, "none", run))
                if mm != m or s not in changing or not base:
                    continue
                d = v["retained"] - base["retained"]
                d_ret.append(d)
                d_fin.append(v["final"] - base["final"])
                w[0 if d > 0.005 else 1 if d >= -0.005 else 2] += 1
            r, rse = mean_se(d_ret)
            f, fse = mean_se(d_fin)
            sig = "  *" if rse and abs(r) > 2 * rse else ""
            print(f"{m:<8}{len(d_ret):>3}{r:>+11.3f} +-{rse:.3f}{f:>+11.3f} +-{fse:.3f}{'/'.join(map(str, w)):>18}{sig}")
        print("   * = difference larger than 2 standard errors")

    print("\n== Retained per subagent (changing scenarios)")
    print(f"{'mode':<8}" + "".join(f"{r:>12}" for r in ROLES))
    for m in modes:
        cells = []
        for role in ROLES:
            xs = [1 - r["info_loss"] for r in rows if r["mode"] == m and r["role"] == role and r["scenario"] in changing]
            cells.append(f"{statistics.mean(xs):>12.3f}" if xs else f"{'-':>12}")
        print(f"{m:<8}" + "".join(cells))

    print("\n== Most-dropped facts (share of subagent outputs missing them) and stale names mentioned")
    for m in modes:
        rs = [r for r in rows if r["mode"] == m and r["scenario"] in changing]
        failed = collections.Counter(f for r in rs for f in r.get("failed_facts", []))
        stale = collections.Counter(n for r in rs for n in r.get("stale_mentions", []))
        top = ", ".join(f"{k} {100 * v / len(rs):.0f}%" for k, v in failed.most_common(4))
        st = ", ".join(f"{k} x{v}" for k, v in stale.most_common(3)) or "none"
        print(f"{m:<8} dropped: {top or 'none'}\n{'':<8} stale: {st}")

    print("\n== The MAS's pull requests")
    seed = load_seed() if a.tests else None
    for x in runs:
        f = x["feature"]
        line = (f"{x['scenario']:<9}{x['mode']:<7}run {x['run']}: "
                + (f"files={','.join(f['files'])} merged={f['merged']} tests={f['tests_pass']}"
                   if f["files"] else "no files in the lead's answer (no PR)"))
        if f.get("stale_in_feature"):
            line += f" stale={f['stale_in_feature']}"
        print(line)
        if a.tests and f["files"]:
            ok, why = node_test({**seed, **parse_files(x["final_text"], seed)})
            print(f"      seed + MAS files: node --test {'passes' if ok else 'FAILS'}" + (f"\n      {why}" if why and not ok else ""))


if __name__ == "__main__":
    main()
