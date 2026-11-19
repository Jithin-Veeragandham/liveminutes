"""Every model setting a summarization run uses, in one place. Each run copies these values into the results log.

The number of seeds is NOT here: it is at the top of run.py (N_SEEDS), and can be changed on the command line.
"""
import os

from llm_providers import load_env

load_env()   # the repo's .env, so LIVEMINUTES_LLM_PROVIDER / LIVEMINUTES_LLM_MODEL set there apply below

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

# LLM row: rolling notes (summary + action items) from a hosted LLM (llm_summarizer.py). Provider-agnostic:
# llm_providers.py has the providers; change "provider" / "model" here or in .env (LIVEMINUTES_LLM_PROVIDER /
# LIVEMINUTES_LLM_MODEL). The Anthropic provider needs ANTHROPIC_API_KEY in .env.
LLM = {
    "provider": os.environ.get("LIVEMINUTES_LLM_PROVIDER", "anthropic"),
    "model": os.environ.get("LIVEMINUTES_LLM_MODEL", "claude-sonnet-5-5"),
    "chunk_seconds": 30,      # one LLM call per ~30 s of meeting, each given the notes so far
    "context_lines": 6,       # last lines of the previous chunk sent again, so an answer is seen with its question
    "min_seconds_between_calls": 30,   # real-time pace: never more than one call per 30 s (stays far under
                                       # free-tier rate limits). A 30 min recording takes ~30 min; run.py --no-wait skips it
    "summary_words": None,    # None = average length of the TRAINING split's human summaries, like TextRank (AMI runs)
    "effort": "medium",       # how hard the model thinks: low / medium / high (Sonnet's default) / xhigh / max
    "max_tokens": 16000,      # cap per reply, thinking included; the notes themselves are a few hundred tokens
    "fallbacks": "default",   # if Claude's safety filter declines, the API retries on a fallback model; None = off
}
LLM_LIVE_SUMMARY_WORDS = 160  # summary length for live meetings (no training split there): the AMI train average

# BERTScore (optional, --bertscore): raw F1, not rescaled, with a pinned roberta-large.
BERTSCORE = {"model": "FacebookAI/roberta-large", "revision": "722cf37b1afa9454edce342e7895e588b6ff1d59",
             "rescale_with_baseline": False}
