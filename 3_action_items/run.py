"""Run an action-item baseline on AMI or the synthetic meetings, score it, and log the result.

    python 3_action_items/run.py --smoke                                         # both baselines, 3 dev meetings, < 2 min
    python 3_action_items/run.py --baseline rules --data ami --split eval        # 20 AMI eval meetings
    python 3_action_items/run.py --baseline zeroshot --data synthetic --split test
    python 3_action_items/run.py --baseline all --data all --split final         # both baselines: AMI eval + synthetic test
    python 3_action_items/run.py --baseline rules --data ami --split dev --no-log

Steps (main() at the bottom):
  1. load the meetings and check the data (AMI: MANIFEST.json hashes + split check; synthetic: the pinned commit)
  2. build the baseline     3. predict every meeting (contract format), timed
  4. score with the same code as any other system (6_evaluation/action_items/score.py), with breakdowns
  5. log: results_log.jsonl (one line), runs/<run_id>.json (per meeting), predictions/<run_id>.json (the items)
Both baselines are deterministic. The seed (settings.SEED) is set anyway and logged, as in 2_summarization.
"""
import argparse
import os
import random
import sys
import time
from datetime import datetime, timezone

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
for folder in ["3_action_items", "6_evaluation/action_items", "5_data/ami"]:
    sys.path.insert(0, os.path.join(REPO, folder))
import data  # noqa: E402
import score  # noqa: E402
import settings  # noqa: E402
from device import pick_device  # noqa: E402


def fix_hash_seed(seed):
    """Python's string hashing has its own seed; fix it (restarts this script once), as 2_summarization/run.py does."""
    if os.environ.get("PYTHONHASHSEED") != str(seed):
        os.environ["PYTHONHASHSEED"] = str(seed)
        os.execv(sys.executable, [sys.executable] + sys.argv)


def set_seeds(seed):
    random.seed(seed)
    np.random.seed(seed)
    try:
        import torch
        torch.manual_seed(seed)
    except ImportError:
        pass


def make_baseline(name):
    if name == "rules":
        from rules import RuleBaseline
        b = RuleBaseline()
        return b, {"baseline": "rules", "rules": b.cfg}, {}
    from zeroshot import ZeroShotBaseline
    b = ZeroShotBaseline()
    return b, {"baseline": "zeroshot", "zeroshot": b.cfg, "rules": b.rule_cfg}, {"classifier": b.version}


def run(name, source, split, no_log, ids=None):
    meetings, info = data.load(source, split, ids)
    print(f"{source} {split}: {len(meetings)} meetings, {sum(len(m.gold) for m in meetings)} gold items")
    set_seeds(settings.SEED)
    start = time.perf_counter()
    baseline, config, versions = make_baseline(name)
    built = time.perf_counter() - start
    predictions, per_meeting_s = {}, {}
    for m in meetings:
        t = time.perf_counter()
        predictions[m.id] = baseline.predict(m)
        per_meeting_s[m.id] = round(time.perf_counter() - t, 3)
    seconds = time.perf_counter() - start
    timing = {"total_s": round(seconds, 2), "build_s": round(built, 2),
              "seconds_per_meeting": round(sum(per_meeting_s.values()) / len(meetings), 3),
              "per_meeting_s": per_meeting_s}
    if name == "zeroshot":
        sc = baseline.scorer
        timing.update(lines_scored=sc.computed, lines_from_cache=sc.hits, model_s=round(sc.seconds, 2),
                      lines_per_s=round(sc.computed / sc.seconds, 1) if sc.seconds else None)
    print(f"{name}: {sum(len(v) for v in predictions.values())} items in {seconds:.1f} s "
          f"({timing['seconds_per_meeting']} s per meeting)"
          + (f"; {timing['lines_scored']} lines scored, {timing['lines_from_cache']} from cache" if name == "zeroshot" else ""))
    config = {**config, "seed": settings.SEED, "pythonhashseed": settings.SEED}
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"_{name}_{source}_{split}"
    folder = os.path.join(settings.CACHE_DIR, "unlogged") if no_log else settings.PREDICTIONS_DIR
    path = os.path.join(folder, run_id + ".json")
    score.write_predictions(path, name, info, predictions)
    device = pick_device(settings.ZEROSHOT["device"])
    return score.score_and_log(name, meetings, info, predictions, config, versions, timing,
                               None if no_log else path, no_log, device, run_id)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--baseline", choices=["rules", "zeroshot", "all"], default="all")
    p.add_argument("--data", choices=["ami", "synthetic", "all"], default="all")
    p.add_argument("--split", choices=["smoke", "dev", "eval", "test", "final"], default="smoke",
                   help="final = AMI eval + synthetic test (the reported numbers)")
    p.add_argument("--smoke", action="store_true", help="both baselines on 2 AMI dev + 1 synthetic dev meeting")
    p.add_argument("--no-log", action="store_true", help="don't save the result")
    a = p.parse_args()
    fix_hash_seed(settings.SEED)
    if a.smoke:
        a.baseline, a.data, a.split = "all", "all", "smoke"
    start = time.perf_counter()
    for source in (["ami", "synthetic"] if a.data == "all" else [a.data]):
        split = {"final": "eval" if source == "ami" else "test"}.get(a.split, a.split)
        for name in (["rules", "zeroshot"] if a.baseline == "all" else [a.baseline]):
            run(name, source, split, a.no_log)
    print(f"\ndone in {time.perf_counter() - start:.1f} s")


if __name__ == "__main__":
    main()
