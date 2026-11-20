"""Matching and metrics on tiny examples where the right answer is known. A fake embedder makes texts with the same
first word identical and everything else unrelated, so the matching itself is what is tested."""
import json

import numpy as np
import pytest

import breakdowns
import data
import metrics
import score
from data import Gold, Meeting, Person


class FakeEmbed:
    """Same first word -> cosine 1.0, else 0.0 (orthogonal one-hot vectors)."""

    def __call__(self, texts):
        vocab = sorted({t.split()[0].lower() for t in texts})
        out = np.zeros((len(texts), 64))
        for i, t in enumerate(texts):
            out[i, hash(t.split()[0].lower()) % 64] = 1.0
        return out


PEOPLE = [Person("A", "Helen", ("helen", "a", "pm", "project manager"), ("helen",)),
          Person("B", "Tom", ("tom", "b", "me", "marketing expert"), ("tom",))]


def gold(text, owners=(), due="", evidence=()):
    return Gold(text, text, frozenset(owners), due, frozenset(evidence))


def make_meeting(golds, mid="M1"):
    return Meeting(mid, "synthetic", [], PEOPLE, golds, {"feature": "F2"})


def pred(text, owner="", due="", lines=()):
    return {"text": text, "owner": owner, "due": due, "source_line_ids": list(lines)}


# ================= matching =================

def test_match_is_one_to_one_best_first():
    sim = np.array([[0.9, 0.8],
                    [0.85, 0.1]])
    # best pair (0,0)=0.9 first; then pred 1 can't take gold 0; pred 0 is used -> only one more candidate (1,1)=0.1
    assert metrics.match(sim, 0.5) == [(0, 0, 0.9)]
    assert metrics.match(sim, 0.05) == [(0, 0, 0.9), (1, 1, 0.1)]


def test_match_greedy_not_optimal_is_documented_behaviour():
    sim = np.array([[0.9, 0.85],
                    [0.88, 0.2]])
    # greedy takes (0,0); (1,0) is then blocked; (1,1)=0.2 below threshold -> 1 match
    assert [(i, j) for i, j, _ in metrics.match(sim, 0.5)] == [(0, 0)]


def test_match_threshold_is_inclusive():
    assert metrics.match(np.array([[0.7]]), 0.7) == [(0, 0, 0.7)]
    assert metrics.match(np.array([[0.69]]), 0.7) == []


def test_empty_sides():
    m = make_meeting([gold("send the deck")])
    c = metrics.score_meeting(m, [], FakeEmbed(), 0.5)
    assert (c.pred, c.gold, c.tp) == (0, 1, 0)
    c = metrics.score_meeting(make_meeting([]), [pred("send the deck")], FakeEmbed(), 0.5)
    assert (c.pred, c.gold, c.tp) == (1, 0, 0)


# ================= counts and rates =================

def test_counts_precision_recall_f1_owner_due():
    m = make_meeting([gold("send the deck", {"A"}, "weekday:friday"), gold("check costs", {"B"}),
                      gold("book a room", (), "tomorrow")])
    preds = [pred("send the deck to everyone", "Helen", "", ()),         # match, owner right, due wrong (none)
             {**pred("check the costs", "Helen"), "due_text": "today"},  # match, owner wrong; gold has no due
             pred("book the big room", "", "tomorrow"),                  # match, unassigned = unassigned, due right
             pred("order pizza", "Tom")]                                 # no gold: false positive
    c = metrics.score_meeting(m, preds, FakeEmbed(), 0.5)
    assert (c.pred, c.gold, c.tp) == (4, 3, 3)
    assert (c.owner_ok, c.owner_n) == (2, 3)
    assert (c.due_ok, c.due_n) == (1, 2)
    r = metrics.rates(c)
    assert r["precision"] == pytest.approx(3 / 4) and r["recall"] == 1.0
    assert r["f1"] == pytest.approx(2 * 0.75 / 1.75)
    assert r["owner_acc"] == pytest.approx(2 / 3) and r["due_acc"] == pytest.approx(1 / 2)


def test_owner_right_if_one_of_several_gold_owners():
    m = make_meeting([gold("prototype the remote", {"A", "B"})])
    assert metrics.score_meeting(m, [pred("prototype it", "Tom")], FakeEmbed(), 0.5).owner_ok == 1
    assert metrics.score_meeting(m, [pred("prototype it", "")], FakeEmbed(), 0.5).owner_ok == 0


def test_micro_sums_over_meetings():
    a = metrics.Counts(pred=10, gold=2, tp=2)
    b = metrics.Counts(pred=0, gold=8, tp=0)
    r = metrics.rates(metrics.total([a, b]))
    assert r["precision"] == pytest.approx(0.2) and r["recall"] == pytest.approx(0.2)


def test_no_predictions_no_gold_gives_zero_not_error():
    r = metrics.rates(metrics.Counts())
    assert r == {"precision": 0.0, "recall": 0.0, "f1": 0.0, "owner_acc": None, "due_acc": None}


# ================= owners =================

@pytest.mark.parametrize("owner, key", [
    ("Helen", "A"), ("helen", "A"), ("HELEN PARK", "A"), ("A", "A"), ("Project Manager", "A"), ("pm", "A"),
    ("Tom", "B"), ("the marketing expert (Tom)", "B"), ("", ""), ("Unassigned", ""), ("the team", ""),
    ("everyone", ""), (None, ""), ("Zoe", "?zoe"),
])
def test_owner_key(owner, key):
    assert metrics.owner_key(owner, PEOPLE) == key


# ================= bootstrap =================

def test_bootstrap_is_reproducible_and_brackets_the_point():
    counts = [metrics.Counts(pred=p, gold=g, tp=t, owner_ok=t, owner_n=t) for p, g, t in
              [(3, 2, 1), (4, 4, 3), (1, 2, 1), (5, 1, 1), (2, 3, 2), (0, 2, 0)]]
    s1, s2 = metrics.summarize(counts, B=2000, seed=0), metrics.summarize(counts, B=2000, seed=0)
    assert s1 == s2
    for k in ("precision", "recall", "f1"):
        lo, hi = s1[k + "_ci"]
        assert lo <= s1[k] <= hi
    assert s1["n"] == 6 and s1["pred"] == 15 and s1["gold"] == 14 and s1["matched"] == 8


def test_bootstrap_of_identical_meetings_has_no_width():
    s = metrics.summarize([metrics.Counts(pred=2, gold=2, tp=1)] * 5)
    assert s["precision_ci"] == [0.5, 0.5]


# ================= edge-case regions =================

def test_region_counts():
    m = make_meeting([gold("send the deck", {"A"}, evidence={"t1"}), gold("check costs", {"B"}, evidence={"t9"})])
    preds = [pred("send it", "Helen", lines=["t1"]), pred("drop the idea", "Tom", lines=["t2"]),
             pred("check costs", "Tom", lines=["t9"])]
    c = metrics.score_meeting(m, preds, FakeEmbed(), 0.5)
    r = metrics.region_counts(m, preds, c, frozenset({"t1", "t2"}))
    assert (r.pred, r.gold, r.tp, dict(r.pairs)["recall_tp"], r.owner_ok, r.owner_n) == (2, 1, 1, 1, 1, 1)


# ================= predictions files =================

@pytest.mark.parametrize("content", [
    {"meetings": {"M1": {"items": [{"text": "Send the deck", "owner": "Helen"}]}}},
    {"M1": [{"task": "Send the deck", "owner": "Helen", "due": ""}]},            # the LLM summarizer's shape
    {"M1": {"action_items": [{"task": "Send the deck", "owner": "Helen"}]}},
    {"M1": {"items": ["Send the deck"]}},
])
def test_any_contract_like_predictions_file(tmp_path, content):
    path = tmp_path / "p.json"
    path.write_text(json.dumps(content))
    items = score.load_predictions(str(path))["M1"]
    assert items[0]["text"] == "Send the deck" and items[0]["id"] == "a1" and items[0]["source_line_ids"] == []


def test_evaluate_with_breakdowns():
    ms = [make_meeting([gold("send the deck", {"A"}, evidence={"t1"})], "M1"),
          make_meeting([gold("check costs", {"B"})], "M2")]
    ms[0].edge_cases = [("F2_VAGUE_NO_OWNER", frozenset({"t1"}))]
    res = score.evaluate(ms, {"M1": [pred("send it", "Helen", lines=["t1"])]}, FakeEmbed(), 0.5)
    assert res["overall"]["matched"] == 1 and res["overall"]["gold"] == 2
    assert res["groups"]["feature"]["F2"]["n"] == 2 and res["groups"]["feature"]["F2"]["too_small"]
    assert res["groups"]["edge_case"]["F2_VAGUE_NO_OWNER"]["recall"] == 1.0


# ================= gold owners and text in AMI =================

@pytest.mark.parametrize("sentence, owners", [
    ("The Industrial Designer will research the working design and components.", {"ID"}),
    ("The industrial designer and user interface designer will work on a prototype.", {"ID", "UI"}),
    ("The Designers and the Marketing Expert will work on their individual presentations.", {"ID", "UI", "ME"}),
    ("The team will decide how the user interface will work", set()),
    ("All participants will fill out a questionnaire after the meeting.", set()),
    ("The Project Manager instructed the User Interface Designer and the Industrial Designer to construct the prototype.",
     {"UI", "ID"}),
    ("The User Interface Designer will work on the interface and the Industrial Designer will work on the look.", {"UI", "ID"}),
    ("The Project Manager will research companies that can provide the Industrial Designer with help.", {"PM"}),
    ("The User Interface Designer was instructed to finalize the placement of the buttons.", {"UI"}),
])
def test_ami_owners(sentence, owners):
    assert data.ami_owners(sentence) == frozenset(owners)


def test_ami_he_means_the_owner_before():
    assert data.ami_owners("He will also present the plan to management.", frozenset({"PM"})) == {"PM"}


def test_task_phrase():
    assert data.task_phrase("The Marketing Expert will prepare a report on trendwatching.") == \
        "Prepare a report on trendwatching."
    assert data.task_phrase("The Project Manager instructed the User Interface Designer to construct the prototype.") == \
        "Construct the prototype."
    assert data.task_phrase("Check the costs.") == "Check the costs."


def test_line_ids_are_unique_and_contract_shaped():
    used = set()
    assert [data.line_id(s, used) for s in (1.2344, 1.2344, 1.2345, 0)] == ["t1234", "t1235", "t1236", "t0"]


def test_ami_groups_from_participant_ids():
    parts = {"X1a": [("A", "PM", "MEE001"), ("B", "ME", "FEO002"), ("C", "ID", "MTD003ID"), ("D", "UI", "MIE004")]}
    assert breakdowns.ami_groups("X1a", parts) == {"speakers": "mixed", "gender": "mixed", "type": "a (kickoff)"}
    parts = {"X1d": [("A", "PM", "FEE001"), ("B", "ME", "FEE002"), ("C", "ID", "FEE003"), ("D", "UI", "FEE004")]}
    assert breakdowns.ami_groups("X1d", parts) == {"speakers": "all native", "gender": "all female", "type": "d (final)"}
