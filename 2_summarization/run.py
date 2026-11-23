"""Run a summarizer on AMI or the synthetic meetings with several seeds, score it, and log the result.

Every run scores BOTH datasets by default (AMI, then the synthetic meetings), each logged as its own run.
--dataset ami / --dataset synthetic runs just one.

    python 2_summarization/run.py --model textrank --split smoke             # baseline, smoke split of both datasets
    python 2_summarization/run.py --model textrank --split eval              # baseline, 20 test meetings of each
    python 2_summarization/run.py --model textrank --dataset ami             # AMI only (or --dataset synthetic)
    python 2_summarization/run.py --model textrank --mode full               # whole transcript at once (default: recursive)
    python 2_summarization/run.py --model textrank --params 2_summarization/hpo/results/textrank_best.json   # tuned
    python 2_summarization/run.py --model textrank --split smoke --seeds 5   # 5 seeds
    python 2_summarization/run.py --model textrank --split smoke --seed 7    # one seed: 7
    python 2_summarization/run.py --model bart --split smoke --seeds 3       # deep model (BART packages are in requirements.txt)
    python 2_summarization/run.py --model llm --split smoke                  # LLM rolling notes (needs ANTHROPIC_API_KEY; 1 seed by default)

Modes (--mode, default settings.MODE = recursive): "recursive" feeds the meeting in chunks and updates a summary
so far after each, the way the live bot does; "full" gives the model the whole transcript at once.

Steps (see main() at the bottom):
  1. check the data     2. load the split     3. build the model
  4. summarize + score every meeting, once per seed     5. average over seeds, and record what changed between
     seeds (seed_variation: per-metric std/min/max with the seed, which meetings' summaries changed, and why)
  6. break the scores down by group (metrics_by_group: AMI by speakers' gender and native language,
     synthetic by feature tier and edge-case scenario; see 6_evaluation/summarization/disaggregate.py)
  7. save to the results log
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
import fetch_data  # noqa: E402
import settings  # noqa: E402
from datasets import DATASETS, SPLITS  # noqa: E402
from disaggregate import by_group, format_table, mean_over_seeds  # noqa: E402
from metrics import bertscore, rouge_all  # noqa: E402


# ---------------------------------------------------------------- seeds

# Which seeds to run: --seed S -> [S];  --seeds N -> N seeds;  nothing -> N_SEEDS seeds (1 for the LLM:
# every seed repeats every API call, and costs money, so more seeds must be asked for with --seeds).
def choose_seeds(args):
    if args.seed is not None:
        return [args.seed]
    if args.seeds is None and args.model == "llm":
        return [FIRST_SEED]
    n = N_SEEDS if args.seeds is None else args.seeds
    if n < 1:
        sys.exit("--seeds must be at least 1")
    return list(range(FIRST_SEED, FIRST_SEED + n))


# Python's string hashing has its own seed (PYTHONHASHSEED). It can only be set before Python starts,
# so if it isn't set yet, set it and restart this script once.
def fix_hash_seed(seed):
    if os.environ.get("PYTHONHASHSEED") != str(seed):
        os.environ["PYTHONHASHSEED"] = str(seed)
        # A child process, not os.execv: on Windows execv splits paths with spaces ("Capstone Project").
        sys.exit(subprocess.call([sys.executable] + sys.argv))


# Fix every random number generator: Python, NumPy, and torch (only installed for BART).
def set_seeds(seed):
    random.seed(seed)
    np.random.seed(seed)
    try:
        import torch
        torch.manual_seed(seed)
    except ImportError:
        pass


# ---------------------------------------------------------------- model

# Settings that override settings.py for this run (--params FILE): a JSON file from the hyperparameter search
# (2_summarization/hpo/), {"recursive": {...}, "full": {...}}; the entry for this run's mode is used.
def load_params(path, mode):
    if not path:
        return {}
    with open(path, encoding="utf-8") as f:
        params = json.load(f)
    return params.get(mode, {})


# Build the model and return it with the exact settings it uses (these go into the log). Summary lengths come
# from the dataset's TRAINING split (no leakage). mode: "recursive" or "full" (see the docstring at the top).
# params: overrides from --params (they end up in the logged config too).
def make_model(model_name, dataset, mode, no_wait=False, params=None):
    params = params or {}
    if model_name == "textrank":
        from textrank_baseline import TextRankSummarizer
        cfg = dict(settings.TEXTRANK, mode=mode, **params)
        # words_scale (from the search): the budget is that multiple of the train split's average summary length
        cfg["words"] = cfg["words"] or round(cfg.get("words_scale", 1.0) * dataset.train_summary_words())
        return TextRankSummarizer(cfg["words"], cfg["min_line_words"], mode, cfg["chunk_seconds"], cfg["buffer"],
                                  cfg.get("clean", False), cfg.get("speaker_names", False)), cfg
    if model_name == "llm":
        from llm_summarizer import LLMSummarizer
        cfg = dict(settings.LLM, mode=mode, **params)
        cfg["summary_words"] = cfg["summary_words"] or dataset.train_summary_words()
        if mode == "full":
            cfg["chunk_seconds"] = 10 ** 9   # one chunk: the whole transcript in one call
        if no_wait:
            cfg["min_seconds_between_calls"] = 0
        return LLMSummarizer(cfg, speaker_names=ami.ROLE_NAMES), cfg
    from bart_summarizer import BartSummarizer
    cfg = dict(settings.BART, mode=mode, **params)
    return BartSummarizer(cfg), cfg


# Save every LLM call to a file as soon as it returns (results dir / llm_cache.jsonl), keyed by everything that
# goes into the call. A run that stops (rate limit, daily quota, crash) is simply started again: calls already in
# the file are read back instead of paid for again, so it resumes where it stopped, even in the middle of a meeting.
# The file also keeps every intermediate set of notes. The live bot does not use this.
def add_llm_cache(model, path):
    provider = model.provider
    cache = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:   # a line cut off by a crash mid-write: ignore it
                    continue
                cache[entry["key"]] = entry["reply"]
    provider.usage["cache_hits"] = 0
    original = provider.generate_json

    def cached(system, user, schema, max_tokens):
        key = hashlib.sha256(json.dumps([provider.name, provider.model, system, user, schema, max_tokens])
                             .encode()).hexdigest()
        if key in cache:
            provider.usage["cache_hits"] += 1
            return cache[key]
        reply = original(system, user, schema, max_tokens)
        cache[key] = reply
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"key": key, "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                "model": provider.model, "user": user, "reply": reply}) + "\n")
        return reply

    provider.generate_json = cached
    print(f"LLM calls saved to {os.path.relpath(path, REPO)} ({len(cache)} already there, reused instead of re-asked)")


# ---------------------------------------------------------------- run and score

# One seed: summarize every meeting, score each summary, average over meetings.
def run_one_seed(seed, model, meetings, with_bertscore):
    set_seeds(seed)
    start = time.perf_counter()
    predictions, action_items = {}, {}
    for m in meetings:
        predictions[m.id] = model.summarize(m.lines)
        if getattr(model, "last_notes", None):   # the LLM also writes action items (logged, not scored yet)
            action_items[m.id] = [vars(a) for a in model.last_notes.action_items]
    seconds = time.perf_counter() - start

    per_meeting = {m.id: rouge_all(m.reference, predictions[m.id]) for m in meetings}
    if with_bertscore:   # kept per meeting too, so the group breakdown includes it
        f1 = bertscore([m.reference for m in meetings], [predictions[m.id] for m in meetings], settings.BERTSCORE)
        for m, f in zip(meetings, f1):
            per_meeting[m.id]["bertscore_f1"] = f
    scores = {name: round(float(np.mean([s[name] for s in per_meeting.values()])), 4)
              for name in per_meeting[meetings[0].id]}
    out = {"scores": scores, "per_meeting": per_meeting, "predictions": predictions, "seconds": seconds}
    if action_items:
        out["action_items"] = action_items
    return out


# Mean and standard deviation of each score across seeds.
def average_over_seeds(results):
    names = list(results[0]["scores"])
    mean = {n: round(float(np.mean([r["scores"][n] for r in results])), 4) for n in names}
    std = {n: round(float(np.std([r["scores"][n] for r in results], ddof=1)), 4) if len(results) > 1 else 0.0
           for n in names}
    return mean, std


# ---------------------------------------------------------------- what changed between seeds

# What the seed can and cannot change, per model. Goes into the log next to the measured variation,
# so "why did / didn't the numbers move between seeds?" is answered by the run record itself.
SEED_EFFECT = {
    "textrank": "TextRank (summa) has no random step: PageRank over a sentence-similarity graph, fixed input order. "
                "The seed cannot change the summary, so every seed should give the same text and std 0.",
    "bart": "BART decodes with beam search (do_sample=False), so on the same hardware it is deterministic too. "
            "Differences between seeds can only come from nondeterministic float math (e.g. GPU kernels, "
            "thread scheduling), which can flip a beam choice and change a few words.",
    "llm": "A hosted LLM cannot be seeded: our seeds only reach local random generators, and the provider's sampling "
           "(temperature etc.) is fixed for this model. So every run can give different text, and the spread across "
           "seeds measures the model's own run-to-run variation. Each seed repeats every API call.",
}
SEEDED = "Each seed sets Python random, NumPy and torch. PYTHONHASHSEED is fixed to the FIRST seed for the whole run."


# For every metric: spread across seeds, and the seeds that gave the lowest / highest score.
def metric_spread(seeds, results, mean, std):
    out = {}
    for name in mean:
        values = [r["scores"][name] for r in results]
        lo, hi = int(np.argmin(values)), int(np.argmax(values))
        out[name] = {"mean": mean[name], "std": std[name], "min": values[lo], "min_seed": seeds[lo],
                     "max": values[hi], "max_seed": seeds[hi], "range": round(values[hi] - values[lo], 4)}
    return out


# For every meeting: did the summary text change between seeds, how many different summaries, and the
# std of each metric across seeds. Only meetings that changed are listed in detail.
def per_meeting_changes(seeds, results, meeting_ids):
    changed = {}
    for m in meeting_ids:
        texts = [r["predictions"][m] for r in results]
        if len(set(texts)) == 1:
            continue
        scores = [r["per_meeting"][m] for r in results]
        changed[m] = {
            "distinct_summaries": len(set(texts)),
            "seeds_differing_from_first": [s for s, t in zip(seeds, texts) if t != texts[0]],
            "std": {n: round(float(np.std([s[n] for s in scores], ddof=1)), 4) for n in scores[0]},
            "range": {n: round(float(max(s[n] for s in scores) - min(s[n] for s in scores)), 4) for n in scores[0]},
        }
    return changed


# The full "what changed between seeds" section of the run record.
def seed_variation(model_name, seeds, results, mean, std, meeting_ids):
    changed = per_meeting_changes(seeds, results, meeting_ids)
    if len(seeds) == 1:
        verdict = "Only one seed was run, so there is no seed variation to measure."
    elif not changed:
        verdict = (f"Nothing changed: all {len(seeds)} seeds produced identical summaries for all "
                   f"{len(meeting_ids)} meetings, so every metric has std 0.")
    else:
        moved = [n for n in std if std[n] > 0]
        verdict = (f"{len(changed)} of {len(meeting_ids)} meetings got a different summary under some seed "
                   f"({', '.join(sorted(changed))}); metrics that moved: {', '.join(moved) or 'none (changes were too small to show at 4 decimals)'}.")
    return {
        "verdict": verdict,
        "why": SEED_EFFECT.get(model_name, "") + " " + SEEDED,
        "seeds": seeds,
        "metrics": metric_spread(seeds, results, mean, std),
        "meetings_changed": len(changed),
        "meetings_total": len(meeting_ids),
        "per_meeting": changed,
    }


# ---------------------------------------------------------------- results log

# Current git commit, and files changed but not committed (results/ is ignored, since runs write there).
def git_state():
    def git(*cmd):
        return subprocess.run(["git", *cmd], cwd=REPO, capture_output=True, text=True).stdout
    changed = [line[3:] for line in git("status", "--porcelain").splitlines()   # line = "XY path"
               if not line[3:].startswith(("6_evaluation/summarization/results/", "2_summarization/hpo/results/"))]
    return git("rev-parse", "HEAD").strip() or "unknown", changed


# Short fingerprint of a value, so two runs with the same config have the same config_hash.
def short_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:12]


# Versions of the packages that matter (packages not installed are left out).
def package_versions():
    out = {}
    for name in ["summa", "rouge-score", "numpy", "torch", "transformers", "bert-score", "anthropic"]:
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
    p.add_argument("--model", choices=["textrank", "bart", "llm"], default="textrank")
    p.add_argument("--dataset", choices=["both"] + sorted(DATASETS), default="both",
                   help="both (default: AMI, then synthetic, each logged as its own run), ami, or synthetic "
                        "(5_data/synthetic, LLM-generated edge-case meetings)")
    p.add_argument("--split", choices=SPLITS, default="smoke",
                   help="smoke = 3 dev meetings; eval = the test split (synthetic: its 'test' series)")
    p.add_argument("--mode", choices=["recursive", "full"], default=settings.MODE,
                   help=f"recursive = rolling summary chunk by chunk, like the live bot; full = whole transcript at once "
                        f"(default {settings.MODE})")
    seed_options = p.add_mutually_exclusive_group()
    seed_options.add_argument("--seeds", type=int, help=f"number of seeds (default {N_SEEDS})")
    seed_options.add_argument("--seed", type=int, help="run exactly one seed, with this value")
    p.add_argument("--bertscore", action="store_true", help="also compute BERTScore (BART packages are in requirements.txt)")
    p.add_argument("--no-log", action="store_true", help="don't save the result")
    p.add_argument("--results-dir", help="log to this folder (results_log.jsonl + runs/) instead of "
                                         "6_evaluation/summarization/results, e.g. the hyperparameter search's own folder")
    p.add_argument("--params", help="JSON file of tuned settings that override settings.py for this run "
                                    "(e.g. 2_summarization/hpo/results/textrank_best.json)")
    p.add_argument("--tag", help="word put in the run id after the model name (default: 'tuned' with --params), "
                                 "e.g. --tag 120s for an LLM run with longer chunks")
    p.add_argument("--no-wait", action="store_true",
                   help="LLM: don't space API calls out (settings.LLM min_seconds_between_calls); may hit rate limits")
    args = p.parse_args()

    seeds = choose_seeds(args)
    fix_hash_seed(seeds[0])
    if args.results_dir:
        settings.RESULTS_DIR = os.path.abspath(args.results_dir)
        settings.RESULTS_LOG = os.path.join(settings.RESULTS_DIR, "results_log.jsonl")
    names = sorted(DATASETS) if args.dataset == "both" else [args.dataset]
    for i, name in enumerate(names):
        if len(names) > 1:
            print(f"\n{'=' * 30} dataset {i + 1} of {len(names)}: {name} {'=' * 30}")
        run_dataset(args, seeds, name)


# Steps 1-7 for one dataset: its own model (summary lengths come from its own train split) and its own log line.
def run_dataset(args, seeds, dataset_name):
    # 1. Check the data is exactly the version in its manifest (AMI: downloaded first if it's missing).
    # 2. Load the committed split (AMI: also checks no person is in two splits).
    dataset = DATASETS[dataset_name]()
    meetings = dataset.meetings(args.split)
    print(f"{dataset.name}: data OK, split OK; {args.split}: {len(meetings)} meetings; mode {args.mode}")
    print(f"seeds: {len(seeds)} ({seeds[0]}..{seeds[-1]})")

    # 3. Build the model.
    model, model_cfg = make_model(args.model, dataset, args.mode, args.no_wait, load_params(args.params, args.mode))
    if args.model == "llm":
        add_llm_cache(model, os.path.join(settings.RESULTS_DIR, "llm_cache.jsonl"))
    if model_cfg.get("min_seconds_between_calls"):
        calls = sum(int(m.lines[-1].start // model_cfg["chunk_seconds"]) + 1 for m in meetings if m.lines) * len(seeds)
        print(f"LLM pacing: ~{calls} calls, one per {model_cfg['min_seconds_between_calls']} s: about "
              f"{calls * model_cfg['min_seconds_between_calls'] / 60:.0f} min (--no-wait to go as fast as the API allows)")

    # 4. Summarize and score every meeting, once per seed.
    results = []
    for seed in seeds:
        r = run_one_seed(seed, model, meetings, args.bertscore)
        results.append(r)
        print(f"  seed {seed}: " + "  ".join(f"{k} {v:.4f}" for k, v in r["scores"].items()), flush=True)

    # 5. Average over seeds.
    mean, std = average_over_seeds(results)
    identical = all(r["predictions"] == results[0]["predictions"] for r in results)
    variation = seed_variation(args.model, seeds, results, mean, std, [m.id for m in meetings])
    seconds_per_meeting = round(sum(r["seconds"] for r in results) / (len(seeds) * len(meetings)), 3)

    # 6. The same metrics per group of meetings (each meeting's scores averaged over seeds first).
    per_meeting_mean = mean_over_seeds([r["per_meeting"] for r in results])
    groups = {m.id: m.groups for m in meetings}
    by_groups = by_group(per_meeting_mean, groups, dataset.group_names)

    # 7. Save the scores together with everything needed to reproduce them.
    commit, changed = git_state()
    config = {"model": model_cfg, "mode": args.mode, "seeds": seeds, "pythonhashseed": seeds[0],
              "bertscore": settings.BERTSCORE if args.bertscore else None}
    if args.params:
        config["params_file"] = os.path.relpath(args.params, REPO).replace("\\", "/")
    tag = args.tag if args.tag is not None else ("tuned" if args.params else "")
    record = {
        "run_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                  + f"_{args.model}{'_' + tag if tag else ''}_{dataset_name}_{args.split}_{args.mode}_{len(seeds)}seeds",
        "commit": commit, "uncommitted_changes": changed,
        "dataset": dataset_name, "mode": args.mode,
        "data_hash": dataset.data_hash, "split_file_sha256": fetch_data.sha256_file(dataset.split_file),
        "config": config, "config_hash": short_hash(config),
        "model": args.model, "model_version": model.version,
        "split": args.split, "meetings": [m.id for m in meetings],
        "meeting_groups": groups, "metrics_by_group": by_groups,
        "meetings_hash": short_hash([m.id for m in meetings]),
        "n_seeds": len(seeds), "metrics_mean": mean, "metrics_std": std,
        "metrics_per_seed": {str(s): r["scores"] for s, r in zip(seeds, results)},
        "identical_across_seeds": identical, "seed_variation": variation, "seconds_per_meeting": seconds_per_meeting,
        "python": platform.python_version(), "packages": package_versions(),
        "hardware": {"machine": platform.machine(), "system": platform.platform(), "cpus": os.cpu_count()},
    }
    if hasattr(model, "provider"):   # LLM: API calls, tokens (what it cost) and which model actually answered
        record["llm_usage"] = model.provider.usage
    if not args.no_log:
        save(record, results)

    print(f"\nmean over {len(seeds)} seeds:  " + "  ".join(f"{k} {mean[k]} (±{std[k]})" for k in mean))
    print(f"seconds_per_meeting {seconds_per_meeting}  identical_across_seeds {identical}")
    print("seed variation: " + variation["verdict"])
    for name, s in variation["metrics"].items():
        print(f"  {name:12} std {s['std']}  min {s['min']} (seed {s['min_seed']})  "
              f"max {s['max']} (seed {s['max_seed']})  range {s['range']}")
    print("\nscores by group (mean over seeds; n = meetings, ser = series, i.e. different groups of people):")
    for attribute, result in by_groups.items():
        print(format_table(attribute, result, list(mean)))
    print(f"{'not logged (--no-log)' if args.no_log else 'logged'} {record['run_id']}  commit {commit[:12]}  "
          f"data {dataset.data_hash[:12]}  config {record['config_hash']}")
    if changed:
        print(f"WARNING: {len(changed)} uncommitted file(s); commit them so this number traces to one commit: {changed}")


if __name__ == "__main__":
    main()
