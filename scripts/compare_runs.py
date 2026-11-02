#!/usr/bin/env python3
"""Paired comparison of two runs on the same test rows.

Usage: compare_runs.py <before.jsonl> <after.jsonl>
McNemar exact test on per-row pass/fail, and a 10,000-sample bootstrap of
the gain in row pass rate (95% range).
"""

import json
import random
import sys
from math import comb


def load(p):
    return {r["id"]: r for r in map(json.loads, open(p))}


def main() -> int:
    a, b = load(sys.argv[1]), load(sys.argv[2])
    ids = sorted(set(a) & set(b))
    pa = [a[i]["pass"] for i in ids]
    pb = [b[i]["pass"] for i in ids]
    only_b = sum(1 for x, y in zip(pa, pb) if y and not x)
    only_a = sum(1 for x, y in zip(pa, pb) if x and not y)
    n, k = only_a + only_b, min(only_a, only_b)
    p = min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n) if n else 1.0
    rng = random.Random(0)
    gains = sorted(
        sum(pb[j] - pa[j] for j in s) / len(ids)
        for s in ([rng.randrange(len(ids)) for _ in ids] for _ in range(10000))
    )
    print(json.dumps({"rows": len(ids), "before": round(sum(pa) / len(ids), 3), "after": round(sum(pb) / len(ids), 3),
                      "only_before_right": only_a, "only_after_right": only_b, "mcnemar_p": float(f"{p:.2g}"),
                      "gain_95": [round(gains[250], 3), round(gains[9749], 3)]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
