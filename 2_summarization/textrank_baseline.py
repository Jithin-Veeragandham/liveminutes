"""Baseline: TextRank (summanlp/textrank, pip package "summa").

Builds a graph whose nodes are transcript lines and whose edges are word overlap between lines, runs
PageRank on it, and keeps the most central lines (in meeting order) up to a word budget.
Extractive: every output line is something someone actually said. No training, milliseconds on a CPU.

Two modes (settings.TEXTRANK["mode"], or run.py --mode):
  full       one TextRank over the whole meeting (needs the whole transcript first: not possible live).
  recursive  the way a live summary is built: the meeting arrives in chunks of chunk_seconds. After each chunk,
             TextRank ranks (the lines kept so far + the new chunk's lines) and keeps the best up to
             words x buffer. The buffer (e.g. 1.5x the budget) leaves room so a line that only becomes central
             later is not thrown away too early. At the end, one last ranking trims the kept lines to the budget.

Options found by the hyperparameter search (2_summarization/hpo/):
  clean          drop fillers ("um", "uh", "mm-hmm") and immediately repeated words ("the the") before ranking
  speaker_names  start each kept line with who said it ("Project Manager: ..."), as a summary would show it
"""
import importlib.metadata
import re
import warnings
from collections import namedtuple

from chunks import make_chunks

# summa 1.2.0 has old-style regex strings that make Python 3.12 print harmless SyntaxWarnings on first import.
with warnings.catch_warnings():
    warnings.simplefilter("ignore", SyntaxWarning)
    from summa import summarizer

ROLE_NAMES = {"PM": "Project Manager", "ME": "Marketing Expert",
              "ID": "Industrial Designer", "UI": "User Interface Designer"}
FILLERS = re.compile(r"\b(?:mm-hmm|um-hmm|uh-huh|uhm|um+|uh+|erm?|ah+|hmm+|mm+)\b[,.]?\s*", re.IGNORECASE)
REPEAT = re.compile(r"\b(\w+)(?:\s+\1\b)+", re.IGNORECASE)


# Remove fillers and stutters: "Um, so the the remote, uh, is" -> "so the remote, is".
def clean_text(text):
    text = REPEAT.sub(r"\1", FILLERS.sub("", text))
    return re.sub(r"\s+", " ", text).strip(" ,")


# One line per sentence: end every line with a full stop so summa's sentence splitter keeps lines whole.
def as_sentence(text):
    return text if text[-1] in ".?!" else text + "."


class TextRankSummarizer:
    """Summarize a meeting by picking its most central lines."""

    # words: word budget of the summary; min_line_words: drop shorter lines ("yeah.") before ranking.
    # mode "recursive" also uses chunk_seconds and buffer; clean and speaker_names: see the module docstring.
    def __init__(self, words, min_line_words=4, mode="full", chunk_seconds=30, buffer=1.5,
                 clean=False, speaker_names=False):
        self.words = words
        self.min_line_words = min_line_words
        self.mode, self.chunk_seconds, self.buffer = mode, chunk_seconds, buffer
        self.clean, self.speaker_names = clean, speaker_names
        self.version = "summa " + importlib.metadata.version("summa")

    # The most central of these sentences (in their original order), up to `words` words.
    # Short input is kept whole: ranking 3 sentences to keep 3 is pointless (and summa can return nothing).
    def rank(self, sentences, words):
        if sum(len(s.split()) for s in sentences) <= words:
            return sentences
        kept = summarizer.summarize("\n".join(sentences), words=words, split=True)
        return kept or sentences[-1:]   # no word overlap between any lines: keep the newest one rather than nothing

    # The lines worth ranking, as (start, sentence): cleaned and filtered, optionally with the speaker in front.
    def sentences(self, lines):
        out = []
        for l in lines:
            text = clean_text(l.text) if self.clean else l.text
            if text and len(text.split()) >= self.min_line_words:
                out.append(Sentence(l.start, as_sentence(
                    f"{ROLE_NAMES.get(l.speaker, l.speaker)}: {text}" if self.speaker_names else text)))
        return out

    # Return the summary of one meeting (a list of Line objects) as a single string.
    def summarize(self, lines):
        useful = self.sentences(lines)
        if self.mode == "full":
            return " ".join(self.rank([s.text for s in useful], self.words))
        kept = []
        for chunk in make_chunks(useful, self.chunk_seconds):
            kept = self.rank(kept + [s.text for s in chunk], round(self.words * self.buffer))
        return " ".join(self.rank(kept, self.words))


Sentence = namedtuple("Sentence", "start text")
