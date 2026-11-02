#!/usr/bin/env python3
"""Build the LoRA training set for Fireworks from ai4privacy/pii-masking-300k.

Outputs (in data/):
  data/train_gold.jsonl   -- N chat examples {"messages": [system, user, assistant]}
                             with the dataset's own labels as the answer

Rules:
  - English rows from the train split only. The frozen test set comes from
    the validation split, so no test row can appear here.
  - Same label taxonomy as data/labels.json (numbered variants merged).
  - The system prompt is PROMPT in pii_prompt.py, shared with the scorer, so
    training and scoring ask the same question.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pii_prompt import PROMPT, answer_json  # noqa: E402

SEED = 42
N = 1000
DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def main() -> int:
    from datasets import load_dataset

    labels = json.loads((DATA_DIR / "labels.json").read_text())["raw_to_canonical"]
    ds = load_dataset("ai4privacy/pii-masking-300k", split="train")
    rows = [r for r in ds if r.get("language") == "English"]
    print(f"train English: {len(rows):,}")
    random.Random(SEED).shuffle(rows)

    out = DATA_DIR / "train_gold.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        for r in rows[:N]:
            ents, seen = [], set()
            for e in r["privacy_mask"]:
                v, lab = e["value"].strip(" .,;:"), labels.get(e["label"], e["label"])
                if (v, lab) not in seen:
                    seen.add((v, lab))
                    ents.append({"value": v, "label": lab})
            fh.write(json.dumps({"messages": [
                {"role": "system", "content": PROMPT},
                {"role": "user", "content": r["source_text"]},
                {"role": "assistant", "content": answer_json(ents)},
            ]}, ensure_ascii=False) + "\n")
    print(f"wrote {out} ({N} examples)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
