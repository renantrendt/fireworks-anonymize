#!/usr/bin/env python3
"""Distill step: a teacher model answers each training text; keep only the
answers that match the dataset's labels exactly (same values, nothing extra).

Usage: build_teacher_set.py <teacher model>
Reads data/train_gold.jsonl, writes data/train_teacher.jsonl with the
teacher's own answer text as the assistant turn.
"""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from score_fireworks import ask, norm, parse  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data"


def main() -> int:
    model = sys.argv[1]
    rows = [json.loads(l) for l in (DATA / "train_gold.jsonl").open()]
    with ThreadPoolExecutor(8) as pool:
        answers = list(pool.map(lambda r: ask(model, r["messages"][1]["content"]), rows))
    kept = 0
    with (DATA / "train_teacher.jsonl").open("w", encoding="utf-8") as fh:
        for r, a in zip(rows, answers):
            got = parse(a)
            gold = {norm(e["value"]) for e in json.loads(r["messages"][2]["content"])}
            if got is None or {norm(str(e["value"])) for e in got} != gold:
                continue
            msgs = r["messages"][:2] + [{"role": "assistant",
                                         "content": json.dumps(got, ensure_ascii=False)}]
            fh.write(json.dumps({"messages": msgs}, ensure_ascii=False) + "\n")
            kept += 1
    print(json.dumps({"teacher": model, "rows": len(rows), "kept": kept}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
