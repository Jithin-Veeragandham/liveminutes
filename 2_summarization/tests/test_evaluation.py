"""Tests for the evaluation additions: recursive TextRank, the synthetic dataset loader, and group breakdowns.

    cd 2_summarization && ../.venv/Scripts/python -m unittest discover -s tests
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
for folder in ["2_summarization", "5_data/ami", "6_evaluation/summarization"]:
    sys.path.insert(0, os.path.join(REPO, folder))

import fetch_data  # noqa: E402
from ami import Line  # noqa: E402
from chunks import make_chunks  # noqa: E402
from datasets import Synthetic  # noqa: E402
from disaggregate import by_group, mean_over_seeds  # noqa: E402
from textrank_baseline import TextRankSummarizer  # noqa: E402

TOPICS = ["the remote control needs a bigger battery and a rubber case",
          "the budget for the remote control is twelve euros per unit",
          "the marketing report says users want a remote control that is easy to find",
          "the remote control should have fewer buttons than the old remote control"]


def meeting_lines(n=40, gap=8):
    return [Line("PM" if i % 2 else "ME", i * gap, f"{TOPICS[i % 4]} and point number {i} about it") for i in range(n)]


class ChunksTest(unittest.TestCase):
    def test_chunks_cover_every_line_in_order(self):
        lines = meeting_lines()
        chunks = make_chunks(lines, 30)
        self.assertEqual([l for c in chunks for l in c], lines)
        self.assertTrue(all(c[-1].start - c[0].start < 30 for c in chunks))


class RecursiveTextRankTest(unittest.TestCase):
    def test_recursive_keeps_budget_and_is_extractive(self):
        lines = meeting_lines()
        model = TextRankSummarizer(40, mode="recursive", chunk_seconds=30, buffer=1.5)
        summary = model.summarize(lines)
        self.assertTrue(summary)
        self.assertLessEqual(len(summary.split()), 40 + 20)   # summa stops at the sentence that crosses the budget
        said = {l.text + "." for l in lines}
        sentences = [s.strip() + "." for s in summary.split(".") if s.strip()]
        self.assertTrue(all(s in said for s in sentences), sentences)

    def test_short_meeting_is_kept_whole(self):
        lines = meeting_lines(3)
        for mode in ("full", "recursive"):
            summary = TextRankSummarizer(500, mode=mode).summarize(lines)
            self.assertEqual(summary.count("point number"), 3)

    def test_full_mode_is_the_old_behaviour(self):
        lines = meeting_lines()
        self.assertTrue(TextRankSummarizer(40, mode="full").summarize(lines))


class SyntheticLoaderTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.dir, "meetings"))
        series = {}
        for n, (sid, split, feature) in enumerate([("SY0101", "train", "F1"), ("SY0102", "dev", "F1"),
                                                    ("SY0201", "dev", "F2"), ("SY0202", "test", "F2")]):
            ids = [sid + "a", sid + "b"]
            series[sid] = {"split": split, "feature": feature, "meetings": ids}
            for m in ids:
                self.write_meeting(m, feature)
        files = {f"meetings/{f}": fetch_data.sha256_file(os.path.join(self.dir, "meetings", f))
                 for f in os.listdir(os.path.join(self.dir, "meetings"))}
        with open(os.path.join(self.dir, "manifest.json"), "w") as f:
            json.dump({"files": files}, f)
        self.split_file = os.path.join(self.dir, "splits.json")
        with open(self.split_file, "w") as f:
            json.dump({"series": series}, f)

    def write_meeting(self, mid, feature):
        m = {"meeting_id": mid,
             "participants": [{"label": "A", "role": "PM", "status": "present"},
                              {"label": "B", "role": "ME", "status": "absent"}],
             "segments": [{"speaker": "A", "start": 9.0, "text": "second"}, {"speaker": "A", "start": 1.0, "text": "first"}],
             "summary": {"abstract": ["The team met.", "They agreed."], "actions": [], "decisions": [], "problems": []},
             "edge_cases": [{"scenario_id": f"{feature}_X", "segments": ["s1"]}, {"scenario_id": f"{feature}_Y", "segments": ["s2"]}],
             "provenance": {"feature": feature}}
        with open(os.path.join(self.dir, "meetings", mid + ".json"), "w") as f:
            json.dump(m, f)

    def test_splits_lines_reference_and_groups(self):
        d = Synthetic(self.dir, self.split_file)
        self.assertEqual(d.split["eval"], ["SY0202a", "SY0202b"])
        self.assertEqual(d.split["smoke"], ["SY0102a", "SY0201a"])   # first dev meeting per feature
        m = d.load("SY0201a")
        self.assertEqual([l.text for l in m.lines], ["first", "second"])   # sorted by time
        self.assertEqual(m.lines[0].speaker, "PM")                       # label -> role, as in AMI
        self.assertEqual(m.reference, "The team met. They agreed.")
        self.assertEqual(m.groups["feature"], "F2")
        self.assertEqual(m.groups["scenario"], ["F2_X", "F2_Y"])
        self.assertEqual(d.train_summary_words(), 5)

    def test_changed_file_is_refused(self):
        with open(os.path.join(self.dir, "meetings", "SY0101a.json"), "a") as f:
            f.write(" ")
        with self.assertRaises(SystemExit):
            Synthetic(self.dir, self.split_file)


class GroupBreakdownTest(unittest.TestCase):
    def test_means_gap_worst_and_multi_valued_groups(self):
        per_meeting = {"ES2002a": {"rouge1": 0.2}, "ES2002b": {"rouge1": 0.4},
                       "IS1000a": {"rouge1": 0.6}, "SY0101a": {"rouge1": 0.5}}
        groups = {"ES2002a": {"g": "x", "s": ["p", "q"]}, "ES2002b": {"g": "x", "s": ["p"]},
                  "IS1000a": {"g": "y", "s": ["q"]}, "SY0101a": {"g": "y", "s": ["q"]}}
        out = by_group(per_meeting, groups, ["g", "s"])
        x, y = out["g"]["groups"]["x"], out["g"]["groups"]["y"]
        self.assertEqual((x["n"], x["n_series"], x["mean"]["rouge1"]), (2, 1, 0.3))
        self.assertTrue(x["small"])
        self.assertEqual((y["n"], y["n_series"], y["mean"]["rouge1"]), (2, 2, 0.55))
        self.assertEqual(out["g"]["gap"]["rouge1"], 0.25)
        self.assertEqual(out["g"]["worst"]["rouge1"], "x")
        self.assertEqual(out["s"]["groups"]["q"]["n"], 3)   # a meeting with two scenarios counts in both

    def test_mean_over_seeds(self):
        runs = [{"m": {"rouge1": 0.2}}, {"m": {"rouge1": 0.4}}]
        self.assertAlmostEqual(mean_over_seeds(runs)["m"]["rouge1"], 0.3)


if __name__ == "__main__":
    unittest.main()
