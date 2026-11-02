#!/usr/bin/env python3
"""Score a Fireworks model on the frozen 500-row test set.

Usage: score_fireworks.py <model> <out.jsonl> [--limit N]
Needs FIREWORKS_API_KEY. The system prompt is identical on every call, so
Fireworks' automatic prompt caching applies to it.

A value counts as found when the model lists the same text (case and
surrounding punctuation ignored); labels are reported but not required.
Per row: pass = every gold value found and no extra value listed.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pii_prompt import PROMPT  # noqa: E402

URL = "https://api.fireworks.ai/inference/v1/chat/completions"
DATA = Path(__file__).resolve().parent.parent / "data" / "test_set.jsonl"


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip(" .,;:\"'").lower())


SHOTS: list[dict] = []  # few-shot turns from the train split, set by --fewshot


def ask(model: str, text: str) -> str:
    body = json.dumps({"model": model, "temperature": 0, "max_tokens": 4096,
                       "messages": [{"role": "system", "content": PROMPT}, *SHOTS,
                                    {"role": "user", "content": text}]}).encode()
    for attempt in range(5):
        try:
            req = urllib.request.Request(URL, body, {"Content-Type": "application/json",
                                                     "Authorization": "Bearer " + os.environ["FIREWORKS_API_KEY"]})
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.load(r)["choices"][0]["message"]["content"] or ""
        except Exception as e:  # rate limits and timeouts: back off and retry
            if attempt == 4:
                return f"ERROR: {e}"
            time.sleep(5 * (attempt + 1))
    return ""


def parse(answer: str) -> list[dict] | None:
    answer = re.sub(r"(?s)<think>.*?</think>", "", answer)
    m = re.search(r"(?s)\[.*\]", answer)
    try:
        items = json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        return None
    return [i for i in items if isinstance(i, dict) and "value" in i] if isinstance(items, list) else None


def main() -> int:
    model, out = sys.argv[1], Path(sys.argv[2])
    rows = [json.loads(l) for l in DATA.open()]
    if "--limit" in sys.argv:
        rows = rows[: int(sys.argv[sys.argv.index("--limit") + 1])]
    if "--fewshot" in sys.argv:
        k = int(sys.argv[sys.argv.index("--fewshot") + 1])
        for l in list((DATA.parent / "train_gold.jsonl").open())[:k]:
            SHOTS.extend(json.loads(l)["messages"][1:])

    def timed(r):
        t0 = time.time()
        return ask(model, r["source_text"]), time.time() - t0

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(timed, rows))
    answers, secs = [a for a, _ in results], sorted(s for _, s in results)
    tp = fn = fp = passed = bad = 0
    with out.open("w") as fh:
        for r, a in zip(rows, answers):
            got = parse(a)
            bad += got is None
            gold = {norm(e["value"]) for e in r["entities"]}
            pred = {norm(str(e["value"])) for e in (got or [])}
            t, n, p = len(gold & pred), len(gold - pred), len(pred - gold)
            tp, fn, fp = tp + t, fn + n, fp + p
            ok = n == 0 and p == 0 and got is not None
            passed += ok
            fh.write(json.dumps({"id": r["id"], "pass": ok, "tp": t, "fn": n, "fp": p, "raw": a}) + "\n")
    rec, prec = tp / max(tp + fn, 1), tp / max(tp + fp, 1)
    f1 = 2 * rec * prec / max(rec + prec, 1e-9)
    print(json.dumps({"model": model, "rows": len(rows), "row_pass": round(passed / len(rows), 3),
                      "recall": round(rec, 3), "precision": round(prec, 3), "f1": round(f1, 3),
                      "unparsed": bad,
                      "p50_s": round(secs[len(secs) // 2], 2), "p95_s": round(secs[int(len(secs) * 0.95)], 2)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
