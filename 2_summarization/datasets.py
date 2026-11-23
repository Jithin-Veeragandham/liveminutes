"""The datasets run.py can score a summarizer on. Each gives meetings in the same shape (ami.Meeting: id, lines,
reference summary, groups), so the models and metrics never know which dataset they are reading.

  ami        AMI scenario meetings, the committed speaker-disjoint split (5_data/ami). Groups: the speakers'
             gender mix and how many are non-native English speakers (participants.xml).
  synthetic  LLM-generated meetings with planned edge cases (5_data/synthetic), its own committed split.
             Groups: the feature tier the meeting was made for (F1 live summary, F2 action items, F3 Q&A)
             and the edge-case scenarios in it (scenarios.yaml; a meeting has two, so it counts in both).

Split names are the same for both: smoke (3 dev meetings), dev, eval (synthetic calls it "test").
"""
import json
import os

import numpy as np

import ami
import ami_settings
import fetch_data
import split

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYNTHETIC_DIR = os.path.join(REPO, "5_data", "synthetic")
SYNTHETIC_DATA = os.path.join(SYNTHETIC_DIR, "data", "raw")   # the built dataset; a reviewed release would be data/reviewed/vN
SYNTHETIC_SPLIT = os.path.join(SYNTHETIC_DIR, "data", "splits", "synthetic_splits.json")
SPLITS = ["smoke", "dev", "eval"]


class AMI:
    name = "ami"
    group_names = ["gender_mix", "non_native"]

    def __init__(self):
        # The data must be exactly the version in MANIFEST.json (downloaded first if it's missing).
        self.data_hash = fetch_data.verify() if os.path.isdir(ami_settings.AMI_DIR) else fetch_data.fetch()
        self.split_file = ami_settings.SPLIT_FILE
        self.split = split.load_split()   # also checks no person is in two splits

    # The meetings to summarize. Meetings without a human summary can't be scored, so they are skipped.
    def meetings(self, split_name):
        ids = ami_settings.SMOKE_MEETINGS if split_name == "smoke" else self.split[split_name]
        meetings = [ami.load_meeting(m) for m in ids]
        skipped = [m.id for m in meetings if not m.reference]
        if skipped:
            print(f"no human summary, skipped: {skipped}")
        return [m for m in meetings if m.reference]

    # Average length in words of the TRAINING split's human summaries (train only: no leakage).
    def train_summary_words(self):
        lengths = [len(ami.read_reference(m).split()) for m in self.split["train"]]
        return round(np.mean([n for n in lengths if n]))


class Synthetic:
    name = "synthetic"
    group_names = ["feature", "scenario"]

    def __init__(self, data_dir=SYNTHETIC_DATA, split_file=SYNTHETIC_SPLIT):
        self.data_dir, self.split_file = data_dir, split_file
        self.data_hash = self.verify()
        with open(split_file, encoding="utf-8") as f:
            series = json.load(f)["series"]
        self.split = {name: [m for s in series.values() if s["split"] == name for m in s["meetings"]]
                      for name in ("train", "dev", "test")}
        self.split["eval"] = self.split["test"]
        # smoke: the first meeting of the first dev series of each feature (F1, F2, F3), like AMI's one per site
        firsts = {}
        for s in series.values():
            if s["split"] == "dev":
                firsts.setdefault(s["feature"], s["meetings"][0])
        self.split["smoke"] = [firsts[f] for f in sorted(firsts)]

    # Every file must match the sha256 in the dataset's manifest.json; the data hash is the manifest's own sha256.
    def verify(self):
        manifest_path = os.path.join(self.data_dir, "manifest.json")
        with open(manifest_path, encoding="utf-8") as f:
            files = json.load(f)["files"]
        bad = [rel for rel, digest in files.items() if fetch_data.sha256_file(os.path.join(self.data_dir, rel)) != digest]
        if bad:
            raise SystemExit(f"synthetic data differs from its manifest.json in {len(bad)} file(s), e.g. {bad[:3]}: "
                             "rebuild it with `python -m synthetic build` in 5_data/synthetic")
        return fetch_data.sha256_file(manifest_path)

    def read(self, meeting_id):
        with open(os.path.join(self.data_dir, "meetings", meeting_id + ".json"), encoding="utf-8") as f:
            return json.load(f)

    # One synthetic meeting as an ami.Meeting. Speakers become their roles (PM, ME, ...), as in AMI, and the
    # reference is the summary's abstract section, the same section AMI's reference uses.
    def load(self, meeting_id):
        m = self.read(meeting_id)
        roles = {p["label"]: p["role"] for p in m["participants"]}
        lines = sorted((ami.Line(roles[s["speaker"]], s["start"], s["text"]) for s in m["segments"]),
                       key=lambda l: (l.start, l.speaker))
        groups = {"feature": m["provenance"]["feature"],
                  "scenario": sorted({e["scenario_id"] for e in m["edge_cases"]}),
                  "absent_participants": sum(p["status"] == "absent" for p in m["participants"])}
        return ami.Meeting(meeting_id, lines, " ".join(m["summary"]["abstract"]), groups)

    def meetings(self, split_name):
        return [self.load(m) for m in self.split[split_name]]

    def train_summary_words(self):
        return round(np.mean([len(" ".join(self.read(m)["summary"]["abstract"]).split()) for m in self.split["train"]]))


DATASETS = {"ami": AMI, "synthetic": Synthetic}
