"""Hyperparameter search for BART, on a dev subset of both datasets (see common.py for the objective).

BART takes ~1-3 min per AMI meeting on a CPU, so this is a staged search on the first meeting of every dev
series (5 AMI + 5 synthetic meetings), not a grid:
  full       A: window size x each window's summary length;  B: the final summary's length, around the best A;
             C: length_penalty 2.0 on the best so far
  recursive  A: window size;  B: rolling-summary lengths around the best A
Every BART generation is cached (cache/bart_generations.jsonl, keyed by input text + settings), so trials that
share windows don't repeat them and an interrupted search resumes where it stopped.

    python 2_summarization/hpo/tune_bart.py                  # both modes (several hours on a CPU)
    python 2_summarization/hpo/tune_bart.py --mode full      # one mode

Trials go to results/bart_trials.jsonl, the best settings per mode to results/bart_best.json (run.py --params).
"""
import argparse
import hashlib
import json
import os

from common import HERE, RESULTS, done, dev_sets, key, log, score, show

TRIALS = os.path.join(RESULTS, "bart_trials.jsonl")
BEST = os.path.join(RESULTS, "bart_best.json")
CACHE = os.path.join(HERE, "cache", "bart_generations.jsonl")

STAGES = {
    "full": [
        ("A", lambda best: [{"window_seconds": w, "window_max_tokens": t} for w in (60, 120, 180, 240) for t in (60, 100, 140)]),
        ("B", lambda best: [dict(best, final_max_tokens=mx, final_min_tokens=mn)
                            for mx in (150, 220, 300) for mn in (30, 60, 120) if mn < mx]),
        ("C", lambda best: [dict(best, length_penalty=2.0)]),
    ],
    "recursive": [
        ("A", lambda best: [{"window_seconds": w} for w in (60, 120, 180)]),
        ("B", lambda best: [dict(best, rec_max_tokens=mx, floor_ratio=r, floor_cap=c, length_penalty=lp)
                            for mx, r, c, lp in [(300, 0.8, 120, 1.0), (300, 1.0, 150, 1.0), (150, 0.8, 60, 1.0),
                                                 (220, 0.8, 60, 2.0)]]),
    ],
}


# Wrap the model's generate step with a disk cache keyed by (text, lengths, beams, length_penalty).
def add_cache(model):
    cache = {}
    if os.path.exists(CACHE):
        with open(CACHE, encoding="utf-8") as f:
            for line in f:
                k, v = json.loads(line)
                cache[k] = v
    original = model.summarize_text
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)

    def cached(text, max_new_tokens=80, min_new_tokens=10):
        k = hashlib.sha256(json.dumps([text, max_new_tokens, min(min_new_tokens, max_new_tokens),
                                       model.cfg["num_beams"], model.cfg["length_penalty"]]).encode()).hexdigest()
        if k not in cache:
            cache[k] = original(text, max_new_tokens, min_new_tokens)
            with open(CACHE, "a", encoding="utf-8") as f:
                f.write(json.dumps([k, cache[k]]) + "\n")
        return cache[k]

    model.summarize_text = cached
    return model


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["full", "recursive", "both"], default="both")
    args = p.parse_args()

    import settings
    from bart_summarizer import DEFAULTS, BartSummarizer
    sets = dev_sets(subset=True)
    print("dev subset: " + ", ".join(f"{n}: {[m.id for m in ms]}" for n, (ms, _) in sets.items()), flush=True)
    model = add_cache(BartSummarizer(dict(settings.BART)))
    base = {**DEFAULTS, **settings.BART}
    finished = done(TRIALS)
    best_all = json.load(open(BEST)) if os.path.exists(BEST) else {}

    for mode in (["full", "recursive"] if args.mode == "both" else [args.mode]):
        def run(params):
            trial = dict(params, mode=mode)
            if key(trial) in finished:
                return finished[key(trial)]
            model.cfg = {**base, **params, "mode": mode}
            model.mode = mode
            result = score({n: model.summarize for n in sets}, sets, keep_summaries=True)
            log(TRIALS, trial, result, subset=True)
            finished[key(trial)] = {"params": trial, **result}
            show(trial, result)
            return finished[key(trial)]

        best = run({})   # today's settings, the reference point
        print(f"\n{mode}: default objective {best['objective']}", flush=True)
        for stage, make in STAGES[mode]:
            current = {k: v for k, v in best["params"].items() if k != "mode"}
            for params in make(current):
                r = run(params)
                if r["objective"] > best["objective"]:
                    best = r
            print(f"{mode} after stage {stage}: best {best['objective']}  {best['params']}", flush=True)
        best_all[mode] = {k: v for k, v in best["params"].items() if k != "mode"}
        with open(BEST, "w") as f:
            json.dump(best_all, f, indent=2)
    print(f"wrote {BEST}: {best_all}")


if __name__ == "__main__":
    main()
