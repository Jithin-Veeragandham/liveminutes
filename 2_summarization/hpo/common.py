"""Shared by the searches: the dev meetings of both datasets, scoring, and the trial log.

Searches only ever see the DEV splits. The test ("eval") splits are scored once, at the end, with run.py
--params <best file>, so the reported test numbers are not tuned on.

Objective: for each dataset, the mean over its dev meetings of (ROUGE-1 + ROUGE-2 + ROUGE-L) / 3; then the mean
of the two datasets, so AMI and synthetic count equally (AMI meetings are 4x longer, but neither should dominate).
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
for folder in ["2_summarization", "5_data/ami", "6_evaluation/summarization"]:
    path = os.path.join(REPO, folder)
    if path not in sys.path:
        sys.path.insert(0, path)

import numpy as np  # noqa: E402
from datasets import DATASETS  # noqa: E402
from disaggregate import by_group  # noqa: E402
from metrics import rouge_all  # noqa: E402

RESULTS = os.path.join(HERE, "results")
METRICS = ["rouge1", "rouge2", "rougeL"]


# {dataset name: (dev meetings, train-split average summary words)}. subset=True: only the first meeting of
# each series (5 per dataset), for the slow BART search.
def dev_sets(subset=False):
    out = {}
    for name, cls in sorted(DATASETS.items()):
        d = cls()
        meetings = d.meetings("dev")
        if subset:
            seen = set()
            meetings = [m for m in meetings if not (m.id[:-1] in seen or seen.add(m.id[:-1]))]
        out[name] = (meetings, d.train_summary_words())
    return out


# Score a summarize function on every dataset. Everything is kept, so no trial has to be rerun to look at it:
# per dataset the metric means, every meeting's scores (and summary word count), and the scores per group
# (AMI: gender / non-native mix; synthetic: feature tier / scenario); plus the objective. keep_summaries=True
# also stores each summary (BART: few, slow trials; TextRank's are cheap to recompute from the params).
def score(summarize_by_dataset, sets, keep_summaries=False):
    result, start = {}, time.perf_counter()
    for name, (meetings, _) in sets.items():
        summarize = summarize_by_dataset[name]
        summaries = {m.id: summarize(m.lines) for m in meetings}
        per = {m.id: {k: round(v, 4) for k, v in rouge_all(m.reference, summaries[m.id]).items()} for m in meetings}
        for m in meetings:
            per[m.id]["words"] = len(summaries[m.id].split())
        means = {k: round(float(np.mean([s[k] for s in per.values()])), 4) for k in METRICS + ["rougeSU4", "words"]}
        means["avg"] = round(float(np.mean([means[k] for k in METRICS])), 4)
        metric_only = {m: {k: v for k, v in s.items() if k != "words"} for m, s in per.items()}
        groups = by_group(metric_only, {m.id: m.groups for m in meetings}, DATASETS[name].group_names)
        result[name] = {**means, "per_meeting": per,
                        "by_group": {a: {v: {"n": t["n"], **t["mean"]} for v, t in g["groups"].items()}
                                     for a, g in groups.items()},
                        "group_gap": {a: g["gap"] for a, g in groups.items()}}
        if keep_summaries:
            result[name]["summaries"] = summaries
    result["objective"] = round(float(np.mean([result[n]["avg"] for n in sets])), 4)
    result["seconds"] = round(time.perf_counter() - start, 1)
    return result


def git_commit():
    return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()


def key(params):
    return json.dumps(params, sort_keys=True)


# Trials already in a log (so an interrupted search resumes instead of starting over).
def done(path):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return {key(r["params"]): r for r in map(json.loads, f) if r.get("params") is not None}


def log(path, params, result, **extra):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"params": params, **result, "commit": git_commit(), **extra}) + "\n")


def show(params, r, prefix=""):
    print(f"{prefix}objective {r['objective']:.4f}  ami {r['ami']['avg']:.4f} (R1 {r['ami']['rouge1']:.3f})  "
          f"synthetic {r['synthetic']['avg']:.4f} (R1 {r['synthetic']['rouge1']:.3f})  {r['seconds']:>6}s  {params}",
          flush=True)
