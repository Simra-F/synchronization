"""Comparison graphs: regular system (none) vs full re-read (full) vs source of truth V (v).
  python -m ctxdrift.plots runs/<dir>      (re-draw from results.json)"""
from __future__ import annotations
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# Reference categorical slots in fixed order per mode (none 1, full 2, v 3, drift 4, hybrid 5, lean 7); marker + line style as secondary encoding (slot 4 has low contrast).
STYLE = {
    "none": dict(color="#2a78d6", marker="o", linestyle="-", label="regular (no sync)"),
    "full": dict(color="#eb6834", marker="s", linestyle="--", label="full re-read"),
    "v": dict(color="#1baf7a", marker="^", linestyle="-", label="source of truth V"),
    "drift": dict(color="#eda100", marker="D", linestyle="-.", label="V, output-checked"),
    "hybrid": dict(color="#e87ba4", marker="P", linestyle=":", label="V + output check"),
    "lean": dict(color="#6250d6", marker="*", linestyle="-", label="V lean (briefs + code fixes)"),
}
for _m, _c in (("unknown", "#7a7974"),):
    STYLE[_m] = dict(color=_c, marker="x", linestyle="-", label=_m)

INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"


def _style_axes(ax):
    ax.set_facecolor("#fcfcfb")
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelsize=8)


def _mean_se(xs):
    m = statistics.mean(xs)
    return m, (statistics.stdev(xs) / len(xs) ** 0.5 if len(xs) > 1 else 0.0)


def _by_hop(rows, metric):
    """{scenario: {mode: [(hop, role, mean, se)]}}"""
    groups = defaultdict(list)
    for r in rows:
        groups[(r["scenario"], r["mode"], r["hop"], r["role"])].append(r[metric])
    out = defaultdict(lambda: defaultdict(list))
    for (sc, mode, hop, role), xs in sorted(groups.items(), key=lambda kv: kv[0][2]):
        out[sc][mode].append((hop, role, *_mean_se(xs)))
    return out


def line_by_hop(rows, metric, ylabel, title, path, ylim=None):
    data = _by_hop(rows, metric)
    scenarios = list(dict.fromkeys(r["scenario"] for r in rows))
    fig, axes = plt.subplots(1, len(scenarios), figsize=(3.6 * len(scenarios), 3.6), sharey=True, squeeze=False)
    for ax, sc in zip(axes[0], scenarios):
        _style_axes(ax)
        for mode, pts in data[sc].items():
            hops = [p[0] for p in pts]
            mean = [p[2] for p in pts]
            se = [p[3] for p in pts]
            st = STYLE.get(mode, STYLE["unknown"])
            ax.fill_between(hops, [m - e for m, e in zip(mean, se)], [m + e for m, e in zip(mean, se)],
                            color=st["color"], alpha=0.12, linewidth=0)
            ax.plot(hops, mean, color=st["color"], marker=st["marker"], linestyle=st["linestyle"],
                    linewidth=2, markersize=6, markeredgecolor="#fcfcfb", markeredgewidth=1.5, label=st["label"])
        pts = next(iter(data[sc].values()))
        ax.set_xticks([p[0] for p in pts])
        ax.set_xticklabels([f"{p[0]}\n{p[1]}" for p in pts], fontsize=7)
        ax.set_title(sc, fontsize=10, color=INK, loc="left")
        if ylim:
            ax.set_ylim(*ylim)
    axes[0][0].set_ylabel(ylabel, color=MUTED, fontsize=9)
    handles, labels = axes[0][0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper right", ncol=3, frameon=False, fontsize=8)
    fig.suptitle(title, x=0.01, ha="left", fontsize=11, color=INK)
    fig.supxlabel("agent call (time ->)", fontsize=8, color=MUTED)
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    fig.savefig(path, dpi=150, facecolor="#fcfcfb")
    plt.close(fig)


def per_agent(rows, path):
    """Mean information loss per sub-agent role, across all scenarios where the repo changes."""
    rows = [r for r in rows if r["scenario"] != "static"] or rows
    roles = list(dict.fromkeys(r["role"] for r in rows))
    modes = list(dict.fromkeys(r["mode"] for r in rows))
    fig, ax = plt.subplots(figsize=(7, 3.6))
    _style_axes(ax)
    width = 0.8 / len(modes)
    for i, mode in enumerate(modes):
        vals = [statistics.mean([r["info_loss"] for r in rows if r["role"] == role and r["mode"] == mode] or [0])
                for role in roles]
        xs = [j + (i - (len(modes) - 1) / 2) * width for j in range(len(roles))]
        ax.bar(xs, vals, width=width - 0.03, color=STYLE.get(mode, STYLE["unknown"])["color"], label=STYLE.get(mode, STYLE["unknown"])["label"],
               edgecolor="#fcfcfb", linewidth=1)
    ax.set_xticks(range(len(roles)))
    ax.set_xticklabels(roles, fontsize=8)
    ax.set_ylabel("mean information loss", color=MUTED, fontsize=9)
    ax.set_ylim(0, 1)
    ax.legend(frameon=False, fontsize=8, ncol=3, loc="upper left")
    ax.set_title("Information loss per sub-agent (all changing scenarios)", fontsize=11, color=INK, loc="left")
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor="#fcfcfb")
    plt.close(fig)


def loss_vs_changes(rows, path):
    """Each point = one run's final (lead) output: x = repo events landed by then, y = information loss."""
    finals = defaultdict(dict)
    for r in rows:
        key = (r["scenario"], r["mode"], r["run"])
        if r["hop"] >= finals[key].get("hop", 0):
            finals[key] = r
    fig, ax = plt.subplots(figsize=(6, 3.8))
    _style_axes(ax)
    for mode in dict.fromkeys(r["mode"] for r in rows):
        pts = sorted((f["repo_version"], f["info_loss"]) for (sc, m, _), f in finals.items() if m == mode)
        st = STYLE.get(mode, STYLE["unknown"])
        ax.scatter([p[0] for p in pts], [p[1] for p in pts], color=st["color"], marker=st["marker"], s=40,
                   edgecolors="#fcfcfb", linewidths=1.5, label=st["label"], zorder=3)
    ax.set_xlabel("repository changes landed during the task", color=MUTED, fontsize=9)
    ax.set_ylabel("final information loss", color=MUTED, fontsize=9)
    ax.set_ylim(-0.03, 1.03)
    ax.legend(frameon=False, fontsize=8)
    ax.set_title("Final output vs. amount of modification (one point per run)", fontsize=11, color=INK, loc="left")
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor="#fcfcfb")
    plt.close(fig)


def cost(rows, path):
    """Two measures, two charts: prompt tokens sent, and tokens a prefix cache would have to recompute."""
    scenarios = list(dict.fromkeys(r["scenario"] for r in rows))
    modes = list(dict.fromkeys(r["mode"] for r in rows))
    runs = defaultdict(set)
    for r in rows:
        runs[(r["scenario"], r["mode"])].add(r["run"])
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.6))
    for ax, metric, title in zip(axes, ["prompt_tokens", "recomputed_tokens"],
                                 ["Prompt tokens per run", "Tokens recomputed per run (prefix cache)"]):
        _style_axes(ax)
        width = 0.8 / len(modes)
        for i, mode in enumerate(modes):
            vals = [sum(r[metric] for r in rows if r["scenario"] == sc and r["mode"] == mode)
                    / max(1, len(runs[(sc, mode)])) for sc in scenarios]
            xs = [j + (i - (len(modes) - 1) / 2) * width for j in range(len(scenarios))]
            ax.bar(xs, vals, width=width - 0.03, color=STYLE.get(mode, STYLE["unknown"])["color"], label=STYLE.get(mode, STYLE["unknown"])["label"],
                   edgecolor="#fcfcfb", linewidth=1)
        ax.set_xticks(range(len(scenarios)))
        ax.set_xticklabels(scenarios, fontsize=8)
        ax.set_title(title, fontsize=10, color=INK, loc="left")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper right", ncol=3, frameon=False, fontsize=8)
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    fig.savefig(path, dpi=150, facecolor="#fcfcfb")
    plt.close(fig)


def make_all(out_dir):
    out_dir = Path(out_dir)
    rows = json.loads((out_dir / "results.json").read_text())["rows"]
    line_by_hop(rows, "info_loss", "information loss (0 = none)",
                "Information loss as agents are called and the repo keeps changing", out_dir / "info_loss_vs_hop.png", (-0.03, 1.03))
    line_by_hop(rows, "unseen_relevant", "relevant changes not yet seen",
                "Context staleness of each agent call", out_dir / "staleness_vs_hop.png")
    line_by_hop(rows, "stale_rate", "share of symbol mentions that are stale",
                "Stale symbol use", out_dir / "stale_rate_vs_hop.png", (-0.03, 1.03))
    per_agent(rows, out_dir / "per_agent_loss.png")
    loss_vs_changes(rows, out_dir / "loss_vs_changes.png")
    cost(rows, out_dir / "cost.png")
    return sorted(p.name for p in out_dir.glob("*.png"))


if __name__ == "__main__":
    print(make_all(sys.argv[1]))
