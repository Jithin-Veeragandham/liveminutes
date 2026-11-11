"""The train / dev / eval split: AMI's official scenario-only partition, split by team.

    python 5_data/ami/split.py          # rebuild ami_scenario_split.json, run the leakage check, record its hashes in MANIFEST.json
    python 5_data/ami/split.py --check  # check the committed split file against split.py and MANIFEST.json (run.py does this every run)

AMI meetings come in series of four (a-d) recorded by the same team of 4 people. The split is made by
SERIES, so a team's whole series, and so every person in it, stays in one split. 25 / 5 / 5 teams.
"""
import argparse
import glob
import json
import os
import re
import sys
import hashlib
import xml.etree.ElementTree as ET

import ami_settings
import fetch_data

# >>> THE SPLIT IS MADE HERE. AMI's official scenario-only partition (AMI corpus website, "Scenario-only"):
DEV_SERIES = ["ES2003", "ES2011", "IS1008", "TS3004", "TS3006"]
EVAL_SERIES = ["ES2004", "ES2014", "IS1009", "TS3003", "TS3007"]
# Every other scenario series (ES/IS/TS) is training. No randomness, so no split seed is needed.


# Return every scenario meeting id that has a transcript, e.g. ["ES2002a", ...] (non-scenario meetings excluded).
def scenario_meetings():
    files = glob.glob(os.path.join(ami_settings.AMI_DIR, "words", "*.words.xml"))
    ids = {os.path.basename(f).split(".")[0] for f in files}
    return sorted(m for m in ids if re.match(r"^(ES|IS|TS)\d{4}[a-d]$", m))


# Assign each meeting to train / dev / eval by its series (first 6 characters, e.g. "ES2004").
def build_split():
    split = {"train": [], "dev": [], "eval": []}
    for m in scenario_meetings():
        name = "dev" if m[:6] in DEV_SERIES else "eval" if m[:6] in EVAL_SERIES else "train"
        split[name].append(m)
    return split


# Map each meeting to the participant ids (e.g. "MEE006") who spoke in it.
def participants_by_meeting():
    root = ET.parse(os.path.join(ami_settings.AMI_DIR, "corpusResources", "meetings.xml")).getroot()
    return {m.get("observation"): {s.get("global_name") for s in m.iter("speaker")} for m in root.iter("meeting")}


# Stop with an error if any person appears in two splits (leakage by person) or a meeting is in two splits.
def check_no_leakage(split):
    people = participants_by_meeting()
    seen = {name: {p for m in meetings for p in people[m]} for name, meetings in split.items()}
    for a, b in [("train", "dev"), ("train", "eval"), ("dev", "eval")]:
        if set(split[a]) & set(split[b]):
            sys.exit(f"LEAKAGE: meetings in both {a} and {b}: {sorted(set(split[a]) & set(split[b]))}")
        if seen[a] & seen[b]:
            sys.exit(f"LEAKAGE: participants in both {a} and {b}: {sorted(seen[a] & seen[b])}")
    return {name: len(p) for name, p in seen.items()}


# Repo-relative path with "/" on every OS (what the manifest stores).
def rel(path):
    return os.path.relpath(path, ami_settings.REPO).replace(os.sep, "/")


# SHA-256 of each split's sorted meeting list. Unlike the file hash, it ignores formatting and the other
# fields in the file, so "the eval set did not change" can be checked on its own.
def list_hashes(split):
    return {name: hashlib.sha256(json.dumps(sorted(ids)).encode()).hexdigest() for name, ids in split.items()}


# The "split" block of MANIFEST.json: where the split is defined and saved, and its hashes.
def manifest_entry(split):
    return {"rule": rel(os.path.join(ami_settings.HERE, "split.py")),
            "file": rel(ami_settings.SPLIT_FILE),
            "file_sha256": fetch_data.sha256_file(ami_settings.SPLIT_FILE),
            "counts": {k: len(v) for k, v in split.items()},
            "list_sha256": list_hashes(split),
            "list_sha256_method": "sha256 of json.dumps(sorted(meeting ids)) per split (split.py: list_hashes)"}


# Stop if the split file or its meeting lists don't match the hashes recorded in MANIFEST.json.
def check_manifest(split):
    m = fetch_data.load_manifest().get("split")
    if m is None:
        sys.exit("MANIFEST.json has no split entry; run  python 5_data/ami/split.py  and commit the result")
    got = manifest_entry(split)
    for key in ("file", "file_sha256", "list_sha256"):
        if got[key] != m[key]:
            sys.exit(f"split {key} does not match MANIFEST.json: expected {m[key]}, got {got[key]}. "
                     "If the split was changed on purpose, run  python 5_data/ami/split.py  and commit the result")


# Load the committed split file and confirm it matches the rule above, the manifest, and has no leakage.
def load_split():
    with open(ami_settings.SPLIT_FILE) as f:
        split = json.load(f)["split"]
    if split != build_split():
        sys.exit("split file does not match split.py; run  python 5_data/ami/split.py  and commit the result")
    check_manifest(split)
    check_no_leakage(split)
    return split


# Rebuild the split file from the series lists and write it.
def write_split():
    split = build_split()
    people = check_no_leakage(split)
    out = {"source": "AMI official scenario-only partition, split by series (team)",
           "dev_series": DEV_SERIES, "eval_series": EVAL_SERIES,
           "counts": {k: len(v) for k, v in split.items()}, "participants": people,
           "smoke": ami_settings.SMOKE_MEETINGS, "split": split}
    with open(ami_settings.SPLIT_FILE, "w") as f:
        json.dump(out, f, indent=2)
    print(f"wrote {ami_settings.SPLIT_FILE}: {out['counts']} meetings, {people} people, no overlap")
    # Record the split's paths and hashes in MANIFEST.json (the other fields are kept as they are).
    manifest = fetch_data.load_manifest()
    manifest["split"] = manifest_entry(split)
    with open(ami_settings.MANIFEST, "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    print(f"updated {ami_settings.MANIFEST}: split file_sha256 {manifest['split']['file_sha256'][:12]}...")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--check", action="store_true")
    if p.parse_args().check:
        s = load_split()
        print("split OK:", {k: len(v) for k, v in s.items()}, "meetings, no person in two splits")
    else:
        write_split()
