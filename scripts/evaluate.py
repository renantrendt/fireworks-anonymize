#!/usr/bin/env python3
"""Score the anonymizer on the frozen test set (data/test_set.jsonl).

Each row's text goes through the same detection steps that
anonymize.collect() runs on one file, then through replace_tokens(). The
score asks one question per gold entity: is it still readable in the
anonymized text?

Usage:
  python scripts/evaluate.py --run baseline                # full run
  python scripts/evaluate.py --run smoke --limit 20        # quick check
  python scripts/evaluate.py --run rules --no-llm --no-gliner
  python scripts/evaluate.py --run baseline --rescore      # re-score saved predictions

Backend: FIREWORKS_API_KEY set -> Fireworks; otherwise local Ollama
(see anonymize.llm_generate). Predictions are saved row by row to
data/runs/<run>/predictions.jsonl, so an interrupted run resumes where it
stopped. The report goes to data/runs/<run>/report.json.

Metrics (per gold entity, then grouped by label):
  recall_strict   every space-separated word of the value is gone from the
                  output (words a fake brought in don't count).
                  "John Smith" -> "Paul Smith" counts as a leak.
  recall_exact    the full value string is gone from the output (lenient).
  precision       share of replaced tokens that overlap a gold value.
  clean_rows      share of rows where nothing leaked (strict).
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import io
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
TEST_PATH = DATA_DIR / "test_set.jsonl"
SHA_PATH = DATA_DIR / "test_set.sha256"
LABELS_PATH = DATA_DIR / "labels.json"
RUNS_DIR = DATA_DIR / "runs"
LOW_N = 10            # labels with fewer gold instances are flagged as unreliable
MAX_CONSECUTIVE_ERRORS = 5


# ---------------------------------------------------------------- setup

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run", required=True, help="Run name; output goes to data/runs/<run>/")
    p.add_argument("--limit", type=int, default=None, help="Only the first N rows")
    p.add_argument("--no-llm", action="store_true", help="Skip the LLM step (rules, plus GLiNER if on)")
    p.add_argument("--no-gliner", action="store_true", help="Run without GLiNER (sets OFFLINE_ANON_GLINER=off)")
    p.add_argument("--rescore", action="store_true", help="Only re-score existing predictions")
    p.add_argument("--fresh", action="store_true", help="Delete existing predictions for this run first")
    return p.parse_args()


def check_frozen() -> None:
    expected = SHA_PATH.read_text().split()[0]
    actual = hashlib.sha256(TEST_PATH.read_bytes()).hexdigest()
    if actual != expected:
        sys.exit(f"ERROR test_set.jsonl hash {actual} does not match test_set.sha256 {expected}")


def load_rows(limit: int | None) -> list[dict]:
    with TEST_PATH.open() as f:
        rows = [json.loads(line) for line in f if line.strip()]
    return rows[:limit] if limit else rows


def import_anonymizer(no_gliner: bool):
    if no_gliner:
        os.environ["OFFLINE_ANON_GLINER"] = "off"
    sys.path.insert(0, str(ROOT / "scripts"))
    import anonymize as anon
    # Folder mode: an empty LLM answer is a normal outcome (51 rows have no
    # PII), not a reason to exit the process.
    anon.FOLDER_MODE = True
    return anon


# ---------------------------------------------------------------- detection

def snapshot_state(anon) -> dict:
    """Module-level containers as they are right after import. Restored
    before every row so one row's findings never change another's."""
    return {
        name: copy.deepcopy(value)
        for name, value in vars(anon).items()
        if name.isupper() and not name.startswith("_") and isinstance(value, (set, dict, list))
    }


def restore_state(anon, snap: dict) -> None:
    for name, value in snap.items():
        current = getattr(anon, name)
        current.clear()
        if isinstance(current, list):
            current.extend(copy.deepcopy(value))
        else:
            current.update(copy.deepcopy(value))


def detect(anon, text: str, use_llm: bool) -> tuple[dict, dict]:
    """anonymize.collect_hits + collect, for plain text instead of a file."""
    hits: dict = {}
    lines = [line.split() for line in text.splitlines() if line.strip()]
    anon.scan_text_blobs(text, hits)
    before_rules = set(hits)
    anon.scan_word_lines(lines, hits)
    rule_tokens = set(hits) - before_rules
    if use_llm:
        anon.add_local_ids(hits, text)
    if anon.gliner_enabled():
        anon.add_gliner_ids(hits, text)
        anon.reject_rule_only(hits, rule_tokens, text)
        anon.retype_company_words(hits)
    anon.add_phrase_words(hits)
    anon.add_variants(hits)
    anon.drop_ordinary_words(hits, [text], [])
    anon.merge_possessives(hits)
    anon.learn_genders([text])
    anon.drop_public_terms(hits, [text])
    return anon.build_map(hits)


def predict(args, rows: list[dict], pred_path: Path) -> None:
    anon = import_anonymizer(args.no_gliner)
    snap = snapshot_state(anon)
    done = set()
    if pred_path.exists():
        with pred_path.open() as f:
            done = {json.loads(line)["id"] for line in f if line.strip()}
    todo = [r for r in rows if r["id"] not in done]
    backend = "none" if args.no_llm else anon.llm_backend_name()
    gliner = anon.GLINER_MODEL if anon.gliner_enabled() else "off"
    print(f"backend={backend}  gliner={gliner}  rows: {len(done)} done, {len(todo)} to go")

    consecutive_errors = 0
    with pred_path.open("a") as out:
        for i, row in enumerate(todo, 1):
            restore_state(anon, snap)
            started = time.time()
            log = io.StringIO()
            record = {"id": row["id"], "backend": backend, "gliner": gliner}
            try:
                with contextlib.redirect_stdout(log):
                    mapping, kinds = detect(anon, row["source_text"], not args.no_llm)
                record.update(
                    output_text=anon.replace_tokens(row["source_text"], mapping),
                    replaced={real: kinds.get(real, "") for real in mapping},
                    fakes=sorted(set(mapping.values())),
                    error=None,
                )
                consecutive_errors = 0
            except (Exception, SystemExit) as exc:
                record.update(output_text=None, replaced={}, error=f"{type(exc).__name__}: {exc}")
                consecutive_errors += 1
            record["seconds"] = round(time.time() - started, 2)
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            out.flush()
            status = "ERROR " + record["error"] if record["error"] else f"{len(record['replaced'])} replaced"
            print(f"[{len(done) + i}/{len(rows)}] {row['id']}  {record['seconds']}s  {status}")
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                sys.exit(f"ERROR {MAX_CONSECUTIVE_ERRORS} rows failed in a row; is the backend up? "
                         "Rerun the same command to resume.")


# ---------------------------------------------------------------- scoring

def present(value: str, text: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(value) + r"(?!\w)", text) is not None


def value_words(value: str) -> list[str]:
    """Whitespace-separated parts of a value. A value with no spaces (email,
    IP, ID) is one part, so strict equals exact for it: "gmail" left in a
    fake address is not a leak."""
    words = [w.strip(".,;:()[]'\"") for w in value.split()]
    words = [w for w in words if len(w) >= 2]
    return words or [value]


def word_leaked(word: str, out: str, fake_words: set[str]) -> bool:
    """A word still in the output leaks, unless a fake brought it in
    ("Street" in a fake "12 Elm Street")."""
    return present(word, out) and word.lower() not in fake_words


def overlaps(token: str, value: str) -> bool:
    t, v = token.lower(), value.lower()
    return t in v or v in t


def prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f = 2 * p * r / (p + r) if p and r else (0.0 if p is not None and r is not None else None)
    return {"precision": p, "recall": r, "f1": f, "tp": tp, "fp": fp, "fn": fn}


def label_scores(rows: list[dict], preds: dict[str, dict], canonical: dict[str, str]) -> dict | None:
    """Exact-value matching of (value, label) pairs, for systems that return
    labeled entities (harness.py). `span` ignores the label."""
    if not any("entities" in p for p in preds.values()):
        return None
    per_label = defaultdict(lambda: [0, 0, 0])
    span = [0, 0, 0]
    for row in rows:
        pred = preds.get(row["id"])
        if pred is None:
            continue
        gold = Counter((e["value"].strip(), canonical.get(e["label"], e["label"])) for e in row["entities"])
        # A failed call found nothing: its gold entities count as misses.
        got = Counter() if pred["error"] else Counter((e["value"].strip(), e["label"]) for e in pred.get("entities", []))
        for (value, label), n in (gold | got).items():
            tp = min(gold[(value, label)], got[(value, label)])
            per_label[label][0] += tp
            per_label[label][1] += got[(value, label)] - tp
            per_label[label][2] += gold[(value, label)] - tp
        gold_v = Counter(v for v, _ in gold.elements())
        got_v = Counter(v for v, _ in got.elements())
        tp = sum((gold_v & got_v).values())
        span[0] += tp
        span[1] += sum(got_v.values()) - tp
        span[2] += sum(gold_v.values()) - tp
    total = [sum(x[i] for x in per_label.values()) for i in range(3)]
    return {
        "micro": prf(*total),
        "span": prf(*span),
        "per_label": {label: prf(*v) for label, v in sorted(per_label.items())},
    }


def score(rows: list[dict], preds: dict[str, dict], canonical: dict[str, str]) -> dict:
    per_label = defaultdict(lambda: {"n": 0, "strict": 0, "exact": 0})
    tp_tokens = fp_tokens = 0
    clean_rows = scored_rows = errors = 0
    zero_pii_rows = zero_pii_touched = 0
    leaks = []

    for row in rows:
        pred = preds.get(row["id"])
        if pred is None:
            continue
        # A failed call masks nothing: score it as the unmasked text, so
        # failures count against the system instead of being skipped.
        errors += bool(pred["error"])
        scored_rows += 1
        out = row["source_text"] if pred["error"] else pred["output_text"]
        fake_words = {w.lower() for fake in pred.get("fakes", []) for w in re.findall(r"\w+", fake)}
        row_clean = True
        for ent in row["entities"]:
            label = canonical.get(ent["label"], ent["label"])
            value = ent["value"]
            strict_ok = not any(word_leaked(w, out, fake_words) for w in value_words(value))
            exact_ok = not present(value, out)
            stats = per_label[label]
            stats["n"] += 1
            stats["strict"] += strict_ok
            stats["exact"] += exact_ok
            if not strict_ok:
                row_clean = False
                leaks.append({"id": row["id"], "label": label, "value": value})
        clean_rows += row_clean

        values = [e["value"] for e in row["entities"]]
        for token in pred["replaced"]:
            if any(overlaps(token, v) for v in values):
                tp_tokens += 1
            else:
                fp_tokens += 1
        if not values:
            zero_pii_rows += 1
            zero_pii_touched += bool(pred["replaced"])

    n = sum(s["n"] for s in per_label.values())
    strict = sum(s["strict"] for s in per_label.values())
    exact = sum(s["exact"] for s in per_label.values())
    replaced = tp_tokens + fp_tokens
    return {
        "rows_scored": scored_rows,
        "rows_errored": errors,
        "entities": n,
        "recall_strict": strict / n if n else None,
        "recall_exact": exact / n if n else None,
        "precision": tp_tokens / replaced if replaced else None,
        "replaced_tokens": replaced,
        "false_positive_tokens": fp_tokens,
        "clean_rows": clean_rows / scored_rows if scored_rows else None,
        "zero_pii_rows": zero_pii_rows,
        "zero_pii_rows_touched": zero_pii_touched,
        "per_label": {
            label: {
                "n": s["n"],
                "recall_strict": s["strict"] / s["n"],
                "recall_exact": s["exact"] / s["n"],
                "low_n": s["n"] < LOW_N,
            }
            for label, s in sorted(per_label.items())
        },
        "labeled": label_scores(rows, preds, canonical),
        "leaks": leaks,
    }


def pct(x: float | None) -> str:
    return "  n/a" if x is None else f"{100 * x:5.1f}%"


def print_report(rep: dict, meta: dict) -> None:
    print(f"\nRun: {meta['run']}   backend: {meta['backend']}   gliner: {meta['gliner']}")
    print(f"Rows scored: {rep['rows_scored']}   errored: {rep['rows_errored']}   entities: {rep['entities']}")
    print(f"  recall (strict)  {pct(rep['recall_strict'])}   every word of the value is gone")
    print(f"  recall (exact)   {pct(rep['recall_exact'])}   the full value string is gone")
    print(f"  precision        {pct(rep['precision'])}   {rep['false_positive_tokens']} of "
          f"{rep['replaced_tokens']} replaced tokens are not PII")
    print(f"  clean rows       {pct(rep['clean_rows'])}   rows with zero leaks")
    print(f"  zero-PII rows touched: {rep['zero_pii_rows_touched']} of {rep['zero_pii_rows']}")
    lab = rep.get("labeled")
    if lab:
        m, s = lab["micro"], lab["span"]
        print(f"  labeled P/R/F1   {pct(m['precision'])} {pct(m['recall'])} {pct(m['f1'])}   "
              "exact value + correct label")
        print(f"  span P/R/F1      {pct(s['precision'])} {pct(s['recall'])} {pct(s['f1'])}   "
              "exact value, any label")
    header = f"\n{'Label':<15}{'n':>6}{'strict':>9}{'exact':>9}"
    if lab:
        header += f"{'P':>9}{'R':>9}{'F1':>9}"
    print(header)
    print("-" * (66 if lab else 39))
    for label, s in sorted(rep["per_label"].items(), key=lambda kv: kv[1]["recall_strict"]):
        line = f"{label:<15}{s['n']:>6}{pct(s['recall_strict']):>9}{pct(s['recall_exact']):>9}"
        if lab:
            l = lab["per_label"].get(label, {})
            line += f"{pct(l.get('precision')):>9}{pct(l.get('recall')):>9}{pct(l.get('f1')):>9}"
        print(line + ("  (low n)" if s["low_n"] else ""))


def finish(run_dir: Path, rows: list[dict], meta: dict) -> dict:
    """Score data/runs/<run>/predictions.jsonl, write report.json, print."""
    canonical = json.loads(LABELS_PATH.read_text())["raw_to_canonical"]
    with (run_dir / "predictions.jsonl").open() as f:
        preds = {p["id"]: p for p in map(json.loads, f) if p}
    rep = score(rows, preds, canonical)
    (run_dir / "report.json").write_text(json.dumps({"meta": meta, **rep}, indent=2, ensure_ascii=False))
    print_report(rep, meta)
    print(f"\nWrote {run_dir / 'report.json'}  ({len(rep['leaks'])} leaks listed there)")
    return rep


# ---------------------------------------------------------------- main

def main() -> int:
    args = parse_args()
    check_frozen()
    rows = load_rows(args.limit)
    run_dir = RUNS_DIR / args.run
    run_dir.mkdir(parents=True, exist_ok=True)
    pred_path = run_dir / "predictions.jsonl"
    if args.fresh and pred_path.exists():
        pred_path.unlink()

    if not args.rescore:
        predict(args, rows, pred_path)
    if not pred_path.exists():
        sys.exit(f"ERROR no predictions at {pred_path}")

    with pred_path.open() as f:
        first = json.loads(f.readline())
    meta = {"run": args.run, "backend": first["backend"], "gliner": first.get("gliner", "off"),
            "limit": args.limit, "test_sha256": SHA_PATH.read_text().split()[0]}
    finish(run_dir, rows, meta)
    return 0


if __name__ == "__main__":
    sys.exit(main())
