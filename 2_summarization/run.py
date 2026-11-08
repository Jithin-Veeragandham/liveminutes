"""Run one summarizer on one split of AMI over several seeds, score it, and log the number with its commit,
config, data hash and every seed.

    python 2_summarization/run.py --model textrank --split smoke             # baseline, 3 meetings, N_SEEDS seeds
    python 2_summarization/run.py --model textrank --split eval              # baseline, 20 evaluation meetings
    python 2_summarization/run.py --model textrank --split smoke --seeds 5   # 5 seeds instead of N_SEEDS
    python 2_summarization/run.py --model textrank --split smoke --seed 7    # exactly one seed: 7
    python 2_summarization/run.py --model bart --split smoke                 # deep row (needs requirements-bart.txt)

Order of steps (raw data -> logged number):
  1. fix PYTHONHASHSEED   2. fetch/verify the data against MANIFEST.json   3. load + check the committed split
  4. for each seed: set seeds, summarize each meeting, score against the human abstract
  5. average over seeds (mean and std)   6. append one line to 6_evaluation/summarization/results/results_log.jsonl
"""

# =====================================================================================================
# SEEDS: change them here. A run uses N_SEEDS seeds: FIRST_SEED, FIRST_SEED + 1, ..., FIRST_SEED + N_SEEDS - 1.
# Override on the command line:  --seeds 5  (use 5 seeds)   or   --seed 7  (use exactly one seed, 7).
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

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
for folder in (HERE, os.path.join(REPO, "5_data", "ami"), os.path.join(REPO, "6_evaluation", "summarization")):
    sys.path.insert(0, folder)
import ami  # noqa: E402
import ami_settings  # noqa: E402
import fetch_data  # noqa: E402
import settings  # noqa: E402
import split as split_mod  # noqa: E402
from metrics import bertscore, rouge_all  # noqa: E402

RESULTS_PATH = "6_evaluation/summarization/results/"   # left out of the "uncommitted changes" check


# The seeds this run uses: --seed S gives [S]; --seeds N gives N seeds from FIRST_SEED; default N_SEEDS.
def choose_seeds(a):
    if a.seed is not None:
        return [a.seed]
    n = N_SEEDS if a.seeds is None else a.seeds
    if n < 1:
        sys.exit("--seeds must be at least 1")
    return list(range(FIRST_SEED, FIRST_SEED + n))


# PYTHONHASHSEED only works at interpreter start, so set it to the run's first seed and restart once.
def fix_hash_seed(seed):
    if os.environ.get("PYTHONHASHSEED") != str(seed):
        os.environ["PYTHONHASHSEED"] = str(seed)
        os.execv(sys.executable, [sys.executable] + sys.argv)


# Fix every other source of randomness for one seed: Python random, NumPy, torch (if installed).
def set_seeds(seed):
    random.seed(seed)
    np.random.seed(seed)
    try:
        import torch
        torch.manual_seed(seed)
    except ImportError:
        pass   # baseline environment has no torch


# Return (commit hash, list of uncommitted changed files outside the results folder) for the repository.
def git_state():
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True,
                                check=True).stdout.strip()
        status = subprocess.run(["git", "status", "--porcelain"], cwd=REPO, capture_output=True, text=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return "unknown (not a git checkout)", []
    changed = [l[3:] for l in status.splitlines() if not l[3:].startswith(RESULTS_PATH)]
    return commit, changed


# Pick the meetings of the requested split; meetings with no human abstract cannot be scored and are skipped.
def choose_meetings(split, name):
    ids = ami_settings.SMOKE_MEETINGS if name == "smoke" else split[name]
    meetings = [ami.load_meeting(m) for m in ids]
    return [m for m in meetings if m.reference], [m.id for m in meetings if not m.reference]


# TextRank word budget: average length of the TRAINING split's human abstracts (train only, so no leakage).
def train_summary_length(split):
    lengths = [len(ami.read_reference(m).split()) for m in split["train"]]
    return int(round(np.mean([n for n in lengths if n])))


# Build the requested summarizer and the exact config it runs with.
def make_model(name, split):
    if name == "textrank":
        from textrank_baseline import TextRankSummarizer
        cfg = dict(settings.TEXTRANK)
        cfg["words"] = cfg["words"] or train_summary_length(split)
        return TextRankSummarizer(cfg["words"], cfg["min_line_words"]), cfg
    from bart_summarizer import BartSummarizer
    return BartSummarizer(settings.BART), dict(settings.BART)


# Versions of the given packages that are installed (packages that are not installed are left out).
def installed_versions(names):
    out = {}
    for name in names:
        try:
            out[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    return out


# Short hash of any JSON-able value (used for the config and the meeting list).
def short_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:12]


# Summarize and score every meeting with one seed. Returns (metrics, per-meeting scores, predictions, seconds).
def run_one_seed(seed, model, meetings, with_bertscore):
    set_seeds(seed)
    start, predictions = time.perf_counter(), {}
    for m in meetings:
        predictions[m.id] = model.summarize(m.lines)
    seconds = time.perf_counter() - start
    per_meeting = {m.id: rouge_all(m.reference, predictions[m.id]) for m in meetings}
    metrics = {k: float(np.mean([s[k] for s in per_meeting.values()])) for k in next(iter(per_meeting.values()))}
    if with_bertscore:
        f1 = bertscore([m.reference for m in meetings], [predictions[m.id] for m in meetings], settings.BERTSCORE)
        metrics["bertscore_f1"] = float(np.mean(f1))
    return metrics, per_meeting, predictions, seconds


# Run everything and log the result.
def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model", choices=["textrank", "bart"], default="textrank")
    p.add_argument("--split", choices=["smoke", "dev", "eval"], default="smoke")
    seeds_arg = p.add_mutually_exclusive_group()
    seeds_arg.add_argument("--seeds", type=int, help=f"number of seeds (default N_SEEDS = {N_SEEDS}, from FIRST_SEED)")
    seeds_arg.add_argument("--seed", type=int, help="run exactly one seed, with this value")
    p.add_argument("--bertscore", action="store_true", help="also compute BERTScore (needs requirements-bart.txt)")
    p.add_argument("--no-log", action="store_true", help="do not append to results_log.jsonl")
    a = p.parse_args()
    seeds = choose_seeds(a)

    fix_hash_seed(seeds[0])                                                 # 1
    data_hash = fetch_data.verify() if os.path.isdir(ami_settings.AMI_DIR) else fetch_data.fetch()   # 2
    split = split_mod.load_split()                                          # 3
    meetings, skipped = choose_meetings(split, a.split)
    print(f"split OK; {a.split}: {len(meetings)} meetings" + (f" (no abstract, skipped: {skipped})" if skipped else ""))
    print(f"seeds: {len(seeds)} ({seeds[0]}..{seeds[-1]})")

    model, model_cfg = make_model(a.model, split)
    per_seed, runs, total_seconds = {}, {}, 0.0
    for seed in seeds:                                                      # 4
        metrics, per_meeting, predictions, seconds = run_one_seed(seed, model, meetings, a.bertscore)
        per_seed[str(seed)] = {k: round(v, 4) for k, v in metrics.items()}
        runs[str(seed)] = {"per_meeting": per_meeting, "predictions": predictions}
        total_seconds += seconds
        print(f"  seed {seed}: " + "  ".join(f"{k} {v:.4f}" for k, v in metrics.items()), flush=True)

    names = list(next(iter(per_seed.values())))                             # 5
    mean = {k: round(float(np.mean([s[k] for s in per_seed.values()])), 4) for k in names}
    std = {k: round(float(np.std([s[k] for s in per_seed.values()], ddof=1)) if len(seeds) > 1 else 0.0, 4)
           for k in names}
    first = runs[str(seeds[0])]["predictions"]
    identical = all(r["predictions"] == first for r in runs.values())
    seconds_per_meeting = round(total_seconds / (len(seeds) * len(meetings)), 3)

    commit, changed = git_state()
    config = {"model": model_cfg, "seeds": seeds, "pythonhashseed": seeds[0],
              "bertscore": settings.BERTSCORE if a.bertscore else None}
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"_{a.model}_{a.split}_{len(seeds)}seeds"
    record = {                                                              # 6
        "run_id": run_id,
        "commit": commit, "uncommitted_changes": changed,
        "data_hash": data_hash, "split_file_sha256": fetch_data.sha256_file(ami_settings.SPLIT_FILE),
        "config": config, "config_hash": short_hash(config),
        "model": a.model, "model_version": model.version,
        "split": a.split, "meetings": [m.id for m in meetings], "meetings_hash": short_hash([m.id for m in meetings]),
        "n_seeds": len(seeds), "seeds": seeds,
        "metrics_mean": mean, "metrics_std": std, "metrics_per_seed": per_seed,
        "identical_across_seeds": identical, "seconds_per_meeting": seconds_per_meeting,
        "python": platform.python_version(),
        "packages": installed_versions(["summa", "rouge-score", "numpy", "torch", "transformers", "bert-score"]),
        "hardware": {"machine": platform.machine(), "system": platform.platform(), "cpus": os.cpu_count()},
    }
    if not a.no_log:
        os.makedirs(os.path.join(settings.RESULTS_DIR, "runs"), exist_ok=True)
        with open(os.path.join(settings.RESULTS_DIR, "runs", run_id + ".json"), "w") as f:
            json.dump({**record, "per_seed_runs": runs}, f, indent=2)
        with open(settings.RESULTS_LOG, "a") as f:
            f.write(json.dumps(record) + "\n")

    print(f"\nmean over {len(seeds)} seeds:  " + "  ".join(f"{k} {mean[k]} (±{std[k]})" for k in names))
    print(f"seconds_per_meeting {seconds_per_meeting}  identical_across_seeds {identical}")
    print(f"{'not logged (--no-log)' if a.no_log else 'logged'} {run_id}  commit {commit[:12]}  "
          f"data {data_hash[:12]}  config {record['config_hash']}")
    if changed:
        print(f"WARNING: {len(changed)} uncommitted file(s); commit them so this number traces to one commit: {changed}")


if __name__ == "__main__":
    main()
