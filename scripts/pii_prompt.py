"""The one prompt used for training and scoring on the English PII test set."""

import json
from pathlib import Path

LABELS = json.loads((Path(__file__).resolve().parent.parent / "data" / "labels.json").read_text())["canonical"]

PROMPT = (
    "List every piece of personal information in the user's text. "
    "Answer with a JSON array only, no other text. Each item is "
    '{"value": <the exact text as written>, "label": <one label>}. '
    "Labels: " + ", ".join(LABELS) + ". "
    "List each value once, in the order it appears. If there is none, answer []."
)


def answer_json(entities: list[dict]) -> str:
    return json.dumps([{"value": e["value"], "label": e["label"]} for e in entities], ensure_ascii=False)
