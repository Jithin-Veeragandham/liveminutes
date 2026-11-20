"""Score any action-item predictions file against AMI or the synthetic meetings, with breakdowns, and log the run.

    .venv/bin/python 6_evaluation/action_items/score.py --data ami --split eval --predictions my_llm.json --name llm
    .venv/bin/python 6_evaluation/action_items/score.py --data synthetic --split test --predictions my_llm.json --name llm

Predictions file (JSON), one entry per meeting id, items in the UI contract's format (docs/UI_CONTRACT.md):
  {"meetings": {"ES2004a": {"items": [{"text": "...", "owner": "Project Manager", "due": "", "source_line_ids": []}]}}}
Also accepted: {"ES2004a": [items]} and {"ES2004a": {"items": [...]}}; "task" instead of "text" (the LLM
summarizer's field name). Only text is required. owner: a display name, role, first name or label, ""
for unassigned. due: a date, a phrase ("by Friday", "next meeting"), or "" (a "due_text" field is read first).
source_line_ids are only needed for the synthetic edge-case breakdown. A meeting missing from the file counts as
no predictions.
3_action_items/run.py calls the same functions for the two baselines, so every system is scored the same way.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "3_action_items"))
import breakdowns  # noqa: E402
import data  # noqa: E402
import metrics  # noqa: E402
import settings  # noqa: E402
from device import pick_device  # noqa: E402


# ================= predictions =================

def normalize_item(item, n):
    if isinstance(item, str):
        item = {"text": item}
    text = item.get("text") or item.get("task") or item.get("description") or ""
    return {**item, "id": item.get("id") or f"a{n}", "text": str(text).strip(), "owner": item.get("owner") or "",
            "due": item.get("due") or "", "source_line_ids": list(item.get("source_line_ids") or [])}


def load_predictions(path):
    with open(path) as f:
        raw = json.load(f)
    raw = raw.get("meetings", raw) if isinstance(raw, dict) else raw
    out = {}
    for mid, entry in raw.items():
        items = entry.get("items", entry.get("action_items", [])) if isinstance(entry, dict) else entry
        out[mid] = [normalize_item(it, n) for n, it in enumerate(items, 1)]
        out[mid] = [it for it in out[mid] if it["text"]]
    return out


def write_predictions(path, system, info, predictions):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({"system": system, "data": info["source"], "split": info["split"],
                   "meetings": {mid: {"items": items} for mid, items in predictions.items()}}, f, indent=1)


# ================= scoring =================

def evaluate(meetings, predictions, embed, threshold):
    """Overall scores, breakdowns, and per-meeting details for one system on one split."""
    cfg = settings.MATCHING["bootstrap"]
    per, counts = {}, {}
    for m in meetings:
        preds = predictions.get(m.id, [])
        c = metrics.score_meeting(m, preds, embed, threshold)
        counts[m.id] = c
        per[m.id] = {"pred": c.pred, "gold": c.gold, "matched": c.tp, "owner_ok": c.owner_ok, "due_ok": c.due_ok,
                     "due_n": c.due_n, "groups": m.groups,
                     "pairs": [{"pred": preds[i]["text"], "pred_owner": preds[i]["owner"], "gold": m.gold[j].text,
                                "gold_owners": sorted(m.gold[j].owners), "similarity": round(s, 4),
                                "owner_ok": metrics.owner_key(preds[i]["owner"], m.people) in (m.gold[j].owners or {""})}
                               for i, j, s in c.pairs],
                     "unmatched_pred": [preds[i]["text"] for i in range(len(preds)) if i not in {p[0] for p in c.pairs}],
                     "unmatched_gold": [m.gold[j].text for j in range(len(m.gold)) if j not in {p[1] for p in c.pairs}]}
    overall = metrics.summarize(list(counts.values()), cfg["B"], 0, cfg["level"])
    groups = {}
    names = breakdowns.AMI_GROUPS if meetings and meetings[0].source == "ami" else breakdowns.SYNTHETIC_GROUPS
    for g in names:
        groups[g] = {}
        for value in sorted({m.groups[g] for m in meetings}):
            sub = [counts[m.id] for m in meetings if m.groups[g] == value]
            s = metrics.summarize(sub, cfg["B"], 0, cfg["level"])
            groups[g][value] = {**s, "too_small": s["n"] <= breakdowns.SMALL_N}
    if meetings and meetings[0].source == "synthetic":
        groups["edge_case"] = edge_case_breakdown(meetings, predictions, counts, cfg)
    return {"overall": overall, "groups": groups, "per_meeting": per}


def edge_case_breakdown(meetings, predictions, counts, cfg):
    by_type = {}
    for m in meetings:
        for scenario, region in m.edge_cases:
            c = metrics.region_counts(m, predictions.get(m.id, []), counts[m.id], region)
            by_type.setdefault(scenario, []).append(c)
    out = {}
    for scenario in sorted(by_type):
        regions = by_type[scenario]
        recall_tps = [dict(c.pairs)["recall_tp"] for c in regions]
        s = metrics.summarize(regions, cfg["B"], 0, cfg["level"], recall_tps=recall_tps)
        out[scenario] = {**s, "too_small": s["n"] <= breakdowns.SMALL_N}
    return out


# ================= results log =================

def git_state():
    def git(*cmd):
        return subprocess.run(["git", *cmd], cwd=REPO, capture_output=True, text=True).stdout
    changed = [line[3:] for line in git("status", "--porcelain").splitlines()
               if not line[3:].startswith("6_evaluation/action_items/results/")]
    return git("rev-parse", "HEAD").strip() or "unknown", changed


def short_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:12]


def sha256_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def package_versions():
    out = {}
    for name in ["numpy", "torch", "transformers", "tokenizers", "huggingface-hub", "safetensors"]:
        try:
            out[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    return out


def hardware(device):
    out = {"machine": platform.machine(), "system": platform.platform(), "cpus": os.cpu_count(), "device": device}
    if platform.system() == "Darwin":
        chip = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip()
        out["chip"] = chip or None
    return out


def record(system, info, config, result, predictions_path, timing, device, model_versions, run_id=None):
    commit, changed = git_state()
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"_{system}_{info['source']}_{info['split']}"
    return {
        "run_id": run_id, "commit": commit, "uncommitted_changes": changed,
        "system": system, "data": info["source"], "split": info["split"], "meetings": info["meetings"],
        "meetings_hash": short_hash(info["meetings"]),
        "data_hashes": {k: v for k, v in info.items() if k not in ("meetings", "source", "split")},
        "config": config, "config_hash": short_hash(config), "model_versions": model_versions,
        "metrics": result["overall"], "breakdowns": result["groups"], "timing": timing,
        "predictions_file": os.path.relpath(predictions_path, REPO) if predictions_path else None,
        "predictions_sha256": sha256_file(predictions_path) if predictions_path else None,
        "python": platform.python_version(), "packages": package_versions(), "hardware": hardware(device),
    }


def save(rec, result):
    os.makedirs(os.path.join(settings.RESULTS_DIR, "runs"), exist_ok=True)
    with open(os.path.join(settings.RESULTS_DIR, "runs", rec["run_id"] + ".json"), "w") as f:
        json.dump({**rec, "per_meeting": result["per_meeting"]}, f, indent=1)
    with open(settings.RESULTS_LOG, "a") as f:
        f.write(json.dumps(rec) + "\n")


# ================= printing =================

def fmt(s, key):
    v, ci = s.get(key), s.get(key + "_ci")
    if v is None:
        return "  n/a           "
    return f"{v:.3f} [{ci[0]:.2f}, {ci[1]:.2f}]" if ci else f"{v:.3f}"


def print_result(system, info, result):
    o = result["overall"]
    print(f"\n{system} on {info['source']} {info['split']} ({o['n']} meetings): predicted {o['pred']}, gold {o['gold']}, "
          f"matched {o['matched']}")
    print(f"  precision {fmt(o, 'precision')}  recall {fmt(o, 'recall')}  F1 {fmt(o, 'f1')}")
    print(f"  owner accuracy {fmt(o, 'owner_acc')} (n={o['owner_n']})   due accuracy {fmt(o, 'due_acc')} (n={o['due_n']})")
    for g, values in result["groups"].items():
        print(f"  by {g}:")
        for value, s in values.items():
            flag = "  (too small to read)" if s["too_small"] else ""
            print(f"    {value:28} n={s['n']:<3} gold {s['gold']:<3} pred {s['pred']:<4} F1 {fmt(s, 'f1')}  "
                  f"owner {fmt(s, 'owner_acc')}{flag}")


def score_and_log(system, meetings, info, predictions, config, model_versions, timing, predictions_path,
                  no_log=False, device=None, run_id=None):
    device = device or pick_device(settings.ZEROSHOT["device"])
    embed = metrics.Embedder(settings.MATCHING, settings.CACHE_DIR, device)
    result = evaluate(meetings, predictions, embed, settings.MATCHING["threshold"])
    model_versions = {**model_versions, "embedder": f"{settings.MATCHING['embedder']}@{settings.MATCHING['revision']}"}
    config = {**config, "matching": settings.MATCHING}
    rec = record(system, info, config, result, predictions_path, timing, device, model_versions, run_id)
    print_result(system, info, result)
    if not no_log:
        save(rec, result)
    commit, changed = rec["commit"], rec["uncommitted_changes"]
    print(f"{'not logged (--no-log)' if no_log else 'logged'} {rec['run_id']}  commit {commit[:12]}  "
          f"data {info['data_hash'][:12]}  config {rec['config_hash']}")
    if changed:
        print(f"WARNING: {len(changed)} uncommitted file(s); commit them so this number traces to one commit: {changed}")
    return rec, result


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--data", choices=["ami", "synthetic"], required=True)
    p.add_argument("--split", choices=["smoke", "dev", "eval", "test"], required=True)
    p.add_argument("--predictions", required=True, help="JSON file in the contract format (see the top of this file)")
    p.add_argument("--name", default="external", help="system name for the log, e.g. llm")
    p.add_argument("--no-log", action="store_true")
    a = p.parse_args()
    meetings, info = data.load(a.data, a.split)
    predictions = load_predictions(a.predictions)
    missing = [m.id for m in meetings if m.id not in predictions]
    extra = sorted(set(predictions) - {m.id for m in meetings})
    if missing:
        print(f"WARNING: no predictions for {len(missing)} meeting(s), scored as none: {missing}")
    if extra:
        print(f"note: {len(extra)} meeting(s) in the file are not in this split and are ignored")
    score_and_log(a.name, meetings, info, predictions, {"system": a.name}, {}, {}, os.path.abspath(a.predictions),
                  a.no_log)


if __name__ == "__main__":
    main()
