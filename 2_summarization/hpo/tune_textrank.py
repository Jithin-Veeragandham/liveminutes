"""Hyperparameter search for TextRank, on the dev splits of both datasets (see common.py for the objective).

    python 2_summarization/hpo/tune_textrank.py              # full grid (full mode) + 160 random recursive trials
    python 2_summarization/hpo/tune_textrank.py --trials 40  # fewer recursive trials
    python 2_summarization/hpo/tune_textrank.py --scale 1.0  # summaries as long as the human ones:
        full grid in both modes with words_scale fixed at 1.0, best settings to results/textrank_best_capped.json

Search space:
  words_scale     summary budget as a multiple of the train split's average human summary (1.0 = today)
  min_line_words  drop lines shorter than this before ranking (4 = today)
  clean           drop fillers and stutters first (off today)
  speaker_names   prefix each line with the speaker's role (off today)
  recursive only: chunk_seconds (how often the summary is updated), buffer (how much extra is kept between chunks)

Every trial is appended to results/textrank_trials.jsonl (a rerun skips trials already there). The best settings
per mode go to results/textrank_best.json, which run.py reads with --params.
"""
import argparse
import itertools
import json
import os
import random
from concurrent.futures import ProcessPoolExecutor

from common import RESULTS, done, dev_sets, key, log, score, show

TRIALS = os.path.join(RESULTS, "textrank_trials.jsonl")
BEST = os.path.join(RESULTS, "textrank_best.json")
SPACE = {
    "words_scale": [0.6, 0.8, 1.0, 1.25, 1.5, 2.0],
    "min_line_words": [2, 4, 6, 8, 12, 16, 20, 25],
    "clean": [False, True],
    "speaker_names": [False, True],
}
RECURSIVE = {"chunk_seconds": [30, 60, 120, 180, 300], "buffer": [1.0, 1.5, 2.0, 3.0]}
DEFAULT = {"words_scale": 1.0, "min_line_words": 4, "clean": False, "speaker_names": False}

SETS = None


def init():
    global SETS
    SETS = dev_sets()


def trial(params):
    from textrank_baseline import TextRankSummarizer
    p = dict(params)
    mode = p.pop("mode")
    models = {name: TextRankSummarizer(round(p["words_scale"] * words), p["min_line_words"], mode,
                                       p.get("chunk_seconds", 30), p.get("buffer", 1.5), p["clean"], p["speaker_names"])
              for name, (_, words) in SETS.items()}
    return params, score({n: m.summarize for n, m in models.items()}, SETS)


def configs(n_recursive, seed=6170, scale=None):
    space = dict(SPACE)
    if scale is not None:
        space["words_scale"] = [scale]
    names = list(space)
    full = [dict(zip(names, v), mode="full") for v in itertools.product(*space.values())]
    rng = random.Random(seed)
    every = [dict(zip(names + list(RECURSIVE), v), mode="recursive")
             for v in itertools.product(*space.values(), *RECURSIVE.values())]
    default = [dict(DEFAULT, chunk_seconds=30, buffer=1.5, mode="recursive")]
    if scale is not None:   # the capped search is small enough to run the whole recursive grid
        return full + default + every
    return full + default + rng.sample(every, n_recursive)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--trials", type=int, default=160, help="random recursive-mode trials (full mode is a full grid)")
    p.add_argument("--scale", type=float, help="fix the summary length at this multiple of the human length "
                                                "(1.0 = as long as the average human summary)")
    p.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    args = p.parse_args()

    finished = done(TRIALS)
    todo = list({key(c): c for c in configs(args.trials, scale=args.scale) if key(c) not in finished}.values())
    print(f"{len(finished)} trials already done, {len(todo)} to run on {args.workers} processes", flush=True)
    with ProcessPoolExecutor(args.workers, initializer=init) as pool:
        for params, result in pool.map(trial, todo):
            log(TRIALS, params, result)
            finished[key(params)] = {"params": params, **result}
            show(params, result)

    best, report = {}, {}
    for mode in ("full", "recursive"):
        rows = [r for r in finished.values() if r["params"]["mode"] == mode
                and (args.scale is None or r["params"]["words_scale"] == args.scale)]
        top = max(rows, key=lambda r: r["objective"])
        default = next(r for r in rows if all(r["params"].get(k) == v for k, v in DEFAULT.items())
                       and (mode == "full" or (r["params"]["chunk_seconds"], r["params"]["buffer"]) == (30, 1.5)))
        best[mode] = {k: v for k, v in top["params"].items() if k != "mode"}
        report[mode] = {"best": top, "default": default, "trials": len(rows)}
        print(f"\n{mode}: {len(rows)} trials")
        show(default["params"], default, "  default  ")
        show(top["params"], top, "  best     ")
    suffix = "" if args.scale is None else "_capped"
    best_file = BEST.replace(".json", suffix + ".json")
    with open(best_file, "w") as f:
        json.dump(best, f, indent=2)
    with open(os.path.join(RESULTS, f"textrank_report{suffix}.json"), "w") as f:
        json.dump({"words_scale": args.scale, **report}, f, indent=2)
    print(f"\nwrote {best_file}")


if __name__ == "__main__":
    main()
