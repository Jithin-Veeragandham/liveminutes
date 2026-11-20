"""The zero-shot baseline without the model: a fake pipeline stands in for bart-large-mnli."""
import json

import pytest

import settings
import zeroshot


def lines(*rows):
    return [{"id": f"t{i}", "speaker": who, "start": float(i), "text": text} for i, (who, text) in enumerate(rows)]


class FakePipe:
    """Scores 0.95 for lines with 'send' or 'check', else 0.1. Counts calls."""

    def __init__(self):
        self.calls = 0

    def __call__(self, texts, candidate_labels, hypothesis_template, multi_label, batch_size):
        self.calls += 1
        if multi_label:   # entailment mode: one label
            assert len(candidate_labels) == 1
            return [{"labels": candidate_labels, "scores": [0.95 if ("send" in t or "check" in t) else 0.1]} for t in texts]
        assert len(candidate_labels) == 2   # versus_other: labels come back sorted by score, highest first
        out = []
        for t in texts:
            task = 0.8 if ("send" in t or "check" in t) else 0.3
            pairs = sorted(zip(candidate_labels, [task, 1 - task]), key=lambda x: -x[1])
            out.append({"labels": [l for l, _ in pairs], "scores": [v for _, v in pairs]})
        return out


@pytest.fixture
def scorer(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CACHE_DIR", str(tmp_path))
    s = zeroshot.Scorer({**settings.ZEROSHOT, "device": "cpu"})
    s.pipe = FakePipe()
    return s


def test_premise_with_and_without_the_line_before():
    ls = lines(("Ann", "The deck is done."), ("Bob", "Great, I'll send it."))
    assert zeroshot.premise(ls, 1, "none") == "Bob: Great, I'll send it."
    assert zeroshot.premise(ls, 1, "previous_line") == "Ann: The deck is done.\nBob: Great, I'll send it."
    assert zeroshot.premise(ls, 0, "previous_line") == "Ann: The deck is done."


def test_short_lines_are_not_scored(scorer):
    ls = lines(("Ann", "Okay."), ("Bob", "Yeah sure"), ("Ann", "I will send it round tonight."))
    scored = zeroshot.scored_lines(ls, scorer, {**settings.ZEROSHOT, "min_words": 3})
    assert [i for i, _ in scored] == [2]


def test_scores_are_cached_on_disk(scorer):
    texts = ["Ann: please check the costs", "Bob: nice weather"]
    assert scorer(texts, "h") == [0.95, 0.1] and scorer.computed == 2
    assert scorer(texts, "h") == [0.95, 0.1] and scorer.hits == 2 and scorer.pipe.calls == 1
    assert scorer(texts, "another hypothesis") and scorer.pipe.calls == 2   # the hypothesis is part of the key
    again = zeroshot.Scorer({**settings.ZEROSHOT, "device": "cpu"})       # a new run reads the file
    assert again(texts, "h") == [0.95, 0.1] and again.computed == 0
    with open(scorer.path) as f:
        assert all(len(json.loads(line)) == 2 for line in f)


def test_items_use_the_rules_for_owner_and_due_and_merge(scorer):
    ls = lines(("Ann", "Bob, can you check the supplier quote by Friday?"), ("Bob", "Sure."),
               ("Ann", "And I will send the minutes round."), ("Bob", "Nice weather today, isn't it."))
    scores = zeroshot.scored_lines(ls, scorer, {**settings.ZEROSHOT, "context": "none"})
    items = zeroshot.items_from_scores(ls, ["Ann", "Bob"], scores, 0.9, settings.RULES)
    assert [(i["owner"], i["due_text"]) for i in items] == [("Bob", "by Friday"), ("Ann", "")]
    assert all(i["kind"].startswith("zeroshot/") for i in items)


def test_a_chosen_line_without_a_rule_trigger_is_still_an_item(scorer):
    ls = lines(("Ann", "The quote, Bob, check it with the supplier."), ("Ann", "Sending the deck is mine."))
    items = zeroshot.items_from_scores(ls, ["Ann", "Bob"], [(0, 0.99), (1, 0.99)], 0.9, settings.RULES)
    assert [i["owner"] for i in items] == ["Bob", "Ann"]


def test_threshold():
    ls = lines(("Ann", "I will send the deck round tonight."))
    assert zeroshot.items_from_scores(ls, ["Ann"], [(0, 0.89)], 0.9, settings.RULES) == []
    assert len(zeroshot.items_from_scores(ls, ["Ann"], [(0, 0.9)], 0.9, settings.RULES)) == 1


def test_pinned_model_and_device_choice():
    assert settings.ZEROSHOT["model"] == "facebook/bart-large-mnli"
    assert len(settings.ZEROSHOT["revision"]) == 40
    from device import pick_device
    assert pick_device("cpu") == "cpu" and pick_device("auto") in ("mps", "cuda", "cpu")


def test_versus_other_mode_takes_the_task_labels_share(scorer):
    texts = ["Ann: please check the costs", "Bob: nice weather"]
    assert scorer(texts, "h", "versus_other") == [0.8, 0.3]
    assert scorer(texts, "h") == [0.95, 0.1]                    # a different cache key from the entailment mode
    assert scorer.key("h", texts[0]) != scorer.key("h", texts[0], "versus_other")
