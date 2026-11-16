"""Sweep V's two staleness bounds and pick values.
  count bound s     : refresh when more than s relevant changes are unseen        (s = inf: count trigger off)
  time bound t_max  : refresh when a relevant change has waited t_max agent calls (off: time trigger off)
The regular system (none) and the full re-read (full) are run on the same seeds as reference points.

Decision rule: among V settings, take the best mean information retained across the changing scenarios; every
setting within --epsilon of it is "as good"; of those pick the cheapest (fewest tokens a prefix cache must recompute,
then fewest refreshes). Cheap is the point of V, so we do not pay for retention we cannot measure.

  python -m s_values_testing.sweep --backend sim --sim-preset small --runs 20          # minutes, no GPU
  python -m s_values_testing.sweep --backend ollama --mas-model qwen3:4b --runs 3 \\
         --s-values 0,1,2,inf --t-values off,2                                          # Colab GPU
"""
from __future__ import annotations
import argparse
import json
import math
import statistics
import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from ctxdrift.plots import INK, MUTED, STYLE, _style_axes  # noqa: E402
from ctxdrift.run_experiment import add_model_args, make_llms, parse_s, run_one  # noqa: E402
from ctxdrift.task import load_seed  # noqa: E402

BLUES = ["#9ec5f4", "#5598e7", "#2a78d6", "#1c5cab", "#104281", "#0d366b"]   # sequential ramp, light -> dark


def label(s, t):
    return f"s={'inf' if math.isinf(s) else int(s)}, t={'off' if t is None else int(t)}"


def run_sweep(a, mas_llm, mod_llm):
    seed = load_seed()
    s_values = [parse_s(x) for x in a.s_values.split(",")]
    t_values = [None if x in ("off", "none") else float(x) for x in a.t_values.split(",")]
    configs = [("none", None, None), ("full", None, None)] + [(a.v_mode, s, t) for s in s_values for t in t_values]
    rows = []
    for scenario in a.scenarios.split(","):
        for run in range(a.runs):
            for mode, s, t in configs:
                cfg = {"s": s if s is not None else 0, "t_max": t, "lockstep": not a.parallel, "rounds": a.rounds,
                       "lookup": not a.no_lookup, "merge": not a.no_merge}
                hops, meta = run_one(scenario, mode, run, cfg, mas_llm, mod_llm, seed)
                rows.append({
                    "scenario": scenario, "run": run, "mode": mode, "s": s, "t": t,
                    "retained": statistics.mean(1 - h["info_loss"] for h in hops),
                    "final_retained": 1 - hops[-1]["info_loss"],
                    "staleness": statistics.mean(h["unseen_relevant"] for h in hops),
                    "refreshes": sum(h["refreshed"] for h in hops),
                    "time_triggers": sum(h["trigger"] == "time" for h in hops),
                    "update_tokens": sum(h["update_tokens"] for h in hops),
                    "prompt_tokens": sum(h["prompt_tokens"] for h in hops),
                    "recomputed_tokens": sum(h["recomputed_tokens"] for h in hops),
                    "merged": meta["feature"]["merged"], "tests_pass": meta["feature"]["tests_pass"],
                })
            print(f"  {scenario} run {run} done")
    return rows


def aggregate(rows):
    keys = list(dict.fromkeys((r["mode"], r["s"], r["t"]) for r in rows))
    out = []
    for mode, s, t in keys:
        rs = [r for r in rows if (r["mode"], r["s"], r["t"]) == (mode, s, t)]
        agg = {"mode": mode, "s": s, "t": t, "label": mode if mode in ("none", "full") else label(s, t), "n": len(rs)}
        for k in ("retained", "final_retained", "staleness", "refreshes", "time_triggers", "update_tokens",
                  "prompt_tokens", "recomputed_tokens"):
            agg[k] = statistics.mean(r[k] for r in rs)
        agg["se"] = statistics.stdev([r["retained"] for r in rs]) / len(rs) ** 0.5 if len(rs) > 1 else 0.0
        merged = [r["merged"] for r in rs if r["merged"] is not None]
        agg["merge_rate"] = statistics.mean(merged) if merged else None
        out.append(agg)
    return out


def recommend(agg, epsilon):
    v = [x for x in agg if x["mode"] not in ("none", "full")]
    best = max(x["retained"] for x in v)
    good = [x for x in v if x["retained"] >= best - epsilon]
    pick = min(good, key=lambda x: (x["recomputed_tokens"], x["refreshes"], -(x["s"] if not math.isinf(x["s"]) else 99)))
    return pick, best, good


def plot_retained_vs_s(rows, path):
    scenarios = list(dict.fromkeys(r["scenario"] for r in rows))
    s_vals = sorted({r["s"] for r in rows if r["mode"] not in ("none", "full")})
    t_vals = list(dict.fromkeys(r["t"] for r in rows if r["mode"] not in ("none", "full")))
    xpos = {s: i for i, s in enumerate(s_vals)}
    fig, axes = plt.subplots(1, len(scenarios), figsize=(3.6 * len(scenarios), 3.6), sharey=True, squeeze=False)
    for ax, sc in zip(axes[0], scenarios):
        _style_axes(ax)
        for ref, ls in (("none", ":"), ("full", "--")):
            vals = [r["retained"] for r in rows if r["scenario"] == sc and r["mode"] == ref]
            if vals:
                y = statistics.mean(vals)
                ax.axhline(y, color=MUTED, linestyle=ls, linewidth=1.2)
                ax.text(len(s_vals) - 0.95, y, f" {ref}", color=MUTED, fontsize=7, va="center")
        for i, t in enumerate(t_vals):
            ys = [statistics.mean([r["retained"] for r in rows if r["scenario"] == sc and r["mode"] not in ("none", "full")
                                   and r["s"] == s and r["t"] == t]) for s in s_vals]
            ax.plot(range(len(s_vals)), ys, color=BLUES[min(i + 1, len(BLUES) - 1)] if t is not None else "#9ec5f4",
                    marker="o", linewidth=2, markersize=6, markeredgecolor="#fcfcfb", markeredgewidth=1.5,
                    label=f"t_max={'off' if t is None else int(t)}")
        ax.set_xticks(range(len(s_vals)))
        ax.set_xticklabels(["inf" if math.isinf(s) else str(int(s)) for s in s_vals], fontsize=8)
        ax.set_title(sc, fontsize=10, color=INK, loc="left")
        ax.set_xlim(-0.3, len(s_vals) - 0.2 + 0.6)
    axes[0][0].set_ylabel("information retained (mean over agents)", color=MUTED, fontsize=9)
    h, l = axes[0][0].get_legend_handles_labels()
    fig.legend(h, l, loc="upper right", ncol=len(t_vals), frameon=False, fontsize=8)
    fig.suptitle("V: retention vs count bound s, per time bound", x=0.01, ha="left", fontsize=11, color=INK)
    fig.supxlabel("count bound s (refresh when more than s relevant changes are unseen)", fontsize=8, color=MUTED)
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    fig.savefig(path, dpi=150, facecolor="#fcfcfb")
    plt.close(fig)


def plot_pareto(agg, pick, path):
    fig, ax = plt.subplots(figsize=(7, 4.2))
    _style_axes(ax)
    v = sorted([x for x in agg if x["mode"] not in ("none", "full")], key=lambda x: x["recomputed_tokens"])
    ax.scatter([x["recomputed_tokens"] for x in v], [x["retained"] for x in v], color=STYLE[v[0]["mode"]]["color"],
               marker="^", s=40, edgecolors="#fcfcfb", linewidths=1.5, label="V settings", zorder=3)
    frontier, best = [], -1
    for x in v:
        if x["retained"] > best:
            frontier.append(x)
            best = x["retained"]
    ax.plot([x["recomputed_tokens"] for x in frontier], [x["retained"] for x in frontier],
            color=STYLE[v[0]["mode"]]["color"], linewidth=1.5, zorder=2, label="best retention for the cost")
    for mode in ("none", "full"):
        for x in agg:
            if x["mode"] == mode:
                st = STYLE[mode]
                ax.scatter([x["recomputed_tokens"]], [x["retained"]], color=st["color"], marker=st["marker"], s=60,
                           edgecolors="#fcfcfb", linewidths=1.5, label=st["label"], zorder=4)
    ax.annotate(f"recommended\n{pick['label']}", (pick["recomputed_tokens"], pick["retained"]),
                textcoords="offset points", xytext=(12, -28), fontsize=8, color=INK,
                arrowprops=dict(arrowstyle="-", color=MUTED, linewidth=0.8))
    ax.set_xlabel("tokens a prefix cache must recompute per run (cost)", color=MUTED, fontsize=9)
    ax.set_ylabel("information retained", color=MUTED, fontsize=9)
    ax.legend(frameon=False, fontsize=8, loc="lower right")
    ax.set_title("Retention vs cost, averaged over changing scenarios", fontsize=11, color=INK, loc="left")
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor="#fcfcfb")
    plt.close(fig)


def plot_heatmap(agg, path):
    v = [x for x in agg if x["mode"] not in ("none", "full")]
    s_vals = sorted({x["s"] for x in v})
    t_vals = list(dict.fromkeys(x["t"] for x in v))
    grid = [[next(x["retained"] for x in v if x["s"] == s and x["t"] == t) for s in s_vals] for t in t_vals]
    fig, ax = plt.subplots(figsize=(1.2 * len(s_vals) + 2, 0.7 * len(t_vals) + 1.6))
    lo, hi = min(map(min, grid)), max(map(max, grid))
    im = ax.imshow(grid, cmap=matplotlib.colors.LinearSegmentedColormap.from_list("blues", BLUES), vmin=lo, vmax=hi, aspect="auto")
    for i, row in enumerate(grid):
        for j, val in enumerate(row):
            ax.text(j, i, f"{val:.3f}", ha="center", va="center", fontsize=8,
                    color="#fcfcfb" if (val - lo) / max(hi - lo, 1e-9) > 0.55 else INK)
    ax.set_xticks(range(len(s_vals)))
    ax.set_xticklabels(["inf" if math.isinf(s) else str(int(s)) for s in s_vals], fontsize=8)
    ax.set_yticks(range(len(t_vals)))
    ax.set_yticklabels(["off" if t is None else str(int(t)) for t in t_vals], fontsize=8)
    ax.set_xlabel("count bound s", color=MUTED, fontsize=9)
    ax.set_ylabel("time bound t_max (agent calls)", color=MUTED, fontsize=9)
    ax.set_title("Information retained by (s, t_max)", fontsize=11, color=INK, loc="left")
    fig.colorbar(im, ax=ax, fraction=0.04)
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor="#fcfcfb")
    plt.close(fig)


def report(rows, out, epsilon):
    changing = [r for r in rows if r["scenario"] != "static"] or rows
    agg = aggregate(changing)
    pick, best, good = recommend(agg, epsilon)
    print(f"\n{'setting':<16}{'retained':>9}{'+-se':>7}{'final':>7}{'stale':>7}{'refresh':>8}{'by time':>8}"
          f"{'upd tok':>8}{'recomp':>8}")
    for x in sorted(agg, key=lambda x: -x["retained"]):
        print(f"{x['label']:<16}{x['retained']:>9.3f}{x['se']:>7.3f}{x['final_retained']:>7.3f}{x['staleness']:>7.2f}"
              f"{x['refreshes']:>8.2f}{x['time_triggers']:>8.2f}{x['update_tokens']:>8.0f}{x['recomputed_tokens']:>8.0f}")
    print(f"\nBest V retention {best:.3f}; {len(good)} settings within {epsilon}. Recommended: {pick['label']}")
    (out / "recommendation.json").write_text(json.dumps(
        {"recommended": {"s": pick["s"], "t_max": pick["t"]}, "best_retained": best, "epsilon": epsilon,
         "as_good": [x["label"] for x in good], "table": agg}, indent=1, default=str))
    plot_retained_vs_s(rows, out / "retained_vs_s.png")
    plot_pareto(agg, pick, out / "pareto.png")
    plot_heatmap(agg, out / "heatmap_s_t.png")
    return pick


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--s-values", default="0,1,2,3,5,inf")
    ap.add_argument("--t-values", default="off,1,2,3,5")
    ap.add_argument("--scenarios", default="light,moderate,heavy,churn")
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--epsilon", type=float, default=0.01)
    ap.add_argument("--v-mode", default="lean", choices=["v", "hybrid", "lean"], help="which V variant to sweep")
    ap.add_argument("--parallel", action="store_true", help="modifier in its own thread (default: lockstep, paired seeds)")
    ap.add_argument("--no-lookup", action="store_true")
    ap.add_argument("--no-merge", action="store_true")
    ap.add_argument("--out", default="s_values_testing/results")
    add_model_args(ap)
    a = ap.parse_args(argv)
    mas_llm, mod_llm = make_llms(a)
    out = Path(a.out) / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    rows = run_sweep(a, mas_llm, mod_llm)
    (out / "sweep.json").write_text(json.dumps({"config": vars(a), "rows": rows}, indent=1, default=str))
    report(rows, out, a.epsilon)
    print("Saved to", out)


if __name__ == "__main__":
    main()
