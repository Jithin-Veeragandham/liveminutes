"""Write a split's transcripts in the UI contract's line format, so another system (e.g. the LLM) reads exactly the
lines the baselines read, with the same line ids for source_line_ids.

    .venv/bin/python 6_evaluation/action_items/export_meetings.py --data ami --split eval --out 5_data/processed/ami_eval.json
    .venv/bin/python 6_evaluation/action_items/export_meetings.py --data synthetic --split test --out 5_data/processed/synthetic_test.json

Output: {"data": ..., "split": ..., "data_hash": ..., "meetings": {"<id>": {"people": ["Project Manager", ...],
         "lines": [{"id": "t12345", "speaker": "...", "start": 12.3, "end": 12.3, "text": "..."}, ...]}}}
Gold items are NOT written. 5_data/processed/ is gitignored: keep transcripts out of git.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import data  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data", choices=["ami", "synthetic"], required=True)
    p.add_argument("--split", choices=["smoke", "dev", "eval", "test"], required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    meetings, info = data.load(a.data, a.split)
    out = {"data": a.data, "split": a.split, "data_hash": info["data_hash"],
           "meetings": {m.id: {"people": m.names(), "lines": m.lines} for m in meetings}}
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w") as f:
        json.dump(out, f)
    print(f"wrote {len(meetings)} meetings to {a.out}")


if __name__ == "__main__":
    main()
