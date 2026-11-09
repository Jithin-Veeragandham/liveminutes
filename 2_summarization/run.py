"""Run a summarizer on AMI with several seeds, score it, and log the result.

    python 2_summarization/run.py --model textrank --split smoke             # baseline, 3 meetings, N_SEEDS seeds
    python 2_summarization/run.py --model textrank --split eval              # baseline, 20 test meetings
    python 2_summarization/run.py --model textrank --split smoke --seeds 5   # 5 seeds
    python 2_summarization/run.py --model textrank --split smoke --seed 7    # one seed: 7
    python 2_summarization/run.py --model bart --split smoke --seeds 3       # deep model (needs requirements-bart.txt)

Steps (see main() at the bottom):
  1. check the data     2. load the split     3. build the model
  4. summarize + score every meeting, once per seed     5. average over seeds     6. save to the results log
"""

# =====================================================================================================
# SEEDS: change them here. A run uses N_SEEDS seeds: FIRST_SEED, FIRST_SEED + 1, ...
# Or on the command line:  --seeds 5  (5 seeds)   or   --seed 7  (exactly one seed, 7).
N_SEEDS = 20
FIRST_SEED = 42
# =====================================================================================================

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import random
import subprocess
import sys
import time
from datetime import datetime, timezone

import numpy as np

# Our code lives in three folders (models, data, metrics). Let Python import from all three.
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for folder in ["2_summarization", "5_data/ami", "6_evaluation/summarization"]:
    sys.path.insert(0, os.path.join(REPO, folder))
import ami  # noqa: E402
import ami_settings  # noqa: E402
import fetch_data  # noqa: E402
import settings  # noqa: E402
import split  # noqa: E402
from metrics import bertscore, rouge_all  # noqa: E402


# ---------------------------------------------------------------- seeds

# Which seeds to run: --seed S -> [S];  --seeds N -> N seeds;  nothing -> N_SEEDS seeds.
def choose_seeds(args):
    if args.seed is not None:
        return [args.seed]
    n = N_SEEDS if args.seeds is None else args.seeds
    if n < 1:
        sys.exit("--seeds must be at least 1")
    return list(range(FIRST_SEED, FIRST_SEED + n))


# Python's string hashing has its own seed (PYTHONHASHSEED). It can only be set before Python starts,
# so if it isn't set yet, set it and restart this script once.
def fix_hash_seed(seed):
    if os.environ.get("PYTHONHASHSEED") != str(seed):
        os.environ["PYTHONHASHSEED"] = str(seed)
        os.execv(sys.executable, [sys.executable] + sys.argv)


# Fix every random number generator: Python, NumPy, and torch (only installed for BART).
def set_seeds(seed):
    random.seed(seed)
    np.random.seed(seed)
    try:
        import torch
        torch.manual_seed(seed)
    except ImportError:
        pass


# ---------------------------------------------------------------- data and model

# The meetings to summarize. Meetings without a human summary can't be scored, so they are skipped.
def load_meetings(the_split, split_name):
    ids = ami_settings.SMOKE_MEETINGS if split_name == "smoke" else the_split[split_name]
    meetings = [ami.load_meeting(m) for m in ids]
    skipped = [m.id for m in meetings if not m.reference]
    if skipped:
        print(f"no human summary, skipped: {skipped}")
    return [m for m in meetings if m.reference]


# Build the model and return it with the exact settings it uses (these go into the log).
def make_model(model_name, the_split):
    if model_name == "textrank":
        from textrank_baseline import TextRankSummarizer
        cfg = dict(settings.TEXTRANK)
        # Summary length = average length of the TRAINING split's human summaries (train only: no leakage).
        lengths = [len(ami.read_reference(m).split()) for m in the_split["train"]]
        cfg["words"] = cfg["words"] or round(np.mean([n for n in lengths if n]))
        return TextRankSummarizer(cfg["words"], cfg["min_line_words"]), cfg
    from bart_summarizer import BartSummarizer
    return BartSummarizer(settings.BART), dict(settings.BART)


# ---------------------------------------------------------------- run and score

# One seed: summarize every meeting, score each summary, average over meetings.
def run_one_seed(seed, model, meetings, with_bertscore):
    set_seeds(seed)
    start = time.perf_counter()
    predictions = {m.id: model.summarize(m.lines) for m in meetings}
    seconds = time.perf_counter() - start

    per_meeting = {m.id: rouge_all(m.reference, predictions[m.id]) for m in meetings}
    scores = {name: np.mean([s[name] for s in per_meeting.values()]) for name in per_meeting[meetings[0].id]}
    if with_bertscore:
        f1 = bertscore([m.reference for m in meetings], [predictions[m.id] for m in meetings], settings.BERTSCORE)
        scores["bertscore_f1"] = np.mean(f1)
    scores = {name: round(float(v), 4) for name, v in scores.items()}
    return {"scores": scores, "per_meeting": per_meeting, "predictions": predictions, "seconds": seconds}


# Mean and standard deviation of each score across seeds.
def average_over_seeds(results):
    names = list(results[0]["scores"])
    mean = {n: round(float(np.mean([r["scores"][n] for r in results])), 4) for n in names}
    std = {n: round(float(np.std([r["scores"][n] for r in results], ddof=1)), 4) if len(results) > 1 else 0.0
           for n in names}
    return mean, std


# ---------------------------------------------------------------- results log

# Current git commit, and files changed but not committed (results/ is ignored, since runs write there).
def git_state():
    def git(*cmd):
        return subprocess.run(["git", *cmd], cwd=REPO, capture_output=True, text=True).stdout
    changed = [line[3:] for line in git("status", "--porcelain").splitlines()   # line = "XY path"
               if not line[3:].startswith("6_evaluation/summarization/results/")]
    return git("rev-parse", "HEAD").strip() or "unknown", changed


# Short fingerprint of a value, so two runs with the same config have the same config_hash.
def short_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:12]


# Versions of the packages that matter (packages not installed are left out).
def package_versions():
    out = {}
    for name in ["summa", "rouge-score", "numpy", "torch", "transformers", "bert-score"]:
        try:
            out[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    return out


# Write the result: one line in results_log.jsonl, and the full run (with every summary) in runs/<run_id>.json.
def save(record, results):
    os.makedirs(os.path.join(settings.RESULTS_DIR, "runs"), exist_ok=True)
    full = {**record, "per_seed_runs": {str(s): r for s, r in zip(record["config"]["seeds"], results)}}
    with open(os.path.join(settings.RESULTS_DIR, "runs", record["run_id"] + ".json"), "w") as f:
        json.dump(full, f, indent=2)
    with open(settings.RESULTS_LOG, "a") as f:
        f.write(json.dumps(record) + "\n")


# ---------------------------------------------------------------- main

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model", choices=["textrank", "bart"], default="textrank")
    p.add_argument("--split", choices=["smoke", "dev", "eval"], default="smoke")
    seed_options = p.add_mutually_exclusive_group()
    seed_options.add_argument("--seeds", type=int, help=f"number of seeds (default {N_SEEDS})")
    seed_options.add_argument("--seed", type=int, help="run exactly one seed, with this value")
    p.add_argument("--bertscore", action="store_true", help="also compute BERTScore (needs requirements-bart.txt)")
    p.add_argument("--no-log", action="store_true", help="don't save the result")
    args = p.parse_args()

    seeds = choose_seeds(args)
    fix_hash_seed(seeds[0])

    # 1. Check the data is exactly the version in MANIFEST.json (download it first if it's missing).
    data_hash = fetch_data.verify() if os.path.isdir(ami_settings.AMI_DIR) else fetch_data.fetch()

    # 2. Load the committed split and check no person is in two splits.
    the_split = split.load_split()
    meetings = load_meetings(the_split, args.split)
    print(f"split OK; {args.split}: {len(meetings)} meetings")
    print(f"seeds: {len(seeds)} ({seeds[0]}..{seeds[-1]})")

    # 3. Build the model.
    model, model_cfg = make_model(args.model, the_split)

    # 4. Summarize and score every meeting, once per seed.
    results = []
    for seed in seeds:
        r = run_one_seed(seed, model, meetings, args.bertscore)
        results.append(r)
        print(f"  seed {seed}: " + "  ".join(f"{k} {v:.4f}" for k, v in r["scores"].items()), flush=True)

    # 5. Average over seeds.
    mean, std = average_over_seeds(results)
    identical = all(r["predictions"] == results[0]["predictions"] for r in results)
    seconds_per_meeting = round(sum(r["seconds"] for r in results) / (len(seeds) * len(meetings)), 3)

    # 6. Save the scores together with everything needed to reproduce them.
    commit, changed = git_state()
    config = {"model": model_cfg, "seeds": seeds, "pythonhashseed": seeds[0],
              "bertscore": settings.BERTSCORE if args.bertscore else None}
    record = {
        "run_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"_{args.model}_{args.split}_{len(seeds)}seeds",
        "commit": commit, "uncommitted_changes": changed,
        "data_hash": data_hash, "split_file_sha256": fetch_data.sha256_file(ami_settings.SPLIT_FILE),
        "config": config, "config_hash": short_hash(config),
        "model": args.model, "model_version": model.version,
        "split": args.split, "meetings": [m.id for m in meetings],
        "meetings_hash": short_hash([m.id for m in meetings]),
        "n_seeds": len(seeds), "metrics_mean": mean, "metrics_std": std,
        "metrics_per_seed": {str(s): r["scores"] for s, r in zip(seeds, results)},
        "identical_across_seeds": identical, "seconds_per_meeting": seconds_per_meeting,
        "python": platform.python_version(), "packages": package_versions(),
        "hardware": {"machine": platform.machine(), "system": platform.platform(), "cpus": os.cpu_count()},
    }
    if not args.no_log:
        save(record, results)

    print(f"\nmean over {len(seeds)} seeds:  " + "  ".join(f"{k} {mean[k]} (±{std[k]})" for k in mean))
    print(f"seconds_per_meeting {seconds_per_meeting}  identical_across_seeds {identical}")
    print(f"{'not logged (--no-log)' if args.no_log else 'logged'} {record['run_id']}  commit {commit[:12]}  "
          f"data {data_hash[:12]}  config {record['config_hash']}")
    if changed:
        print(f"WARNING: {len(changed)} uncommitted file(s); commit them so this number traces to one commit: {changed}")


if __name__ == "__main__":
    main()
