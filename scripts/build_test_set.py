#!/usr/bin/env python3
"""Build the frozen 500-row English test set from ai4privacy/pii-masking-300k.

Outputs (written to data/ next to this script's folder):
  data/labels.json       -- canonical label taxonomy, numbered variants merged
  data/test_set.jsonl    -- 500 frozen rows  {"id", "source_text", "entities"}
  data/test_set.sha256   -- SHA-256 of test_set.jsonl (commit this before any model run)

Sampling rules (from the problem statement):
  - English rows only.
  - Drawn from the validation split only (no train contamination).
  - No base document id appears in both train and test (chunk ids like 40767A/B).
  - Natural share of zero-PII rows preserved so over-redaction is measurable.
  - Every label has >= MIN_PER_LABEL gold instances in the final 500.
  - Deterministic: fixed SEED, frozen hash committed before any model sees it.

Label merging:
  LASTNAME1/2/3 -> LASTNAME, GIVENNAME1/2 -> GIVENNAME (numbering is a dataset
  artifact; disclosed in the problem statement).
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

SEED = 42
TEST_SIZE = 500
MIN_PER_LABEL = 10
DATA_DIR = Path(__file__).resolve().parent.parent / "data"


# ---------------------------------------------------------------------------
# Label taxonomy
# ---------------------------------------------------------------------------

def base_label(label: str) -> str:
    """LASTNAME1 -> LASTNAME, GIVENNAME2 -> GIVENNAME, EMAIL -> EMAIL."""
    return re.sub(r"\d+$", "", label)


def build_label_taxonomy(train_rows) -> tuple[list[str], dict[str, str]]:
    """Return (sorted canonical labels, raw->canonical mapping)."""
    raw_labels: set[str] = set()
    for row in train_rows:
        for ent in row["privacy_mask"]:
            raw_labels.add(ent["label"])

    mapping: dict[str, str] = {raw: base_label(raw) for raw in sorted(raw_labels)}
    canonical = sorted(set(mapping.values()))
    return canonical, mapping


# ---------------------------------------------------------------------------
# Test-set sampling
# ---------------------------------------------------------------------------

def chunk_base_id(row_id: str) -> str:
    """'40767A' -> '40767', '40767' -> '40767'."""
    return re.sub(r"[A-Za-z]+$", "", str(row_id))


def sample_test_set(
    val_rows,
    train_ids: set[str],
    label_map: dict[str, str],
    rng_seed: int = SEED,
) -> list[dict]:
    import random
    rng = random.Random(rng_seed)

    # Drop any validation row whose base id overlaps with train.
    eligible = [
        r for r in val_rows
        if chunk_base_id(r.get("id", "")) not in train_ids
    ]

    # Normalize entities using canonical labels.
    def canonical_entities(row) -> list[dict]:
        seen: set[tuple] = set()
        out = []
        for ent in row["privacy_mask"]:
            val = ent["value"].strip(" .,;:")
            lab = label_map[ent["label"]]
            if (val, lab) not in seen:
                seen.add((val, lab))
                out.append({"value": val, "label": lab})
        return out

    # Bucket rows by which labels they contain.
    label_to_rows: dict[str, list[int]] = defaultdict(list)
    zero_pii: list[int] = []
    for i, row in enumerate(eligible):
        ents = canonical_entities(row)
        if not ents:
            zero_pii.append(i)
        for lab in {e["label"] for e in ents}:
            label_to_rows[lab].append(i)

    all_labels = sorted(label_to_rows)

    # Phase 1: guarantee >= MIN_PER_LABEL instances per label.
    selected: set[int] = set()
    label_counts: Counter = Counter()

    for lab in all_labels:
        if label_counts[lab] >= MIN_PER_LABEL:
            continue
        pool = [i for i in label_to_rows[lab] if i not in selected]
        rng.shuffle(pool)
        for idx in pool:
            if label_counts[lab] >= MIN_PER_LABEL:
                break
            selected.add(idx)
            for e in canonical_entities(eligible[idx]):
                label_counts[e["label"]] += 1

    # Phase 2: fill to TEST_SIZE preserving natural zero-PII ratio.
    natural_zero_rate = len(zero_pii) / max(len(eligible), 1)
    target_zero = round((TEST_SIZE - len(selected)) * natural_zero_rate)

    remaining_zero = [i for i in zero_pii if i not in selected]
    remaining_pii = [
        i for i in range(len(eligible))
        if i not in selected and eligible[i]["privacy_mask"]
    ]
    rng.shuffle(remaining_zero)
    rng.shuffle(remaining_pii)

    slots_left = TEST_SIZE - len(selected)
    zero_to_add = min(target_zero, len(remaining_zero), slots_left)
    pii_to_add = min(slots_left - zero_to_add, len(remaining_pii))

    selected.update(remaining_zero[:zero_to_add])
    selected.update(remaining_pii[:pii_to_add])

    # Build output rows.
    result = []
    for idx in sorted(selected):
        row = eligible[idx]
        ents = canonical_entities(row)
        result.append({
            "id": str(row.get("id", idx)),
            "source_text": row["source_text"],
            "entities": ents,
        })

    rng.shuffle(result)
    return result[:TEST_SIZE]


# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------

def print_stats(rows: list[dict], canonical_labels: list[str]) -> None:
    label_counts: Counter = Counter()
    zero_pii = 0
    for row in rows:
        if not row["entities"]:
            zero_pii += 1
        for ent in row["entities"]:
            label_counts[ent["label"]] += 1

    print(f"\nTest set: {len(rows)} rows, {zero_pii} zero-PII "
          f"({100*zero_pii/len(rows):.1f}%)")
    print(f"{'Label':<22} {'Count':>6}  {'>=10?':>5}")
    print("-" * 38)
    missing = []
    for lab in canonical_labels:
        n = label_counts[lab]
        ok = "yes" if n >= MIN_PER_LABEL else "NO"
        print(f"  {lab:<20} {n:>6}  {ok:>5}")
        if n < MIN_PER_LABEL:
            missing.append(lab)
    if missing:
        print(f"\nWARNING: {len(missing)} label(s) below {MIN_PER_LABEL}: {missing}")
    else:
        print(f"\nAll {len(canonical_labels)} labels have >= {MIN_PER_LABEL} instances.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    try:
        from datasets import load_dataset
    except ImportError:
        print("ERROR: 'datasets' not installed. Run: pip install datasets", file=sys.stderr)
        return 1

    print("Loading ai4privacy/pii-masking-300k …")
    ds = load_dataset("ai4privacy/pii-masking-300k", trust_remote_code=True)

    # Filter English rows.
    print("Filtering English rows …")
    train_en = [r for r in ds["train"] if r.get("language") == "English"]
    val_en   = [r for r in ds["validation"] if r.get("language") == "English"]
    print(f"  train English: {len(train_en):,}  |  validation English: {len(val_en):,}")

    # Build label taxonomy from train.
    print("Enumerating labels from train split …")
    canonical_labels, label_map = build_label_taxonomy(train_en)
    print(f"  {len({v for v in label_map})} raw labels -> "
          f"{len(canonical_labels)} canonical labels after merging numbered variants")

    # Collect base ids from train to avoid chunk leakage.
    train_base_ids = {chunk_base_id(str(r.get("id", ""))) for r in train_en}

    # Sample test set.
    print(f"Sampling {TEST_SIZE} rows from validation …")
    test_rows = sample_test_set(val_en, train_base_ids, label_map)

    print_stats(test_rows, canonical_labels)

    # Write outputs.
    DATA_DIR.mkdir(exist_ok=True)

    labels_path = DATA_DIR / "labels.json"
    labels_path.write_text(
        json.dumps({
            "canonical": canonical_labels,
            "raw_to_canonical": label_map,
            "merge_note": (
                "Numbered suffixes stripped: LASTNAME1/2/3 -> LASTNAME, "
                "GIVENNAME1/2 -> GIVENNAME, etc."
            ),
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\nWrote {labels_path}")

    test_path = DATA_DIR / "test_set.jsonl"
    with test_path.open("w", encoding="utf-8") as fh:
        for row in test_rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"Wrote {test_path}")

    sha = hashlib.sha256(test_path.read_bytes()).hexdigest()
    sha_path = DATA_DIR / "test_set.sha256"
    sha_path.write_text(sha + "\n", encoding="utf-8")
    print(f"Wrote {sha_path}")
    print(f"\nSHA-256: {sha}")
    print("\nCommit data/test_set.sha256 NOW before any model runs on the test set.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
