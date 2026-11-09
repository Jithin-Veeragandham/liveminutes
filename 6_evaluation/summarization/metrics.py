"""Summary metrics: ROUGE-1, ROUGE-2, ROUGE-L (rouge-score package), ROUGE-SU4, and optional BERTScore.

All compare a predicted summary with AMI's human-written abstract of the same meeting. F-measure, 0-1.
"""
import re
from collections import Counter

from rouge_score import rouge_scorer

_SCORER = rouge_scorer.RougeScorer(["rouge1", "rouge2", "rougeL"], use_stemmer=True)


# Lower-case and split text into word tokens.
def tokens(text):
    return re.findall(r"[a-z0-9]+", text.lower())


# Unigrams plus skip-bigrams with at most 4 words between the pair (the "SU4" in ROUGE-SU4).
def su4_grams(words, gap=4):
    grams = Counter(words)
    for i, a in enumerate(words):
        for b in words[i + 1: i + 2 + gap]:
            grams[(a, b)] += 1
    return grams


# ROUGE-SU4 F-measure: overlap of SU4 grams between reference and prediction.
def rouge_su4(reference, prediction):
    ref, pred = su4_grams(tokens(reference)), su4_grams(tokens(prediction))
    overlap = sum((ref & pred).values())
    if not overlap:
        return 0.0
    p, r = overlap / sum(pred.values()), overlap / sum(ref.values())
    return 2 * p * r / (p + r)


# ROUGE-1, ROUGE-2, ROUGE-L and ROUGE-SU4 F-measures for one summary.
def rouge_all(reference, prediction):
    s = _SCORER.score(reference, prediction)
    return {"rouge1": s["rouge1"].fmeasure, "rouge2": s["rouge2"].fmeasure,
            "rougeL": s["rougeL"].fmeasure, "rougeSU4": rouge_su4(reference, prediction)}


# BERTScore F1 per summary, with roberta-large pinned to an exact model commit (downloads ~1.4 GB once).
def bertscore(references, predictions, cfg):
    from bert_score import score
    from huggingface_hub import snapshot_download
    path = snapshot_download(cfg["model"], revision=cfg["revision"],
                             allow_patterns=["*.json", "*.txt", "model.safetensors"])   # PyTorch weights only
    _, _, f1 = score(predictions, references, model_type=path, num_layers=17,   # 17 = bert-score's roberta-large default
                     rescale_with_baseline=cfg["rescale_with_baseline"], verbose=False)
    return f1.tolist()
