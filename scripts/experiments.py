#!/usr/bin/env python3
"""Layer-by-layer experiments on the frozen test set, with significance tests.

Every layer uses the same model and adds one idea to the layer below it:

  L0  regex only (EMAIL, IP)                     no model
  L1  baseline: plain prompt, labels named       is a model alone enough?
  L2  + label guide                              the model doesn't know what BOD / BUILDING mean here
  L3  + 3 few-shot examples (train split)        it needs to see how names and addresses are split
  L4  + grounding + regex merge                  it rewrites values and misses fixed formats
  L5  + second pass on the masked text           one look misses things
  L6  + union with a second model's run          different models miss different things

For each layer vs the baseline (L1) and vs the layer below:
  McNemar   per gold entity, found with the right label or not -> exact p-value
  Bootstrap resample test rows 5,000x -> 95% CI of the labeled micro-F1 gap

Usage:
  export FIREWORKS_API_KEY=...
  python scripts/experiments.py --model glm-5p3-flash --partner harness-qwen3p8-max
  python scripts/experiments.py --model glm-5p3-flash --partner harness-qwen3p8-max --report-only
Writes data/experiments/<prefix>/results.md and results.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import replace
from fractions import Fraction
from math import comb
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evaluate as ev  # noqa: E402
import harness as hz  # noqa: E402

EXP_DIR = ev.DATA_DIR / "experiments"
BOOT = 5000


def ladder(model: str) -> list[tuple[str, str, hz.Config | None]]:
    base = hz.Config(model=model, prompt="plain", shots=0, ground=False, regex=False, passes=1)
    l2 = replace(base, prompt="guide")
    l3 = replace(l2, shots=3)
    l4 = replace(l3, ground=True, regex=True)
    l5 = replace(l4, passes=2)
    return [
        ("L0", "Regex only (EMAIL, IP), no model", replace(base, llm=False, regex=True)),
        ("L1", "Baseline: plain prompt", base),
        ("L2", "+ label guide", l2),
        ("L3", "+ 3 few-shot examples (train split)", l3),
        ("L4", "+ grounding + regex merge", l4),
        ("L5", "+ second pass on masked text", l5),
    ]


# ---------------------------------------------------------------- loading

def load_preds(run: str) -> dict[str, dict]:
    path = ev.RUNS_DIR / run / "predictions.jsonl"
    with path.open() as f:
        return {p["id"]: p for p in map(json.loads, f)}  # last line per id wins


def union_run(name: str, first: str, second: str, rows: list[dict]) -> None:
    """L6: entities from both runs; the first run wins label conflicts."""
    a, b = load_preds(first), load_preds(second)
    out_dir = ev.RUNS_DIR / name
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "predictions.jsonl").open("w") as out:
        for row in rows:
            pa, pb = a.get(row["id"]), b.get(row["id"])
            if pa is None or pb is None:
                continue
            ok = [p for p in (pa, pb) if not p["error"]]
            ents = hz.merge(*[p["entities"] for p in ok])
            usage: dict = {}
            for p in ok:
                hz.add_usage(usage, p.get("usage") or {})
            rec = {"id": row["id"], "backend": f"{pa['backend']} + {pb['backend']}", "gliner": "off",
                   "entities": ents, "output_text": hz.mask(row["source_text"], ents),
                   "replaced": {e["value"]: e["label"] for e in ents}, "fakes": [], "usage": usage,
                   "usage_complete": all("usage" in p for p in (pa, pb)),
                   "error": None if ok else pa["error"],
                   "seconds": max(pa["seconds"], pb["seconds"])}  # the two calls run in parallel
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- per-row and per-entity outcomes

def outcomes(rows: list[dict], preds: dict[str, dict], canonical: dict) -> dict:
    """Per gold entity: labeled hit (exact value + label) and not leaked.
    Per row: tp/fp/fn for labeled micro-F1."""
    hit, safe, row_counts = [], [], []
    for row in rows:
        p = preds.get(row["id"])
        bad = p is None or p["error"]
        got = Counter() if bad else Counter((e["value"].strip(), e["label"]) for e in p["entities"])
        out = row["source_text"] if bad else p["output_text"]
        gold = [(e["value"].strip(), canonical.get(e["label"], e["label"])) for e in row["entities"]]
        used = Counter()
        for key in gold:
            used[key] += 1
            hit.append(used[key] <= got[key])
            safe.append(not any(ev.word_leaked(w, out, set()) for w in ev.value_words(key[0])))
        gc = Counter(gold)
        tp = sum((gc & got).values())
        row_counts.append((tp, sum(got.values()) - tp, sum(gc.values()) - tp))
    return {"hit": np.array(hit), "safe": np.array(safe), "rows": np.array(row_counts, dtype=float)}


def f1(counts: np.ndarray) -> float:
    tp, fp, fn = counts.sum(axis=0)
    return 2 * tp / (2 * tp + fp + fn) if tp else 0.0


def mcnemar(old: np.ndarray, new: np.ndarray) -> dict:
    """Exact two-sided McNemar on paired pass/fail outcomes."""
    gained = int((~old & new).sum())   # only the new system right
    lost = int((old & ~new).sum())     # only the old system right
    n, k = gained + lost, min(gained, lost)
    p = min(1.0, float(2 * Fraction(sum(comb(n, i) for i in range(k + 1)), 2 ** n))) if n else 1.0
    return {"gained": gained, "lost": lost, "p": p}


def bootstrap(old_rows: np.ndarray, new_rows: np.ndarray, seed: int = 0) -> dict:
    """Resample test rows with replacement; 95% CI of the F1 gap in points."""
    rng = np.random.default_rng(seed)
    n = len(old_rows)
    gaps = np.empty(BOOT)
    for b in range(BOOT):
        idx = rng.integers(0, n, n)
        gaps[b] = f1(new_rows[idx]) - f1(old_rows[idx])
    lo, hi = np.percentile(gaps, [2.5, 97.5]) * 100
    return {"gap": (f1(new_rows) - f1(old_rows)) * 100, "lo": float(lo), "hi": float(hi)}


def cost(preds: dict[str, dict], rows: list[dict]) -> dict:
    recs = [preds[r["id"]] for r in rows if r["id"] in preds]
    have = [p for p in recs if "usage" in p and p.get("usage_complete", True)]
    if len(have) < len(recs):  # partial token data would misstate the average
        have = []
    tok = lambda k: sum((p.get("usage") or {}).get(k, 0) for p in have)  # noqa: E731
    return {
        "avg_seconds": sum(p["seconds"] for p in recs) / len(recs),
        "calls_per_row": tok("calls") / len(have) if have else None,
        "in_tok_per_row": tok("prompt_tokens") / len(have) if have else None,
        "out_tok_per_row": tok("completion_tokens") / len(have) if have else None,
    }


# ---------------------------------------------------------------- report

def pctm(x) -> str:
    return ev.pct(x).strip()


def fmt_p(p: float) -> str:
    return "<1e-10" if p < 1e-10 else (f"{p:.1e}" if p < 0.001 else f"{p:.3f}")


def verdict(m: dict, b: dict) -> str:
    mc = m["p"] < 0.05
    bs = b["lo"] > 0 or b["hi"] < 0
    if mc and bs:
        return "real gain" if b["gap"] > 0 else "real loss"
    if not mc and not bs:
        return "could be luck"
    return "tests disagree"


def report(prefix: str, entries: list[dict], rows: list[dict]) -> str:
    canonical = json.loads(ev.LABELS_PATH.read_text())["raw_to_canonical"]
    for e in entries:
        e["preds"] = load_preds(e["run"])
        e["out"] = outcomes(rows, e["preds"], canonical)
        e["rep"] = json.loads((ev.RUNS_DIR / e["run"] / "report.json").read_text())
        e["cost"] = cost(e["preds"], rows)
    base = next(e for e in entries if e["layer"] == "L1")

    lines = [f"# PII layer experiments: {prefix}", "",
             f"Test set: {len(rows)} rows, {len(base['out']['hit'])} gold entities "
             f"(sha256 {ev.SHA_PATH.read_text().split()[0][:12]}...). Temperature 0 everywhere.", "",
             "## Results", "",
             "| Layer | What changed | Labeled P | Labeled R | **Labeled F1** | Leak recall | Clean rows "
             "| Δ F1 vs L1 | Δ F1 vs prev | Sec/row | Calls/row | Tokens in/out per row |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    prev = None
    for e in entries:
        lab, rep, c = e["rep"]["labeled"]["micro"], e["rep"], e["cost"]
        d_base = (f1(e["out"]["rows"]) - f1(base["out"]["rows"])) * 100 if e is not base else None
        d_prev = (f1(e["out"]["rows"]) - f1(prev["out"]["rows"])) * 100 if prev else None
        toks = (f"{c['in_tok_per_row']:.0f} / {c['out_tok_per_row']:.0f}"
                if c["in_tok_per_row"] is not None else "n/a")
        calls = f"{c['calls_per_row']:.2f}" if c["calls_per_row"] is not None else "n/a"
        lines.append(
            f"| {e['layer']} | {e['desc']} | {pctm(lab['precision'])} | {pctm(lab['recall'])} "
            f"| **{pctm(lab['f1'])}** | {pctm(rep['recall_strict'])} | {pctm(rep['clean_rows'])} "
            f"| {'' if d_base is None else f'{d_base:+.1f}'} | {'' if d_prev is None else f'{d_prev:+.1f}'} "
            f"| {c['avg_seconds']:.1f} | {calls} | {toks} |")
        prev = e

    lines += ["", "## Is each step real?", "",
              "McNemar: per gold entity, found with the right label (pass) or not. Bootstrap: 5,000 "
              "resamples of the test rows, 95% CI of the labeled-F1 gap in points. A step counts as "
              "real only when both agree.", "",
              "| Comparison | Entities gained | Entities lost | McNemar p | F1 gap | 95% CI | Verdict |",
              "|---|---|---|---|---|---|---|"]
    stats = []
    for i, e in enumerate(entries):
        if e is base:
            continue
        if e["layer"] == "L0":
            pairs = [(e, base)]                       # what the model adds over regex alone
        else:
            pairs = [(base, e)]                       # cumulative gain over the baseline
            prev = entries[i - 1]
            if prev is not base and prev["layer"] != "L0":
                pairs.append((prev, e))               # what this one layer added
            by_layer = {x["layer"]: x for x in entries}
            pairs += [(by_layer[v], e) for v in e.get("vs", []) if v in by_layer]
        for old, new in pairs:
            m = mcnemar(old["out"]["hit"], new["out"]["hit"])
            b = bootstrap(old["out"]["rows"], new["out"]["rows"])
            v = verdict(m, b)
            stats.append({"old": old["layer"], "new": new["layer"], **m, **b, "verdict": v})
            lines.append(f"| {new['layer']} vs {old['layer']} | {m['gained']} | {m['lost']} | {fmt_p(m['p'])} "
                         f"| {b['gap']:+.1f} | [{b['lo']:+.1f}, {b['hi']:+.1f}] | {v} |")

    best = max((e for e in entries if e["layer"] != "L0"), key=lambda e: f1(e["out"]["rows"]))
    lines += ["", f"## Per-label F1: L1 baseline vs best ({best['layer']})", "",
              "| Label | n | L1 F1 | " + f"{best['layer']} F1 | Δ |", "|---|---|---|---|---|"]
    bl, bt = base["rep"]["labeled"]["per_label"], best["rep"]["labeled"]["per_label"]
    for label, s in sorted(best["rep"]["per_label"].items(), key=lambda kv: (bt.get(kv[0], {}).get("f1") or 0)):
        f_old, f_new = (bl.get(label, {}).get("f1") or 0), (bt.get(label, {}).get("f1") or 0)
        flag = " (low n)" if s["low_n"] else ""
        lines.append(f"| {label}{flag} | {s['n']} | {f_old:.1%} | {f_new:.1%} | {100 * (f_new - f_old):+.1f} |")

    md = "\n".join(lines) + "\n"
    out_dir = EXP_DIR / prefix
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.md").write_text(md)
    summary = [{"layer": e["layer"], "desc": e["desc"], "run": e["run"],
                "labeled": e["rep"]["labeled"]["micro"], "leak_recall": e["rep"]["recall_strict"],
                "clean_rows": e["rep"]["clean_rows"], "cost": e["cost"]} for e in entries]
    (out_dir / "results.json").write_text(json.dumps({"layers": summary, "stats": stats}, indent=2))
    return md


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", default="glm-5p3-flash")
    p.add_argument("--prefix", default=None, help="Run name prefix (default: the model id)")
    p.add_argument("--partner", default=None, help="Finished run to union with the last layer (reported as 'U')")
    p.add_argument("--extra", action="append", default=[],
                   help="Add a finished run as a layer: LAYER=RUN=DESCRIPTION[=VS], e.g. "
                        "'L6=lora-L1=LoRA fine-tune, plain prompt'. VS adds one more comparison.")
    p.add_argument("--layers", default=None, help="Comma list to run, e.g. L1,L2 (default: all)")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--report-only", action="store_true")
    args = p.parse_args()
    prefix = args.prefix or args.model.split("/")[-1]
    rows = ev.load_rows(args.limit)
    wanted = set(args.layers.split(",")) if args.layers else None

    entries = []
    for layer, desc, cfg in ladder(args.model):
        run = f"{prefix}-{layer}"
        if not args.report_only and (wanted is None or layer in wanted):
            hz.run(cfg, run, args.limit, args.workers)
        if (ev.RUNS_DIR / run / "report.json").exists():
            entries.append({"layer": layer, "desc": desc, "run": run})

    if args.partner and entries:
        last = entries[-1]
        run = f"{prefix}-union"
        union_run(run, last["run"], args.partner, rows)
        meta = {"run": run, "backend": "union", "gliner": "off", "system": "harness-union",
                "parts": [last["run"], args.partner]}
        ev.finish(ev.RUNS_DIR / run, rows, meta)
        entries.append({"layer": "U", "desc": f"+ union with {args.partner.replace('harness-', '')}", "run": run})

    for spec in args.extra:
        layer, run, desc, *vs = spec.split("=")
        if not (ev.RUNS_DIR / run / "report.json").exists():
            sys.exit(f"ERROR --extra: no report for run {run}")
        entries.append({"layer": layer, "desc": desc, "run": run, "vs": vs})

    print("\n" + report(prefix, entries, rows))
    print(f"Wrote {EXP_DIR / prefix / 'results.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
