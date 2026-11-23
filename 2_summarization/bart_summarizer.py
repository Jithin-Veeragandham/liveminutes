"""Deep row: BART trained on chat summaries (philschmid/bart-large-cnn-samsum), never trained on AMI.

Two modes (settings.BART["mode"], or run.py --mode):
  full       summarizes about 60 seconds of meeting at a time, then summarizes those window summaries into one
             meeting summary ("summary of summaries"). Needs every window before the final summary.
  recursive  the way a live summary is built: after each window, BART summarizes (the summary so far + the
             new window) into the new summary so far. The summary so far goes first, written as a "Summary"
             line of the chat, and the minimum length grows with it, so earlier content is not squeezed out.
Needs torch + transformers (section 3 of requirements.txt). Downloads the pinned model (~1.6 GB) on first use.

Tunable settings (cfg keys; the defaults below are the original behaviour; 2_summarization/hpo/ searches them):
  window_seconds     length of a window                         min_line_words    drop lines shorter than this
  max_window_tokens  a longer window is split, so BART (1024 input tokens) never cuts off its end
  full:      window_max_tokens (each window's summary), final_max_tokens / final_min_tokens (the meeting summary)
  recursive: rec_max_tokens (the summary so far), floor_ratio / floor_cap (its minimum: ratio x previous, capped)
  both:      length_penalty (beam search: > 1 favours longer outputs)
"""
import torch
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
from transformers.utils import logging as hf_logging

from ami import ROLE_NAMES
from chunks import make_chunks

make_windows = make_chunks   # older name, kept for anything that still imports it

DEFAULTS = {"min_line_words": 3, "max_window_tokens": 700, "window_max_tokens": 60, "final_max_tokens": 220,
            "final_min_tokens": 60, "rec_max_tokens": 220, "floor_ratio": 0.8, "floor_cap": 60, "length_penalty": 1.0}


# Write a window as chat lines ("Project Manager: let's start"), the input format BART-SAMSum was trained on.
def as_dialogue(window):
    return "\n".join(f"{ROLE_NAMES.get(l.speaker, l.speaker)}: {l.text}" for l in window)


class BartSummarizer:
    """Window-by-window BART summaries, combined into one meeting summary."""

    # Load the pinned tokenizer and model onto the configured device (CPU by default).
    def __init__(self, cfg):
        self.cfg = {**DEFAULTS, **cfg}
        self.mode = self.cfg.get("mode", "full")
        self.version = f"{cfg['model']}@{cfg['revision'][:12]}"
        self.tokenizer = AutoTokenizer.from_pretrained(cfg["model"], revision=cfg["revision"])
        self.model = AutoModelForSeq2SeqLM.from_pretrained(cfg["model"], revision=cfg["revision"])
        self.model = self.model.to(cfg["device"]).eval()
        hf_logging.set_verbosity_error()   # length limits are set on purpose; hide the warnings

    def n_tokens(self, text):
        return len(self.tokenizer(text)["input_ids"])

    # Summarize one text (BART reads at most 1024 tokens; longer input is cut). Beam search, no sampling.
    @torch.no_grad()
    def summarize_text(self, text, max_new_tokens=80, min_new_tokens=10):
        inputs = self.tokenizer(text, return_tensors="pt", truncation=True, max_length=1024).to(self.cfg["device"])
        out = self.model.generate(**inputs, num_beams=self.cfg["num_beams"], do_sample=False,
                                  max_new_tokens=max_new_tokens, min_new_tokens=min(min_new_tokens, max_new_tokens),
                                  no_repeat_ngram_size=3, early_stopping=True,
                                  length_penalty=self.cfg["length_penalty"])
        return self.tokenizer.decode(out[0], skip_special_tokens=True).strip()

    # Combine window summaries into one summary, compressing groups of 4 until they fit in one BART input.
    def combine(self, parts, max_tokens=700):
        while self.n_tokens(" ".join(parts)) > max_tokens and len(parts) > 1:
            parts = [self.summarize_text(" ".join(parts[i:i + 4]), max_new_tokens=90) for i in range(0, len(parts), 4)]
        return self.summarize_text(" ".join(parts), max_new_tokens=self.cfg["final_max_tokens"],
                                   min_new_tokens=self.cfg["final_min_tokens"])

    # The meeting's windows: ~window_seconds each, short lines dropped, and any window whose chat text is longer
    # than max_window_tokens split in halves until it fits.
    def windows(self, lines):
        out = []
        todo = [[l for l in w if len(l.text.split()) >= self.cfg["min_line_words"]] or w
                for w in make_chunks(lines, self.cfg["window_seconds"])]
        while todo:
            w = todo.pop(0)
            if len(w) > 1 and self.n_tokens(as_dialogue(w)) > self.cfg["max_window_tokens"]:
                todo[:0] = [w[:len(w) // 2], w[len(w) // 2:]]
            else:
                out.append(w)
        return out

    # Summarize a whole meeting (a list of Line objects), in the configured mode.
    def summarize(self, lines):
        windows = self.windows(lines)
        if self.mode == "recursive":
            return self.summarize_recursive(windows)
        return self.combine([self.summarize_text(as_dialogue(w), max_new_tokens=self.cfg["window_max_tokens"])
                             for w in windows])

    # Rolling summary: (summary so far + next window) -> new summary so far. Its minimum length is floor_ratio of
    # the previous summary's (capped at floor_cap tokens), so the summary can grow but doesn't collapse.
    def summarize_recursive(self, windows):
        summary = ""
        for window in windows:
            text = (f"Summary: {summary}\n" if summary else "") + as_dialogue(window)
            floor = min(self.cfg["floor_cap"], int(self.cfg["floor_ratio"] * self.n_tokens(summary))) if summary else 10
            summary = self.summarize_text(text, max_new_tokens=self.cfg["rec_max_tokens"], min_new_tokens=max(10, floor))
        return summary
