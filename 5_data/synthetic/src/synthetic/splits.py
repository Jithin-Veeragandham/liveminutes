"""Series-level train/dev/test assignment for synthetic data.

Each series goes whole into one split, the same rule as AMI. Within each feature
(F1/F2/F3), series are shuffled with a seeded RNG and dealt out so every split gets
at least one series of that feature when there are >= 3. The rest is divided by
largest remainder on the ratios. The result depends only on the series IDs, their
features and the seed. It is written separately from any AMI split.
"""
from __future__ import annotations

import hashlib
import random

SPLITS = ("train", "dev", "test")


def _counts(n: int, ratios: dict) -> dict:
    raw = {s: n * ratios[s] for s in SPLITS}
    counts = {s: int(raw[s]) for s in SPLITS}
    left = n - sum(counts.values())
    for s in sorted(SPLITS, key=lambda s: (-(raw[s] - int(raw[s])), SPLITS.index(s)))[:left]:
        counts[s] += 1
    # Every split gets a series of this feature whenever there are enough.
    if n >= len(SPLITS):
        for s in SPLITS:
            if counts[s] == 0:
                donor = max(SPLITS, key=lambda d: (counts[d], -SPLITS.index(d)))
                counts[donor] -= 1
                counts[s] += 1
    return counts


def assign(index: list[dict], seed: int, ratios: dict) -> dict:
    series = {}
    for row in index:
        series.setdefault(row["series_id"], {"feature": row["feature"], "meetings": []})
        series[row["series_id"]]["meetings"].append(row["meeting_id"])
    assignment = {}
    for feature in sorted({v["feature"] for v in series.values()}):
        ids = sorted(k for k, v in series.items() if v["feature"] == feature)
        h = hashlib.sha256(f"{seed}:split:{feature}".encode()).hexdigest()
        random.Random(int(h[:16], 16)).shuffle(ids)
        c = _counts(len(ids), ratios)
        i = 0
        for s in SPLITS:
            for sid in ids[i:i + c[s]]:
                assignment[sid] = s
            i += c[s]
    missing = [s for s in SPLITS if s not in assignment.values()]
    return {
        "seed": seed,
        "ratios": ratios,
        "rule": "whole series per split, stratified by feature",
        "series": {sid: {"split": assignment[sid], "feature": series[sid]["feature"],
                         "meetings": sorted(series[sid]["meetings"])} for sid in sorted(series)},
        "splits": {s: sorted(m for sid, v in series.items() if assignment[sid] == s for m in v["meetings"])
                   for s in SPLITS},
        "empty_splits": missing,
    }
