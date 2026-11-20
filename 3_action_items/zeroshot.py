"""Baseline 2: a zero-shot NLI classifier picks the task lines; the rules name the owner and the due date.

    b = ZeroShotBaseline()          # facebook/bart-large-mnli at a pinned revision (settings.ZEROSHOT)
    items = b.predict(meeting)      # meeting.lines (UI contract lines), meeting.people

For every transcript line of min_words or more (and min_content_words content words), the transformers zero-shot pipeline asks bart-large-mnli whether
the line (with the line before it, if context = "previous_line") entails the hypothesis, e.g. "In this text,
someone commits to doing a task or is asked to do one." Two ways to score (mode):
  entailment    P(entailment) vs contradiction for this one hypothesis (multi_label=True)
  versus_other  the task hypothesis against settings.OTHER_LABEL ("small talk, a question or a discussion"):
                the task label's share of the two entailment scores (multi_label=False)
Lines at or above the threshold are tasks. Mode, wording, context and threshold were chosen on dev (tune.py).
For each chosen line, rules.item_for_line() gives the owner ("I'll" -> speaker, "Priya, can you" -> Priya, "we" ->
nobody, an answer "Sure, I'll do it" -> who answered) and the due date; then rules.merge() joins repeats.
Scores are cached on disk (cache/zeroshot_<revision>.jsonl) by model, hypothesis and the exact input, so reruns
only score what is new. Runs on Apple's MPS if available, else CPU (device.py).
"""
import hashlib
import json
import os
import time

import rules
import settings
from device import pick_device


def premise(lines, i, context):
    line = lines[i]
    text = f"{line['speaker']}: {line['text']}"
    if context == "previous_line" and i > 0:
        prev = lines[i - 1]
        text = f"{prev['speaker']}: {prev['text']}\n{text}"
    return text


class Scorer:
    """Entailment probabilities from the zero-shot pipeline, with a disk cache."""

    def __init__(self, cfg):
        self.cfg, self.pipe, self.device = cfg, None, pick_device(cfg["device"])
        os.makedirs(settings.CACHE_DIR, exist_ok=True)
        self.path = os.path.join(settings.CACHE_DIR, f"zeroshot_{cfg['revision'][:12]}.jsonl")
        self.cache, self.hits, self.computed, self.seconds = {}, 0, 0, 0.0
        if os.path.exists(self.path):
            with open(self.path) as f:
                for line in f:
                    k, v = json.loads(line)
                    self.cache[k] = v

    def key(self, hypothesis, text, mode="entailment"):
        parts = [self.cfg["model"], self.cfg["revision"], self.cfg["hypothesis_template"], hypothesis, text]
        if mode != "entailment":   # (entailment keys are unchanged from the first version of this cache)
            parts += [mode, settings.OTHER_LABEL]
        return hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:32]

    def load(self):
        if self.pipe is None:
            from transformers import pipeline
            self.pipe = pipeline("zero-shot-classification", model=self.cfg["model"], revision=self.cfg["revision"],
                                 device=self.device)
        return self.pipe

    def __call__(self, texts, hypothesis, mode="entailment"):
        keys = [self.key(hypothesis, t, mode) for t in texts]
        todo = sorted({(k, t) for k, t in zip(keys, texts) if k not in self.cache})
        self.hits += len(texts) - len(todo)
        if todo:
            start = time.perf_counter()
            labels = [hypothesis] if mode == "entailment" else [hypothesis, settings.OTHER_LABEL]
            out = self.load()([t for _, t in todo], candidate_labels=labels,
                              hypothesis_template=self.cfg["hypothesis_template"], multi_label=mode == "entailment",
                              batch_size=self.cfg["batch_size"])
            out = out if isinstance(out, list) else [out]
            with open(self.path, "a") as f:
                for (k, _), o in zip(todo, out):
                    self.cache[k] = round(float(o["scores"][o["labels"].index(hypothesis)]), 6)
                    f.write(json.dumps([k, self.cache[k]]) + "\n")
            self.seconds += time.perf_counter() - start
            self.computed += len(todo)
        return [self.cache[k] for k in keys]


def scored_lines(lines, scorer, cfg, hypothesis=None, context=None, mode=None):
    """[(line index, probability)] for every line with at least min_words words."""
    idx = [i for i, l in enumerate(lines) if len(l["text"].split()) >= cfg["min_words"]
           and len(rules.content(rules.clean(l["text"]))) >= cfg.get("min_content_words", 0)]
    texts = [premise(lines, i, context or cfg["context"]) for i in idx]
    return list(zip(idx, scorer(texts, hypothesis or cfg["hypothesis"], mode or cfg.get("mode", "entailment")))) if idx else []


def items_from_scores(lines, people, scores, threshold, rule_cfg, memo=None):
    """Items for the lines at or above the threshold, with the rules' owner, due date and merging.
    memo: a dict per meeting, kept across thresholds (rules.item_for_line)."""
    people = rules.as_people(people)
    chosen = sorted(i for i, p in scores if p >= threshold)
    items = []
    for i in chosen:
        item = rules.item_for_line(lines, i, people, rule_cfg, items, require_trigger=False, memo=memo)
        if item:
            item.kind = "zeroshot/" + item.kind
            items.append(item)
    return [rules.to_contract(it, n, None) for n, it in enumerate(rules.merge(items, rule_cfg), 1)]


class ZeroShotBaseline:
    name = "zeroshot"

    def __init__(self, cfg=None, rule_cfg=None):
        self.cfg = dict(cfg or settings.ZEROSHOT)
        self.rule_cfg = dict(rule_cfg or settings.RULES)
        self.scorer = Scorer(self.cfg)
        self.version = f"{self.cfg['model']}@{self.cfg['revision']}"

    def predict(self, meeting):
        scores = scored_lines(meeting.lines, self.scorer, self.cfg)
        return items_from_scores(meeting.lines, meeting.people, scores, self.cfg["threshold"], self.rule_cfg)
