"""Scoring predicted action items against gold ones.

Per meeting:
  1. Embed every predicted and gold text (BAAI/bge-small-en-v1.5, pinned revision; settings.MATCHING).
  2. Match one to one, best first: take the most similar (predicted, gold) pair, remove both, repeat. A pair
     only counts if its cosine similarity is at least the threshold (chosen on dev, calibrate.py).
  3. Count: predicted, gold, matched. Owner right (on matched pairs): the predicted owner is one of the gold
     owners, or both are unassigned. Due right (on matched pairs whose gold text names a time): same due key.
Over a set of meetings: sums (micro), so a meeting with many items counts more.
  precision = matched / predicted, recall = matched / gold, F1 = 2PR / (P + R)
  owner accuracy = owner right / matched, due accuracy = due right / matched with a gold due
95% intervals: bootstrap over meetings (B = 2000, numpy.random.default_rng(0)), percentile method.
"""
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "3_action_items"))
from due import due_key  # noqa: E402

UNASSIGNED = {"", "none", "nobody", "no one", "unassigned", "unknown", "n/a", "na", "-", "tbd", "team", "the team",
              "everyone", "everybody", "all", "all of us", "we", "us", "group", "the group", "someone", "somebody"}


# ================= owners =================

def owner_key(owner, people):
    """The gold key of a predicted owner string: '' for unassigned, a person's key, or '?<text>' if unknown.
    Accepts display names, labels, roles and first names: 'Project Manager', 'pm', 'Helen', 'B'."""
    o = " ".join(re.sub(r"[^\w\s'-]", " ", str(owner or "")).lower().split())
    if o in UNASSIGNED:
        return ""
    for p in people:
        if o == p.name.lower() or o in p.aliases:
            return p.key
    for p in people:   # "Helen Park" for "Helen", or "the marketing expert (Tom)"
        words = set(o.split())
        if p.name.lower() in words or any(m in o for m in p.mentions if len(m) > 3):
            return p.key
    return "?" + o


# ================= text similarity =================

class Embedder:
    """Sentence vectors from a pinned Hugging Face model, cached on disk by text."""

    def __init__(self, cfg, cache_dir=None, device="cpu"):
        import torch
        from transformers import AutoModel, AutoTokenizer
        self.cfg, self.torch, self.device = cfg, torch, device
        self.tok = AutoTokenizer.from_pretrained(cfg["embedder"], revision=cfg["revision"])
        self.model = AutoModel.from_pretrained(cfg["embedder"], revision=cfg["revision"]).to(device).eval()
        self.cache, self.path = {}, None
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
            self.path = os.path.join(cache_dir, f"embeddings_{cfg['revision'][:12]}.jsonl")
            if os.path.exists(self.path):
                with open(self.path) as f:
                    for line in f:
                        k, v = json.loads(line)
                        self.cache[k] = np.array(v, dtype=np.float32)

    @staticmethod
    def key(text):
        return hashlib.sha256(text.encode()).hexdigest()[:24]

    def __call__(self, texts):
        todo = sorted({t for t in texts if self.key(t) not in self.cache})
        for i in range(0, len(todo), 64):
            batch = todo[i:i + 64]
            enc = self.tok(batch, padding=True, truncation=True, max_length=256, return_tensors="pt").to(self.device)
            with self.torch.no_grad():
                out = self.model(**enc).last_hidden_state
            vec = out[:, 0] if self.cfg["pooling"] == "cls" else (out * enc["attention_mask"][..., None]).sum(1) / \
                enc["attention_mask"].sum(1, keepdim=True)
            vec = self.torch.nn.functional.normalize(vec, dim=-1).cpu().numpy()
            new = {self.key(t): v for t, v in zip(batch, vec)}
            self.cache.update(new)
            if self.path:
                with open(self.path, "a") as f:
                    for k, v in new.items():
                        f.write(json.dumps([k, [round(float(x), 6) for x in v]]) + "\n")
        return np.stack([self.cache[self.key(t)] for t in texts]) if texts else np.zeros((0, 1))


def similarity(embed, a, b):
    if not a or not b:
        return np.zeros((len(a), len(b)))
    return embed(a) @ embed(b).T


# ================= matching =================

def match(sim, threshold):
    """One-to-one, best first: [(pred index, gold index, similarity)] with similarity >= threshold."""
    pairs = sorted(((sim[i, j], i, j) for i in range(sim.shape[0]) for j in range(sim.shape[1])
                    if sim[i, j] >= threshold), key=lambda x: (-x[0], x[1], x[2]))
    used_p, used_g, out = set(), set(), []
    for s, i, j in pairs:
        if i not in used_p and j not in used_g:
            used_p.add(i)
            used_g.add(j)
            out.append((i, j, float(s)))
    return out


@dataclass
class Counts:
    """What one meeting (or one region of it) contributes to the sums."""
    pred: int = 0
    gold: int = 0
    tp: int = 0
    owner_ok: int = 0
    owner_n: int = 0
    due_ok: int = 0
    due_n: int = 0
    pred_due: int = 0                         # predictions with any due date
    pairs: list = field(default_factory=list)  # (pred index, gold index, similarity)

    def __add__(self, other):
        return Counts(*(getattr(self, f) + getattr(other, f) for f in
                        ("pred", "gold", "tp", "owner_ok", "owner_n", "due_ok", "due_n", "pred_due")))


def pred_due_key(item):
    return due_key(item.get("due_text") or "") or due_key(item.get("due") or "")


def score_meeting(meeting, preds, embed, threshold):
    """Counts for one meeting, plus per-pair details (for error reading)."""
    gold = meeting.gold
    sim = similarity(embed, [p["text"] for p in preds], [g.match_text for g in gold])
    pairs = match(sim, threshold)
    c = Counts(pred=len(preds), gold=len(gold), tp=len(pairs), pairs=pairs,
               pred_due=sum(1 for p in preds if pred_due_key(p)))
    for i, j, _ in pairs:
        key = owner_key(preds[i].get("owner"), meeting.people)
        gold_owners = gold[j].owners or frozenset([""])
        c.owner_n += 1
        c.owner_ok += key in gold_owners
        if gold[j].due:
            c.due_n += 1
            c.due_ok += pred_due_key(preds[i]) == gold[j].due
    return c


def region_counts(meeting, preds, counts, region):
    """Counts restricted to one tagged region of a meeting (a synthetic edge case): gold items with evidence in
    the region, predictions with a source line in it. Matching is the meeting's own (counts.pairs)."""
    in_gold = {j for j, g in enumerate(meeting.gold) if g.evidence & region}
    in_pred = {i for i, p in enumerate(preds) if set(p.get("source_line_ids") or []) & region}
    c = Counts(pred=len(in_pred), gold=len(in_gold))
    matched_pred = {i for i, _, _ in counts.pairs}
    c.tp = len(matched_pred & in_pred)                     # precision side
    recall_tp = 0
    for i, j, _ in counts.pairs:
        if j in in_gold:
            recall_tp += 1
            c.owner_n += 1
            c.owner_ok += owner_key(preds[i].get("owner"), meeting.people) in (meeting.gold[j].owners or frozenset([""]))
    c.pairs = [("recall_tp", recall_tp)]
    return c


# ================= sums and intervals =================

def rates(c, recall_tp=None):
    tp_r = c.tp if recall_tp is None else recall_tp
    p = c.tp / c.pred if c.pred else 0.0
    r = tp_r / c.gold if c.gold else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": p, "recall": r, "f1": f,
            "owner_acc": c.owner_ok / c.owner_n if c.owner_n else None,
            "due_acc": c.due_ok / c.due_n if c.due_n else None}


def total(counts):
    out = Counts()
    for c in counts:
        out = out + c
    return out


def summarize(counts, B=2000, seed=0, level=0.95, recall_tps=None):
    """Point values and bootstrap intervals over meetings (or regions) for a list of Counts."""
    n = len(counts)
    tot = total(counts)
    rtp = sum(recall_tps) if recall_tps is not None else None
    point = rates(tot, rtp)
    out = {"n": n, "pred": tot.pred, "gold": tot.gold, "matched": tot.tp if rtp is None else rtp,
           "owner_n": tot.owner_n, "due_n": tot.due_n, "pred_with_due": tot.pred_due,
           **{k: (round(v, 4) if v is not None else None) for k, v in point.items()}}
    if n == 0:
        return out
    fields = np.array([[c.pred, c.gold, c.tp, c.owner_ok, c.owner_n, c.due_ok, c.due_n] for c in counts], float)
    rt = np.array(recall_tps if recall_tps is not None else [c.tp for c in counts], float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(B, n))
    s = fields[idx].sum(axis=1)          # (B, 7)
    srt = rt[idx].sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        p = np.where(s[:, 0] > 0, s[:, 2] / s[:, 0], 0.0)
        r = np.where(s[:, 1] > 0, srt / s[:, 1], 0.0)
        f = np.where(p + r > 0, 2 * p * r / (p + r), 0.0)
        own = np.where(s[:, 4] > 0, s[:, 3] / s[:, 4], np.nan)
        due = np.where(s[:, 6] > 0, s[:, 5] / s[:, 6], np.nan)
    lo, hi = (1 - level) / 2 * 100, (1 + level) / 2 * 100
    for name, v in (("precision", p), ("recall", r), ("f1", f), ("owner_acc", own), ("due_acc", due)):
        v = v[~np.isnan(v)]
        out[name + "_ci"] = [round(float(np.percentile(v, lo)), 4), round(float(np.percentile(v, hi)), 4)] if len(v) else None
    return out
