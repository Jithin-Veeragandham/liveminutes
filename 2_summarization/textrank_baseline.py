"""Baseline: TextRank (summanlp/textrank, pip package "summa").

Builds a graph whose nodes are transcript lines and whose edges are word overlap between lines, runs
PageRank on it, and keeps the most central lines (in meeting order) up to a word budget.
Extractive: every output line is something someone actually said. No training, milliseconds on a CPU.
"""
import importlib.metadata
import warnings

# summa 1.2.0 has old-style regex strings that make Python 3.12 print harmless SyntaxWarnings on first import.
with warnings.catch_warnings():
    warnings.simplefilter("ignore", SyntaxWarning)
    from summa import summarizer


class TextRankSummarizer:
    """Summarize a meeting by picking its most central lines."""

    # words: word budget of the summary; min_line_words: drop shorter lines ("yeah.") before ranking.
    def __init__(self, words, min_line_words=4):
        self.words = words
        self.min_line_words = min_line_words
        self.version = "summa " + importlib.metadata.version("summa")

    # Return the summary of one meeting (a list of Line objects) as a single string.
    def summarize(self, lines):
        kept = [l.text for l in lines if len(l.text.split()) >= self.min_line_words]
        # One line per sentence: end every line with a full stop so summa's sentence splitter keeps lines whole.
        text = "\n".join(t if t[-1] in ".?!" else t + "." for t in kept)
        return " ".join(summarizer.summarize(text, words=self.words, split=True))
