"""Offline tests for the model-evaluation helpers. No Fireworks calls."""

import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "eval_models", Path(__file__).resolve().parent.parent / "scripts" / "eval_models.py")
ev = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ev)

LABELS = {"GIVENNAME", "LASTNAME", "EMAIL", "IDCARD"}
TEXT = "Tiago Kanakas holds id 511975305747, mail tiakanakas@example.com."


def test_parse_plain_json_array():
    raw = '[{"value": "Tiago", "label": "GIVENNAME"}, {"value": "Kanakas", "label": "LASTNAME"}]'
    got = ev.parse_model_json(raw, LABELS, TEXT)
    assert got == [{"value": "Tiago", "label": "GIVENNAME"},
                   {"value": "Kanakas", "label": "LASTNAME"}]


def test_parse_strips_think_tags_and_markdown():
    raw = ('<think>reasoning</think>```json\n'
           '[{"value": "Tiago", "label": "givenname1"}]\n```')
    got = ev.parse_model_json(raw, LABELS, TEXT)
    assert got == [{"value": "Tiago", "label": "GIVENNAME"}]  # case + numbered suffix fixed


def test_parse_drops_unknown_labels_and_invented_values():
    raw = ('[{"value": "Tiago", "label": "NONSENSE"},'
           ' {"value": "Not In Text", "label": "GIVENNAME"},'
           ' {"value": "Tiago", "label": "GIVENNAME"},'
           ' {"value": "Tiago", "label": "GIVENNAME"}]')
    got = ev.parse_model_json(raw, LABELS, TEXT)
    assert got == [{"value": "Tiago", "label": "GIVENNAME"}]  # deduped too


def test_parse_garbage_returns_empty():
    assert ev.parse_model_json("no json here", LABELS, TEXT) == []
    assert ev.parse_model_json("", LABELS, TEXT) == []


def test_score_strict_and_value_only():
    gold = [[{"value": "Tiago", "label": "GIVENNAME"},
             {"value": "511975305747", "label": "IDCARD"}]]
    pred = [[{"value": "Tiago", "label": "GIVENNAME"},          # strict hit
             {"value": "511975305747", "label": "PASSPORT"},    # value hit, label miss
             {"value": "tiakanakas@example.com", "label": "EMAIL"}]]  # false positive
    r = ev.score_model(gold, pred, failed=0)
    assert r["strict"]["precision"] == round(1 / 3, 4)
    assert r["strict"]["recall"] == 0.5
    assert r["value_only"]["precision"] == round(2 / 3, 4)
    assert r["value_only"]["recall"] == 1.0
    assert r["per_label"]["GIVENNAME"]["f1"] == 1.0
    assert r["per_label"]["IDCARD"]["recall"] == 0.0


def test_score_zero_pii_row_perfect_when_model_returns_empty():
    r = ev.score_model([[]], [[]], failed=0)
    assert r["strict"]["f1"] == 0.0  # no tp: scores are 0, but no fp/fn either
    assert r["rows"] == 1
