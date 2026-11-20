"""Choose the matching threshold on DEV only (AMI dev + synthetic dev). No baseline output is used.

    .venv/bin/python 6_evaluation/action_items/calibrate.py

A predicted item and a gold item should match when they are about the same task. Both datasets say which transcript
lines each gold item was written from (AMI: extractive summary links; synthetic: evidence + history segments). So
for every gold item and every transcript line of 3+ words in its meeting, the pair is "same task" if the line is
one of the item's lines. The threshold is the cosine similarity that best separates those pairs (F1), averaged
over the two datasets so each counts equally. Grid 0.50 ... 0.90, step 0.025.
Writes results/tuning/match_threshold.json; copy the value into 3_action_items/settings.py MATCHING["threshold"].
"""
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "3_action_items"))
import data  # noqa: E402
import metrics  # noqa: E402
import rules  # noqa: E402
import settings  # noqa: E402
from device import pick_device  # noqa: E402

GRID = [round(0.5 + 0.025 * i, 3) for i in range(17)]


def pairs(meetings, embed):
    pos, neg = [], []
    for m in meetings:
        lines = [l for l in m.lines if len(l["text"].split()) >= 3]
        if not m.gold or not lines:
            continue
        sim = metrics.similarity(embed, [rules.clean(l["text"]) for l in lines], [g.match_text for g in m.gold])
        for j, g in enumerate(m.gold):
            for i, line in enumerate(lines):
                (pos if line["id"] in g.evidence else neg).append(sim[i, j])
    return np.array(pos), np.array(neg)


def f1_at(pos, neg, t):
    tp, fp, fn = (pos >= t).sum(), (neg >= t).sum(), (pos < t).sum()
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return 2 * p * r / (p + r) if p + r else 0.0


def main():
    embed = metrics.Embedder(settings.MATCHING, settings.CACHE_DIR, pick_device("auto"))
    table, info = {}, {}
    for source in ("ami", "synthetic"):
        meetings, info[source] = data.load(source, "dev")
        pos, neg = pairs(meetings, embed)
        table[source] = {t: round(f1_at(pos, neg, t), 4) for t in GRID}
        info[source].update(positive_pairs=int(len(pos)), negative_pairs=int(len(neg)))
    mean = {t: round((table["ami"][t] + table["synthetic"][t]) / 2, 4) for t in GRID}
    best = max(GRID, key=lambda t: (mean[t], -t))
    print(" threshold  F1 ami  F1 synthetic  mean")
    for t in GRID:
        print(f"   {t:.3f}    {table['ami'][t]:.3f}     {table['synthetic'][t]:.3f}     {mean[t]:.3f}{'  <- best' if t == best else ''}")
    out = {"chosen_threshold": best, "objective": "mean over AMI dev and synthetic dev of F1(evidence line vs not)",
           "grid": GRID, "f1": {k: {str(t): v for t, v in d.items()} for k, d in {**table, "mean": mean}.items()},
           "embedder": settings.MATCHING["embedder"], "revision": settings.MATCHING["revision"],
           "data": {k: {kk: vv for kk, vv in v.items() if kk != "meetings"} for k, v in info.items()},
           "made": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    path = os.path.join(settings.RESULTS_DIR, "tuning", "match_threshold.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"chosen {best}; wrote {os.path.relpath(path, settings.REPO)}")
    if best != settings.MATCHING["threshold"]:
        print(f"NOTE: settings.MATCHING['threshold'] is {settings.MATCHING['threshold']}; set it to {best}")


if __name__ == "__main__":
    main()
