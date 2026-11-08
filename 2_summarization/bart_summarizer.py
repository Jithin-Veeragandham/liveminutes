"""Deep row: BART trained on chat summaries (philschmid/bart-large-cnn-samsum), never trained on AMI.

Summarizes about 60 seconds of meeting at a time, then summarizes those window summaries into one
meeting summary ("summary of summaries"), the way a live summary would be built during a meeting.
Needs requirements-bart.txt (torch + transformers). Downloads the pinned model (~1.6 GB) on first use.
"""
import time

import torch
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
from transformers.utils import logging as hf_logging

from ami import ROLE_NAMES


# Cut time-ordered lines into consecutive windows of about `seconds` each.
def make_windows(lines, seconds):
    windows, current, start = [], [], None
    for line in lines:
        start = line.start if start is None else start
        if line.start - start >= seconds and current:
            windows.append(current)
            current, start = [], line.start
        current.append(line)
    return windows + ([current] if current else [])


# Write a window as chat lines ("Project Manager: let's start"), the input format BART-SAMSum was trained on.
def as_dialogue(window):
    return "\n".join(f"{ROLE_NAMES.get(l.speaker, l.speaker)}: {l.text}" for l in window)


class BartSummarizer:
    """Window-by-window BART summaries, combined into one meeting summary."""

    # Load the pinned tokenizer and model onto the configured device (CPU by default).
    def __init__(self, cfg):
        self.cfg = cfg
        self.version = f"{cfg['model']}@{cfg['revision'][:12]}"
        self.tokenizer = AutoTokenizer.from_pretrained(cfg["model"], revision=cfg["revision"])
        self.model = AutoModelForSeq2SeqLM.from_pretrained(cfg["model"], revision=cfg["revision"])
        self.model = self.model.to(cfg["device"]).eval()
        hf_logging.set_verbosity_error()   # length limits are set on purpose; hide the warnings
        self.seconds_per_window = []

    # Summarize one text (BART reads at most 1024 tokens; longer input is cut). Beam search, no sampling.
    @torch.no_grad()
    def summarize_text(self, text, max_new_tokens=80, min_new_tokens=10):
        inputs = self.tokenizer(text, return_tensors="pt", truncation=True, max_length=1024).to(self.cfg["device"])
        out = self.model.generate(**inputs, num_beams=self.cfg["num_beams"], do_sample=False,
                                  max_new_tokens=max_new_tokens, min_new_tokens=min_new_tokens,
                                  no_repeat_ngram_size=3, early_stopping=True)
        return self.tokenizer.decode(out[0], skip_special_tokens=True).strip()

    # Combine window summaries into one summary, compressing groups of 4 until they fit in one BART input.
    def combine(self, parts, max_tokens=700):
        while len(self.tokenizer(" ".join(parts))["input_ids"]) > max_tokens and len(parts) > 1:
            parts = [self.summarize_text(" ".join(parts[i:i + 4]), max_new_tokens=90) for i in range(0, len(parts), 4)]
        return self.summarize_text(" ".join(parts), max_new_tokens=220, min_new_tokens=60)

    # Summarize a whole meeting (a list of Line objects): each window, then the summary of summaries.
    def summarize(self, lines):
        parts = []
        for window in make_windows(lines, self.cfg["window_seconds"]):
            start = time.perf_counter()
            useful = [l for l in window if len(l.text.split()) > 2] or window
            parts.append(self.summarize_text(as_dialogue(useful), max_new_tokens=60))
            self.seconds_per_window.append(time.perf_counter() - start)
        return self.combine(parts)
