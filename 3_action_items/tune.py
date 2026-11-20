"""Choose the baselines' settings on the DEV splits only (AMI dev + synthetic dev). Never on AMI eval / synthetic test.

    python 3_action_items/tune.py rules       # merge_jaccard x lookahead_lines            (about 10 s)
    python 3_action_items/tune.py zeroshot    # hypothesis wording x context x threshold   (about 15 min the first
                                              # time on Apple MPS, seconds later: scores are cached)

Objective: the mean of item F1 on AMI dev and on synthetic dev, so each dataset counts equally (synthetic dev has
117 gold items, AMI dev 41). Ties: the earlier grid value. Matching as in the evaluation (settings.MATCHING).
Writes 6_evaluation/action_items/results/tuning/<baseline>_dev.json with the full grid; copy the chosen values into
settings.py (RULES / ZEROSHOT) by hand, so a change is visible in git.
"""
import itertools
import json
import os
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
for folder in ["3_action_items", "6_evaluation/action_items", "5_data/ami"]:
    sys.path.insert(0, os.path.join(REPO, folder))
import data  # noqa: E402
import metrics  # noqa: E402
import rules  # noqa: E402
import settings  # noqa: E402
from device import pick_device  # noqa: E402

RULES_GRID = {"merge_jaccard": [0.3, 0.4, 0.5, 0.6, 0.7], "lookahead_lines": [2, 3, 4], "text_until": ["line", "sentence"]}


def f1(meetings, predictions, embed):
    counts = [metrics.score_meeting(m, predictions[m.id], embed, settings.MATCHING["threshold"]) for m in meetings]
    return metrics.rates(metrics.total(counts))


def dev_sets():
    return {source: data.load(source, "dev")[0] for source in ("ami", "synthetic")}


def save(name, out):
    path = os.path.join(settings.RESULTS_DIR, "tuning", f"{name}_dev.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({**out, "made": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "matching": settings.MATCHING}, f, indent=1)
    print(f"wrote {os.path.relpath(path, REPO)}")


def tune_rules(sets, embed):
    rows = []
    for mj, la, until in itertools.product(RULES_GRID["merge_jaccard"], RULES_GRID["lookahead_lines"],
                                           RULES_GRID["text_until"]):
        cfg = {**settings.RULES, "merge_jaccard": mj, "lookahead_lines": la, "text_until": until}
        res = {s: f1(ms, {m.id: rules.extract(m.lines, m.people, None, cfg) for m in ms}, embed) for s, ms in sets.items()}
        rows.append({"merge_jaccard": mj, "lookahead_lines": la, "text_until": until, "f1_ami": round(res["ami"]["f1"], 4),
                     "f1_synthetic": round(res["synthetic"]["f1"], 4),
                     "mean_f1": round((res["ami"]["f1"] + res["synthetic"]["f1"]) / 2, 4)})
        print(f"  merge_jaccard {mj}  lookahead {la}  text until {until}:  AMI {rows[-1]['f1_ami']:.3f}  synthetic "
              f"{rows[-1]['f1_synthetic']:.3f}  mean {rows[-1]['mean_f1']:.3f}")
    best = max(rows, key=lambda r: r["mean_f1"])   # max() keeps the first of equal values
    print(f"chosen: merge_jaccard {best['merge_jaccard']}, lookahead_lines {best['lookahead_lines']}, "
          f"text_until {best['text_until']}")
    save("rules", {"objective": "mean F1 (AMI dev, synthetic dev)", "grid": RULES_GRID, "rows": rows, "chosen": best})


def tune_zeroshot(sets, embed):
    import zeroshot
    grid = settings.ZEROSHOT_GRID
    scorer = zeroshot.Scorer(settings.ZEROSHOT)
    rows, start = [], time.perf_counter()
    for mode, hyp, ctx in itertools.product(grid["mode"], grid["hypothesis"], grid["context"]):
        t = time.perf_counter()
        scores = {s: {m.id: zeroshot.scored_lines(m.lines, scorer, settings.ZEROSHOT, hyp, ctx, mode) for m in ms}
                  for s, ms in sets.items()}
        print(f"  scored {mode} '{hyp[:40]}...' / {ctx} in {time.perf_counter() - t:.0f} s "
              f"({scorer.computed} lines computed so far, {scorer.hits} from cache)", flush=True)
        for th in grid["threshold"]:
            res = {s: f1(ms, {m.id: zeroshot.items_from_scores(m.lines, m.people, scores[s][m.id], th, settings.RULES)
                              for m in ms}, embed) for s, ms in sets.items()}
            rows.append({"mode": mode, "hypothesis": hyp, "context": ctx, "threshold": th,
                         "f1_ami": round(res["ami"]["f1"], 4), "f1_synthetic": round(res["synthetic"]["f1"], 4),
                         "p_ami": round(res["ami"]["precision"], 4), "r_ami": round(res["ami"]["recall"], 4),
                         "p_synthetic": round(res["synthetic"]["precision"], 4),
                         "r_synthetic": round(res["synthetic"]["recall"], 4),
                         "mean_f1": round((res["ami"]["f1"] + res["synthetic"]["f1"]) / 2, 4)})
        top = max((r for r in rows if (r["mode"], r["hypothesis"], r["context"]) == (mode, hyp, ctx)),
                  key=lambda r: r["mean_f1"])
        print(f"    best threshold {top['threshold']}: AMI {top['f1_ami']:.3f}  synthetic {top['f1_synthetic']:.3f}  "
              f"mean {top['mean_f1']:.3f}", flush=True)
    best = max(rows, key=lambda r: r["mean_f1"])
    print(f"chosen: {best['mode']} / '{best['hypothesis']}' / context {best['context']} / threshold {best['threshold']} "
          f"(mean F1 {best['mean_f1']})")
    save("zeroshot", {"objective": "mean F1 (AMI dev, synthetic dev)", "grid": grid, "rows": rows, "chosen": best,
                      "model": f"{settings.ZEROSHOT['model']}@{settings.ZEROSHOT['revision']}",
                      "device": scorer.device, "lines_computed": scorer.computed, "lines_from_cache": scorer.hits,
                      "model_seconds": round(scorer.seconds, 1), "total_seconds": round(time.perf_counter() - start, 1)})


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else ""
    if which not in ("rules", "zeroshot"):
        sys.exit("usage: python 3_action_items/tune.py rules|zeroshot")
    embed = metrics.Embedder(settings.MATCHING, settings.CACHE_DIR, pick_device(settings.ZEROSHOT["device"]))
    sets = dev_sets()
    (tune_rules if which == "rules" else tune_zeroshot)(sets, embed)


if __name__ == "__main__":
    main()
