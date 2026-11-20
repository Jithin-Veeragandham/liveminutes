"""Print random predictions with their transcript lines and whether they matched, for reading errors by hand.

    .venv/bin/python 6_evaluation/action_items/sample_errors.py --baseline rules --split dev --n 20
    .venv/bin/python 6_evaluation/action_items/sample_errors.py --baseline zeroshot --split dev --n 20

Samples from AMI and synthetic together (numpy default_rng(0)). ERRORS.md was written from this output on DEV, so
that any fix made after reading it is a dev-only change.
"""
import argparse
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
for folder in ["3_action_items", "6_evaluation/action_items", "5_data/ami"]:
    sys.path.insert(0, os.path.join(REPO, folder))
import data  # noqa: E402
import metrics  # noqa: E402
import settings  # noqa: E402
from device import pick_device  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--baseline", choices=["rules", "zeroshot"], required=True)
    p.add_argument("--split", default="dev", choices=["dev", "smoke"])
    p.add_argument("--n", type=int, default=20)
    a = p.parse_args()
    if a.baseline == "rules":
        from rules import RuleBaseline as B
    else:
        from zeroshot import ZeroShotBaseline as B
    baseline = B()
    embed = metrics.Embedder(settings.MATCHING, settings.CACHE_DIR, pick_device(settings.ZEROSHOT["device"]))
    rows = []
    for source in ("ami", "synthetic"):
        for m in data.load(source, a.split)[0]:
            preds = baseline.predict(m)
            c = metrics.score_meeting(m, preds, embed, settings.MATCHING["threshold"])
            sim = metrics.similarity(embed, [x["text"] for x in preds], [g.match_text for g in m.gold])
            matched = {i: (j, s) for i, j, s in c.pairs}
            for i, item in enumerate(preds):
                rows.append((m, item, matched.get(i), sim[i] if len(m.gold) else None))
    rng = np.random.default_rng(0)
    pick = sorted(rng.choice(len(rows), size=min(a.n, len(rows)), replace=False))
    print(f"{a.baseline} on {a.split}: {len(rows)} predictions, showing {len(pick)} at random\n")
    for k, idx in enumerate(pick, 1):
        m, item, match, sims = rows[idx]
        by = {l["id"]: n for n, l in enumerate(m.lines)}
        first = by[item["source_line_ids"][0]]
        print(f"#{k} {m.id} [{item['kind']}] owner={item['owner']!r} due={item.get('due_text')!r}")
        print(f"   text: {item['text']}")
        for n in range(max(0, first - 1), min(len(m.lines), first + 2)):
            line = m.lines[n]
            mark = ">>" if line["id"] in item["source_line_ids"] else "  "
            print(f"   {mark} {line['speaker'][:14]:14} {line['text'][:150]}")
        if match:
            g = m.gold[match[0]]
            ok = metrics.owner_key(item["owner"], m.people) in (g.owners or {""})
            print(f"   MATCHED ({match[1]:.2f}) gold: {g.text}  owners={sorted(g.owners) or 'unassigned'}  owner {'ok' if ok else 'WRONG'}")
        else:
            best = int(np.argmax(sims)) if sims is not None and len(sims) else None
            print("   NO MATCH" + (f"; closest gold ({sims[best]:.2f}): {m.gold[best].text}" if best is not None else "; meeting has no gold"))
        print()


if __name__ == "__main__":
    main()
