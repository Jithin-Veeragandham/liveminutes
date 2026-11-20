"""Every setting the action-item baselines and their evaluation use, in one place. Each run copies these values into
the results log (config + config_hash).

Values marked "dev" were chosen on the dev splits only (AMI dev, synthetic dev): see 6_evaluation/action_items/
results/tuning/ for the runs that chose them. Nothing here was chosen on AMI eval or synthetic test.
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
RESULTS_DIR = os.path.join(REPO, "6_evaluation", "action_items", "results")
RESULTS_LOG = os.path.join(RESULTS_DIR, "results_log.jsonl")      # one line per run (committed)
PREDICTIONS_DIR = os.path.join(RESULTS_DIR, "predictions")        # contract-format predictions per run (committed)
CACHE_DIR = os.path.join(HERE, "cache")                           # zero-shot scores + embeddings (gitignored)

SEED = 42   # both baselines are deterministic; the seed is set anyway and logged (like 2_summarization)

# Baseline 1: rules (rules.py). No model.
RULES = {
    "lookahead_lines": 3,      # dev: how far after a request / offer to look for "Sure, I'll do that" (who takes it)
    "merge_jaccard": 0.4,      # dev: merge two items whose content words overlap this much (Jaccard)...
    "merge_window_lines": 1,   # ...or the owner answers within this many lines ("Yeah, I'll write the commands")
    "min_text_words": 3,       # an item's text is the words from the task verb on; shorter -> the whole line
    "text_until": "sentence",  # dev: the text runs to the end of the line ("line") or of the sentence ("sentence")
}

# Baseline 2: zero-shot NLI classifier (zeroshot.py). Pinned to an exact model commit.
ZEROSHOT = {
    "model": "facebook/bart-large-mnli",
    "revision": "d7645e127eaf1aefc7862fd59a17a5aa8558b8ce",
    "hypothesis_template": "{}",
    # dev: "entailment" = one hypothesis, P(entailment) vs contradiction (the pipeline's multi_label=True);
    #      "versus_other" = two labels, the task hypothesis and OTHER_LABEL, softmax over their entailment scores
    #      (the pipeline's multi_label=False); the score is the task label's share.
    "mode": "entailment",
    "hypothesis": "In this text, someone commits to doing a task or is asked to do one.",   # dev
    "context": "previous_line",   # dev: "none" = the line alone, "previous_line" = the line before it, then the line
    "threshold": 0.9,             # dev: keep lines whose entailment probability is at least this
    "min_words": 3,               # lines shorter than this are never tasks ("Okay.", "Yeah, sure.") and aren't scored
    "batch_size": 32,
    "device": os.environ.get("LIVEMINUTES_DEVICE", "auto"),   # auto = Apple MPS if available, else CPU
}
OTHER_LABEL = "This text is small talk, a question or a discussion, not a task."
# The label wordings and contexts the dev tuning chose from (fixed before tuning; "mode" and the finer thresholds
# were added after the first dev run, see below).
ZEROSHOT_GRID = {
    "mode": ["entailment", "versus_other"],
    "hypothesis": [
        "In this text, someone commits to doing a task or is asked to do one.",
        "This text contains an action item.",
        "Someone promises or is asked to do something after the meeting.",
        "This text is a request or a commitment to do a task.",
    ],
    "context": ["none", "previous_line"],
    # 0.50 ... 0.999, then finer steps up to 0.9998. The finer steps were added after the first dev run peaked at
    # 0.999, the top of the first grid: with one hypothesis, the pipeline's multi_label score is entailment vs
    # contradiction only, and it is above 0.9 for 81% of AMI dev lines, so the useful range is 0.999 to 0.9998.
    "threshold": [round(0.50 + 0.02 * i, 2) for i in range(25)] + [0.99, 0.995, 0.998, 0.999]
                 + [0.9992, 0.9994, 0.9995, 0.9996, 0.9997, 0.9998],
}

# Evaluation (6_evaluation/action_items/). Matching predicted to gold items by meaning.
MATCHING = {
    "embedder": "BAAI/bge-small-en-v1.5",
    "revision": "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a",
    "pooling": "cls",          # as the model card says; vectors L2-normalized, similarity = cosine
    "threshold": 0.7,          # dev: a pair matches if its cosine similarity is at least this (calibrate.py)
    "bootstrap": {"B": 2000, "rng": "numpy.random.default_rng(0)", "level": 0.95, "unit": "meeting"},
}
