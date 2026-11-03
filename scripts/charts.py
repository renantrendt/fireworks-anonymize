#!/usr/bin/env python3
"""Charts for the layer experiments (reads data/experiments/<prefix>/results.json).

  python scripts/charts.py --prefix glm-5p3-flash

Writes PNGs to data/experiments/<prefix>/charts/:
  1_layers.png     typed F1 and leak recall per layer (two panels, one scale each)
  2_mcnemar.png    entities each step gained vs lost against the layer before it
  3_bootstrap.png  F1 gap per step with its 95% bootstrap interval
  4_labels.png     per-label F1, L1 baseline vs the best layer
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evaluate as ev  # noqa: E402

# Light-mode roles from the reference palette (validated: blue/orange pass all checks).
SURFACE = "#ffffff"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e6e5e0"
BLUE = "#2a78d6"      # series 1: the system / gains
ORANGE = "#eb6834"    # series 2: baseline / losses
MUTED = "#b9b8b2"     # not significant

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 10, "text.color": INK,
    "axes.edgecolor": GRID, "axes.labelcolor": INK_2, "axes.titlecolor": INK,
    "axes.titlesize": 12, "axes.titleweight": "bold", "axes.titlelocation": "left",
    "xtick.color": INK_2, "ytick.color": INK, "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE, "savefig.dpi": 200,
})


def clean(ax, grid_axis="x"):
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.grid(axis=grid_axis, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(length=0)


def layers_chart(res: dict, out: Path) -> None:
    layers = res["layers"]
    names = [f"{l['layer']}  {l['desc']}" for l in layers][::-1]
    f1 = [100 * (l["labeled"]["f1"] or 0) for l in layers][::-1]
    leak = [100 * (l["leak_recall"] or 0) for l in layers][::-1]
    best = max(range(len(f1)), key=lambda i: f1[i])
    fig, axes = plt.subplots(1, 2, figsize=(11, 0.55 * len(names) + 1.6), sharey=True,
                             gridspec_kw={"wspace": 0.08})
    for ax, vals, title in ((axes[0], f1, "Typed F1 (exact value + label)"),
                            (axes[1], leak, "Leak recall (value fully masked)")):
        ax.barh(names, vals, height=0.5, color=BLUE)
        for i, v in enumerate(vals):
            ax.text(v + 1, i, f"{v:.1f}", va="center", fontsize=9,
                    color=INK, fontweight="bold" if (ax is axes[0] and i == best) else "normal")
        ax.set_xlim(0, 112)
        ax.set_xticks([0, 25, 50, 75, 100])
        ax.set_title(title, fontsize=10.5)
        clean(ax)
    fig.suptitle("Prompt harness (L1-L5) and LoRA fine-tune (L6) on glm-5p3-flash, 500 test texts",
                 x=0.01, ha="left", fontsize=12, fontweight="bold", y=0.99)
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)


def step_stats(res: dict) -> list[dict]:
    """Each layer vs the layer directly before it (skipping L0), plus best vs L1."""
    order = [l["layer"] for l in res["layers"]]
    by = {(s["old"], s["new"]): s for s in res["stats"]}
    steps = []
    for prev, cur in zip(order, order[1:]):
        if prev == "L0":
            continue
        s = by.get((prev, cur)) or (by.get(("L1", cur)) if prev == "L1" else None)
        if s:
            steps.append({**s, "name": f"{cur} vs {prev}"})
    f1 = {l["layer"]: l["labeled"]["f1"] or 0 for l in res["layers"] if l["layer"] != "L0"}
    best = max(f1, key=f1.get)
    if best != "L1" and ("L1", best) in by and best != order[order.index("L1") + 1]:
        steps.append({**by[("L1", best)], "name": f"{best} vs L1 (total)"})
    return steps


def mcnemar_chart(res: dict, out: Path) -> None:
    steps = step_stats(res)[::-1]
    names = [s["name"] for s in steps]
    y = range(len(steps))
    h = 0.36
    fig, ax = plt.subplots(figsize=(9.5, 0.75 * len(steps) + 1.6))
    ax.barh([i + h / 2 + 0.02 for i in y], [s["gained"] for s in steps], height=h, color=BLUE,
            label="Gained: only the new layer got it right")
    ax.barh([i - h / 2 - 0.02 for i in y], [s["lost"] for s in steps], height=h, color=ORANGE,
            label="Lost: only the old layer got it right")
    top = max(max(s["gained"], s["lost"]) for s in steps)
    for i, s in zip(y, steps):
        ax.text(s["gained"] + top * 0.01, i + h / 2 + 0.02, str(s["gained"]), va="center", fontsize=9)
        ax.text(s["lost"] + top * 0.01, i - h / 2 - 0.02, str(s["lost"]), va="center", fontsize=9)
        p = s["p"]
        ptxt = "p < 0.001" if p < 0.001 else f"p = {p:.3f}"
        ax.text(top * 1.32, i, ptxt, va="center", ha="right", fontsize=9,
                color=INK if p < 0.05 else INK_2, fontweight="bold" if p < 0.05 else "normal")
    ax.set_yticks(list(y))
    ax.set_yticklabels(names)
    ax.set_xlim(0, top * 1.34)
    ax.set_xlabel("Gold entities where the two layers disagree")
    ax.set_title("McNemar: who wins the disagreements? (p < 0.05 in bold)")
    ax.legend(loc="lower center", bbox_to_anchor=(0.45, -0.32 - 0.02 * (6 - len(steps))), ncol=2,
              frameon=False, fontsize=9)
    clean(ax)
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)


def bootstrap_chart(res: dict, out: Path) -> None:
    steps = step_stats(res)[::-1]
    fig, ax = plt.subplots(figsize=(9.5, 0.6 * len(steps) + 1.5))
    for i, s in enumerate(steps):
        real = s["verdict"] in ("real gain", "real loss")
        color = (BLUE if s["gap"] > 0 else ORANGE) if real else MUTED
        ax.plot([s["lo"], s["hi"]], [i, i], color=color, linewidth=2, solid_capstyle="round")
        ax.plot(s["gap"], i, "o", markersize=8, color=color, markeredgecolor=SURFACE, markeredgewidth=2)
        ax.text(max(s["hi"], s["gap"]) + 0.4, i, f"{s['gap']:+.1f}  [{s['lo']:+.1f}, {s['hi']:+.1f}]  {s['verdict']}",
                va="center", fontsize=9, color=INK if real else INK_2)
    ax.axvline(0, color=INK_2, linewidth=1, linestyle=(0, (3, 3)))
    lo = min(s["lo"] for s in steps)
    hi = max(s["hi"] for s in steps)
    ax.set_xlim(min(lo - 1, -1), hi + (hi - lo) * 0.9 + 4)
    ax.set_yticks(range(len(steps)))
    ax.set_yticklabels([s["name"] for s in steps])
    ax.set_xlabel("Typed F1 gap in points (95% interval from 5,000 resamples of the test rows)")
    ax.set_title("Bootstrap: would the gap hold on a different test set?")
    clean(ax)
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)


def labels_chart(res: dict, out: Path) -> None:
    layers = {l["layer"]: l for l in res["layers"]}
    best = max((l for l in res["layers"] if l["layer"] != "L0"), key=lambda l: l["labeled"]["f1"] or 0)
    rep_base = json.loads((ev.RUNS_DIR / layers["L1"]["run"] / "report.json").read_text())
    rep_best = json.loads((ev.RUNS_DIR / best["run"] / "report.json").read_text())
    rows = []
    for label, s in rep_best["per_label"].items():
        if s["low_n"]:
            continue
        a = 100 * (rep_base["labeled"]["per_label"].get(label, {}).get("f1") or 0)
        b = 100 * (rep_best["labeled"]["per_label"].get(label, {}).get("f1") or 0)
        rows.append((label, s["n"], a, b))
    rows.sort(key=lambda r: r[3] - r[2])
    fig, ax = plt.subplots(figsize=(9.5, 0.32 * len(rows) + 1.6))
    for i, (label, n, a, b) in enumerate(rows):
        ax.plot([a, b], [i, i], color=GRID, linewidth=2, zorder=1)
        ax.plot(a, i, "o", color=ORANGE, markersize=8, markeredgecolor=SURFACE, markeredgewidth=2, zorder=2)
        ax.plot(b, i, "o", color=BLUE, markersize=8, markeredgecolor=SURFACE, markeredgewidth=2, zorder=3)
        if abs(b - a) >= 5:
            ax.text(max(a, b) + 1.5, i, f"{b - a:+.0f}", va="center", fontsize=8.5, color=INK)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([f"{r[0]} ({r[1]})" for r in rows], fontsize=9)
    ax.set_xlim(min(min(r[2], r[3]) for r in rows) - 5, 106)
    ax.set_xlabel("Typed F1 (%) per label; changes of 5+ points labeled; (n) = gold instances")
    ax.set_title(f"Where the gain came from: L1 baseline vs {best['layer']}")
    ax.plot([], [], "o", color=ORANGE, label="L1 baseline")
    ax.plot([], [], "o", color=BLUE, label=f"{best['layer']} (best)")
    ax.legend(loc="lower right", frameon=False, fontsize=9)
    clean(ax)
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--prefix", default="glm-5p3-flash")
    args = p.parse_args()
    exp = ev.DATA_DIR / "experiments" / args.prefix
    res = json.loads((exp / "results.json").read_text())
    out = exp / "charts"
    out.mkdir(exist_ok=True)
    layers_chart(res, out / "1_layers.png")
    mcnemar_chart(res, out / "2_mcnemar.png")
    bootstrap_chart(res, out / "3_bootstrap.png")
    labels_chart(res, out / "4_labels.png")
    print(f"Wrote 4 charts to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
