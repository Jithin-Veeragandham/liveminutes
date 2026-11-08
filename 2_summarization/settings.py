"""Every model setting a summarization run uses, in one place. Each run copies these values into the results log.

The number of seeds is NOT here: it is at the top of run.py (N_SEEDS), and can be changed on the command line.
"""
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.path.join(REPO, "6_evaluation", "summarization", "results")
RESULTS_LOG = os.path.join(RESULTS_DIR, "results_log.jsonl")           # one line per run (committed)

# Baseline: TextRank (summanlp/textrank, pip package "summa"), extractive, no training.
TEXTRANK = {
    "library": "summa",
    "words": None,            # None = average length of the TRAINING split's human summaries (set at run time)
    "min_line_words": 4,      # skip very short lines ("yeah.", "okay.") before ranking
}

# Deep row: BART trained on chat summaries (SAMSum), never on AMI. Pinned to an exact model commit.
BART = {
    "model": "philschmid/bart-large-cnn-samsum",
    "revision": "e49b3d60d923f12db22bdd363356f1a4c68532ad",
    "window_seconds": 60,     # summarize about 60 s of meeting at a time (experiment card)
    "num_beams": 4,
    "device": os.environ.get("LIVEMINUTES_DEVICE", "cpu"),
}

# BERTScore (optional, --bertscore): raw F1, not rescaled, with a pinned roberta-large.
BERTSCORE = {"model": "FacebookAI/roberta-large", "revision": "722cf37b1afa9454edce342e7895e588b6ff1d59",
             "rescale_with_baseline": False}
