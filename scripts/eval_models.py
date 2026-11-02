#!/usr/bin/env python3
"""Evaluate how well different Fireworks AI models identify PII.

Reads the frozen test set (data/test_set.jsonl), asks each model to extract
PII entities as JSON, and scores the answer against the gold entities.

Two scores per model:
  strict  -- (value, label) must both match a gold entity.
  value   -- the value matches a gold entity, label ignored. This is what
             matters for anonymization: the PII was found, even if mislabeled.

Usage:
  export FIREWORKS_API_KEY=fw_...
  python scripts/eval_models.py --models \
      accounts/fireworks/models/qwen3-235b-a22b,\
      accounts/fireworks/models/llama-v3p3-70b-instruct
  python scripts/eval_models.py --limit 20          # quick smoke run
  python scripts/eval_models.py --concurrency 16   # more rows in flight

Rows are sent to a model in parallel (--concurrency, default 8).

Results are written to results/eval-<timestamp>.json; raw predictions to
results/preds-<model>-<timestamp>.jsonl (one line per row, written as the
run progresses, so an interrupted run keeps what it finished).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "test_set.jsonl"
HASH_FILE = ROOT / "data" / "test_set.sha256"
LABELS_FILE = ROOT / "data" / "labels.json"
RESULTS_DIR = ROOT / "results"

FIREWORKS_API_KEY = os.environ.get("FIREWORKS_API_KEY") or ""
FIREWORKS_URL = os.environ.get("FIREWORKS_URL") or "https://api.fireworks.ai/inference/v1/chat/completions"

DEFAULT_MODELS = [
    "accounts/fireworks/models/qwen3p8-max",
    "accounts/fireworks/models/gpt-oss-120b",
    "accounts/fireworks/models/glm-5p3-flash",
]


def load_labels() -> list[str]:
    return json.loads(LABELS_FILE.read_text(encoding="utf-8"))["canonical"]


def load_rows() -> list[dict]:
    rows = []
    with DATA_FILE.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def check_frozen_hash() -> str | None:
    """A warning string when the test set no longer matches its frozen hash."""
    if not HASH_FILE.exists():
        return None
    want = HASH_FILE.read_text().strip()
    got = hashlib.sha256(DATA_FILE.read_bytes()).hexdigest()
    if want != got:
        return f"WARNING: {DATA_FILE.name} no longer matches its frozen SHA-256"
    return None


def build_prompt(text: str, labels: list[str]) -> str:
    return (
        "You are a PII extractor. Read the document below and list every piece "
        "of personally identifiable information in it.\n"
        "Return a JSON array only. Each item is "
        '{"value": "exact substring copied from the document", "label": "LABEL"} '
        "where LABEL is one of: " + ", ".join(labels) + ".\n"
        "Copy each value exactly as written, including punctuation and case. "
        "List every occurrence of every person, company identifier, number, "
        "address, date of birth, and contact detail. "
        "Do not invent values. Do not explain. Do not wrap in markdown. "
        "If there is no PII, return [].\n\n"
        "DOCUMENT:\n" + text
    )


def call_fireworks(model: str, prompt: str, max_tokens: int, timeout: int) -> str:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": max_tokens,
    }).encode("utf-8")
    req = urllib.request.Request(
        FIREWORKS_URL,
        data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + FIREWORKS_API_KEY},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"HTTP {exc.code} from Fireworks: {detail}") from None
    return (payload.get("choices") or [{}])[0].get("message", {}).get("content") or ""


def available_models(timeout: int = 60) -> list[str]:
    """Every model id this API key may call."""
    req = urllib.request.Request(
        FIREWORKS_URL.rsplit("/chat/completions", 1)[0] + "/models",
        headers={"Authorization": "Bearer " + FIREWORKS_API_KEY},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return sorted(m.get("id", "") for m in payload.get("data", []) if m.get("id"))


def parse_model_json(raw: str, labels: set[str], text: str) -> list[dict]:
    """The model's answer as [{"value", "label"}]. Anything that is not a
    (known label, value really in the document) pair is dropped."""
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.S).strip()
    start, end = raw.find("["), raw.rfind("]")
    if start < 0 or end < start:
        return []
    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return []
    found = []
    seen = set()
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        value = str(item.get("value") or "").strip(" .,;:")
        label = str(item.get("label") or "").upper().strip()
        label = re.sub(r"\d+$", "", label)  # LASTNAME1 -> LASTNAME
        if len(value) < 1 or label not in labels or value not in text:
            continue
        if (value, label) not in seen:
            seen.add((value, label))
            found.append({"value": value, "label": label})
    return found


def norm(value: str) -> str:
    return value.strip(" .,;:").casefold()


def score_model(rows_gold: list[list[dict]], rows_pred: list[list[dict]],
                failed: int) -> dict:
    """Micro precision/recall/F1 across all rows, strict and value-only,
    plus per-label strict scores."""
    tp_s = fp_s = fn_s = 0
    tp_v = fp_v = fn_v = 0
    per_label: dict[str, Counter] = defaultdict(Counter)
    for gold, pred in zip(rows_gold, rows_pred):
        g_pairs = {(norm(e["value"]), e["label"]) for e in gold}
        p_pairs = {(norm(e["value"]), e["label"]) for e in pred}
        g_vals = {v for v, _ in g_pairs}
        p_vals = {v for v, _ in p_pairs}
        tp_s += len(g_pairs & p_pairs)
        fp_s += len(p_pairs - g_pairs)
        fn_s += len(g_pairs - p_pairs)
        tp_v += len(g_vals & p_vals)
        fp_v += len(p_vals - g_vals)
        fn_v += len(g_vals - p_vals)
        for lab in {lab for _, lab in g_pairs | p_pairs}:
            g_lab = {v for v, l in g_pairs if l == lab}
            p_lab = {v for v, l in p_pairs if l == lab}
            per_label[lab]["tp"] += len(g_lab & p_lab)
            per_label[lab]["fp"] += len(p_lab - g_lab)
            per_label[lab]["fn"] += len(g_lab - p_lab)

    def prf(tp, fp, fn):
        p = tp / (tp + fp) if tp + fp else 0.0
        r = tp / (tp + fn) if tp + fn else 0.0
        f = 2 * p * r / (p + r) if p + r else 0.0
        return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f, 4)}

    return {
        "strict": prf(tp_s, fp_s, fn_s),
        "value_only": prf(tp_v, fp_v, fn_v),
        "rows": len(rows_gold),
        "rows_failed": failed,
        "per_label": {lab: prf(c["tp"], c["fp"], c["fn"]) | {"gold": c["tp"] + c["fn"]}
                      for lab, c in sorted(per_label.items())},
    }


def extract_one(model: str, row: dict, labels: list[str],
                max_tokens: int, timeout: int) -> tuple[str, list[dict], bool]:
    """One row through one model. Returns (raw answer, parsed entities, failed)."""
    prompt = build_prompt(row["source_text"], labels)
    try:
        raw = call_fireworks(model, prompt, max_tokens, timeout)
        return raw, parse_model_json(raw, set(labels), row["source_text"]), False
    except Exception as exc:
        return f"ERROR: {exc}", [], True


def eval_model(model: str, rows: list[dict], labels: list[str],
               max_tokens: int, timeout: int, stamp: str, concurrency: int) -> dict:
    short = model.rsplit("/", 1)[-1]
    preds_path = RESULTS_DIR / f"preds-{short}-{stamp}.jsonl"
    by_index: dict[int, tuple[str, list[dict], bool]] = {}
    failed = 0
    started = time.time()
    with preds_path.open("w", encoding="utf-8") as out, \
            ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(extract_one, model, row, labels, max_tokens, timeout): i
                   for i, row in enumerate(rows)}
        for done, fut in enumerate(as_completed(futures), 1):
            i = futures[fut]
            raw, pred, row_failed = fut.result()
            by_index[i] = (raw, pred, row_failed)
            failed += row_failed
            out.write(json.dumps({"id": rows[i]["id"], "gold": rows[i]["entities"],
                                  "pred": pred, "raw": raw},
                                 ensure_ascii=False) + "\n")
            out.flush()
            print(f"\r  {short}: {done}/{len(rows)} rows", end="", flush=True)
    print()
    rows_gold = [row["entities"] for row in rows]
    rows_pred = [by_index[i][1] for i in range(len(rows))]
    result = score_model(rows_gold, rows_pred, failed)
    result["seconds"] = round(time.time() - started, 1)
    result["predictions"] = str(preds_path.relative_to(ROOT))
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS),
                    help="comma-separated Fireworks model ids")
    ap.add_argument("--limit", type=int, default=0, help="only the first N rows")
    ap.add_argument("--max-tokens", type=int, default=4096)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--concurrency", type=int, default=8,
                    help="rows sent to a model at the same time")
    ap.add_argument("--by-label", action="store_true",
                    help="also print the per-label strict F1 table")
    ap.add_argument("--list-models", action="store_true",
                    help="print the model ids this API key may call, then exit")
    args = ap.parse_args()

    if not FIREWORKS_API_KEY:
        print("ERROR: set FIREWORKS_API_KEY first.", file=sys.stderr)
        return 1

    if args.list_models:
        for mid in available_models():
            print(mid)
        return 0

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    valid = set(available_models())
    bad = [m for m in models if m not in valid]
    if bad:
        print("ERROR: these model ids are not available to your API key:", file=sys.stderr)
        for m in bad:
            print(f"  {m}", file=sys.stderr)
        print("Run with --list-models to see what you can call.", file=sys.stderr)
        return 1

    warning = check_frozen_hash()
    if warning:
        print(warning, file=sys.stderr)

    labels = load_labels()
    rows = load_rows()
    if args.limit:
        rows = rows[:args.limit]
    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    print(f"Test set: {len(rows)} rows | labels: {len(labels)} | models: {len(models)}")
    results = {}
    for model in models:
        print(f"Evaluating {model} …")
        results[model] = eval_model(model, rows, labels, args.max_tokens,
                                    args.timeout, stamp, args.concurrency)

    out_path = RESULTS_DIR / f"eval-{stamp}.json"
    out_path.write_text(json.dumps({
        "created_utc": stamp,
        "rows": len(rows),
        "models": results,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    width = max(len(m.rsplit("/", 1)[-1]) for m in models)
    print(f"\n{'model':<{width}}  {'P':>6} {'R':>6} {'F1':>6}  "
          f"{'val-F1':>6}  {'fail':>4}  {'sec':>6}")
    print("-" * (width + 46))
    for model, r in results.items():
        s, v = r["strict"], r["value_only"]
        print(f"{model.rsplit('/', 1)[-1]:<{width}}  "
              f"{s['precision']:>6.3f} {s['recall']:>6.3f} {s['f1']:>6.3f}  "
              f"{v['f1']:>6.3f}  {r['rows_failed']:>4}  {r['seconds']:>6.0f}")

    if args.by_label:
        all_labels = sorted({lab for r in results.values() for lab in r["per_label"]})
        print(f"\nPer-label strict F1 (gold count):")
        print(f"{'label':<14}" + "".join(f"{m.rsplit('/', 1)[-1][:12]:>14}" for m in models))
        for lab in all_labels:
            cells = []
            for m in models:
                e = results[m]["per_label"].get(lab)
                cells.append(f"{e['f1']:.3f} ({e['gold']:>3})" if e else "-")
            print(f"{lab:<14}" + "".join(f"{c:>14}" for c in cells))

    print(f"\nWrote {out_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
