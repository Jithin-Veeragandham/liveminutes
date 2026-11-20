"""LLM row: rolling meeting notes (a summary + action items) from any provider in llm_providers.py.

The meeting is cut into chunks of about LLM["chunk_seconds"]. For each chunk the LLM gets the notes so far plus
the new part of the transcript, and returns the updated notes for the whole meeting so far ("recursive"
summarization). That is how notes are kept up to date during a live meeting: each call sees only the new part
plus a compact memory of everything before it, so the cost per call stays flat however long the meeting runs.

The reply is structured (JSON schema below): a summary, and action items with the owner's name when one is said.

    python 2_summarization/llm_summarizer.py 1_ingestion/sessions/<bot_id>/transcript.txt   # notes for a live meeting
    python 2_summarization/run.py --model llm --split smoke                                # scored on AMI (ROUGE)

Needs ANTHROPIC_API_KEY in the repo's .env (for the default provider).
"""
import argparse
import json
import re
import sys
import time
from collections import namedtuple
from dataclasses import asdict, dataclass, field

from llm_providers import LLMError, make_provider

# The structured reply. Every field is required, so "nobody named" / "no deadline" are empty strings.
SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "action_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "owner": {"type": "string"},
                    "due": {"type": "string"},
                },
                "required": ["task", "owner", "due"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "action_items"],
    "additionalProperties": False,
}

SYSTEM = """You keep the minutes of a meeting while it is happening. Each time, you get the minutes so far and the \
next part of the transcript, and you return updated minutes for the whole meeting so far.

The transcript is automatic speech recognition, so it has mistakes. Read through them and write what was meant:
- Each line is "[mm:ss] Speaker: text". The speaker is a name, a role, "Unknown speaker", or "Multiple speakers \
(A, B)" when people overlapped (then any of them may have said it: use the content to tell who).
- A label can be "Last, First" (Teams writes "Veeragandham, Jithin"): that is ONE person, Jithin Veeragandham. \
Write people as "First Last".
- Names are often misheard when people address each other. The speaker labels have the correct spellings: when a \
name in the text sounds like a participant's name, it is that participant.
- Technical terms and product names get misheard too (for example "Rook score" for "ROUGE score", "EMI" for the \
"AMI" corpus). Correct them when the context makes the intended word clear. File names spoken aloud are written \
as files: "results underscore log dot JSON" is results_log.json.
- Drop filler, false starts and fragments that carry no meaning. If a passage is too garbled to understand, leave \
it out rather than guess.

summary: what the meeting has covered from its start until now, and what was decided, in readable sentences of \
plain prose (third person, past tense), never a list of keywords. Group what was said into a few main themes, in \
the order they came up, with one or two sentences each. When you add the new part, fold it into an existing \
theme or add a theme, and shorten older themes rather than dropping them, so the start of the meeting stays in \
the summary. Drop small talk and minor details, and stay under {words} words. Refer to people by name or role; \
use "they" rather than guessing anyone's gender.

action_items: every task someone agreed to do, was asked to do, or that the group said needs doing. Tasks are \
often phrased indirectly, and these count too: "we need to ...", "we have to ...", "you have to ...", "X is \
missing, it should be there", "we should ask him for ...", "just drop a message when you're done". Keep the \
earlier items unless the new part cancels or completes them, and update an item when the new part changes it \
(new owner, new deadline, more detail).
One item per task: never list the same task twice, and never combine different tasks into one item. Before you \
answer, compare every new item with the existing list. If it is the same task as an existing item (the same \
change to the same thing, even in other words), don't add it: fold any new detail into that item. If it is a \
different change, even to the same file, it is its own item. An item is one short sentence.
Leave out ideas that were rejected ("I'll remove that" followed by "don't worry about it").
- task: a short imperative that says exactly what to do, with the specifics that were mentioned (which file, \
which number, for whom): "Add the split file's path and hashes to MANIFEST.json", not "Update the manifest".
- owner: who will do it. "I'll ..." means the speaker; "can you ...", "you'll ...", "X will be ..." means the \
person named or addressed (usually named just before, often misheard, or the one who answers "okay"); never the \
speaker giving the instruction unless they take it on themselves. Use the participant's name as the labels \
write it. Use "" when nobody was given the task or it is unclear.
- due: the deadline as it was said ("by Friday", "tonight", "before the call with the instructor"), or "" if none.

<earlier_lines>, when present, are the last lines of the previous part, sent again for context. Use them to \
understand the new part (who "you" is, what an "okay, I'll do it" agrees to), and don't add twice what the minutes \
already have from them. A task can be split across the two parts: asked for or offered in <earlier_lines>, and only \
accepted, given an owner or given a deadline in the new part. If the minutes don't have that task yet, add it now.

Use only what the transcript says: do not invent people, dates or tasks."""


@dataclass
class ActionItem:
    task: str
    owner: str = ""
    due: str = ""


@dataclass
class Notes:
    summary: str = ""
    action_items: list = field(default_factory=list)   # list of ActionItem

    @classmethod
    def from_dict(cls, d):
        return cls(d["summary"].strip(), [ActionItem(a["task"].strip(), a["owner"].strip(), a["due"].strip())
                                          for a in d["action_items"]])

    def to_dict(self):
        return asdict(self)


# One transcript line, the same shape as ami.Line: who spoke, when (seconds from the start), what they said.
Line = namedtuple("Line", "speaker start text")


# Cut time-ordered lines into consecutive chunks of about `seconds` each (the last one can be shorter).
def make_chunks(lines, seconds):
    chunks, current, start = [], [], None
    for line in lines:
        start = line.start if start is None else start
        if line.start - start >= seconds and current:
            chunks.append(current)
            current, start = [], line.start
        current.append(line)
    return chunks + ([current] if current else [])


def mmss(seconds):
    s = int(seconds)
    return f"{s // 60:02d}:{s % 60:02d}"


class LLMSummarizer:
    """Rolling notes: one LLM call per chunk, each given the notes so far."""

    # cfg = settings.LLM (+ "summary_words" filled in). speaker_names turns codes into names, e.g. "PM" -> "Project Manager".
    def __init__(self, cfg, speaker_names=None, provider=None, clock=time.monotonic, sleep=time.sleep):
        self.cfg = cfg
        self.speaker_names = speaker_names or {}
        self.provider = provider or make_provider(cfg)
        self.version = f"{self.provider.name}:{cfg['model']}"
        self.system = SYSTEM.format(words=cfg["summary_words"])
        self.last_notes = None   # notes of the last meeting summarized (run.py logs its action items)
        self.clock, self.sleep, self.last_call = clock, sleep, None

    # Wait until at least min_seconds_between_calls have passed since the previous call (rate-limit pacing).
    def wait_turn(self):
        gap = self.cfg.get("min_seconds_between_calls") or 0
        if self.last_call is not None and gap:
            left = gap - (self.clock() - self.last_call)
            if left > 0:
                self.sleep(left)
        self.last_call = self.clock()

    def as_transcript(self, lines):
        return "\n".join(f"[{mmss(l.start)}] {self.speaker_names.get(l.speaker, l.speaker)}: {l.text}" for l in lines)

    # One step: the notes so far + the next chunk -> the updated notes. total=None: still going (a live meeting).
    # context: the last lines of the previous chunk, already in the notes, sent again so that a reply ("okay, I'll
    # do it tonight") is understood together with the question it answers.
    def update(self, notes, chunk, part, total=None, context=()):
        label = f"{part} of {total}" if total else f"{part}, meeting still going"
        earlier = (f"<earlier_lines>\n{self.as_transcript(context)}\n</earlier_lines>\n\n" if context else "")
        user = (f"<minutes_so_far>\n{json.dumps(notes.to_dict(), indent=1)}\n</minutes_so_far>\n\n{earlier}"
                f'<transcript part="{label}" from="{mmss(chunk[0].start)}" to="{mmss(chunk[-1].start)}">\n'
                f"{self.as_transcript(chunk)}\n</transcript>")
        self.wait_turn()
        return Notes.from_dict(self.provider.generate_json(self.system, user, SCHEMA, self.cfg["max_tokens"]))

    # Whole meeting: start from empty notes and fold in every chunk in order. on_update(part, total, notes) is
    # called after each chunk, e.g. to show the notes growing, as a live meeting would.
    def notes(self, lines, on_update=None):
        notes = Notes()
        chunks = make_chunks(lines, self.cfg["chunk_seconds"])
        keep = self.cfg.get("context_lines", 0)
        for i, chunk in enumerate(chunks, 1):
            context = chunks[i - 2][-keep:] if i > 1 and keep else []
            notes = self.update(notes, chunk, i, len(chunks), context=context)
            if on_update:
                on_update(i, len(chunks), notes)
        self.last_notes = notes
        return notes

    # The interface run.py uses for every model: lines in, summary text out (ROUGE scores the summary).
    def summarize(self, lines):
        return self.notes(lines).summary


# ---------------------------------------------------------------- live meetings (the bot's transcript.txt)

TRANSCRIPT_LINE = re.compile(r"^\[(\d+):(\d{2})\] (.*?): (.*)$")


# Read the meeting bot's transcript.txt ("[mm:ss] Name: text" per line) into Lines.
def read_transcript(path):
    lines = []
    with open(path, encoding="utf-8") as f:
        for raw in f:
            m = TRANSCRIPT_LINE.match(raw.rstrip("\n"))
            if m:
                lines.append(Line(m.group(3), int(m.group(1)) * 60 + int(m.group(2)), m.group(4)))
    return lines


def print_notes(notes):
    print(f"\nSUMMARY\n{notes.summary}\n\nACTION ITEMS")
    for a in notes.action_items or []:
        print(f"- {a.task}" + (f"  (owner: {a.owner})" if a.owner else "") + (f"  (due: {a.due})" if a.due else ""))
    if not notes.action_items:
        print("(none)")


def main():
    import settings
    p = argparse.ArgumentParser(description="Rolling summary + action items for a meeting transcript.")
    p.add_argument("transcript", help="a transcript.txt written by the meeting bot")
    p.add_argument("--json", help="also write the final notes to this JSON file")
    p.add_argument("--no-wait", action="store_true", help="don't space calls out (risks the provider's rate limits)")
    args = p.parse_args()

    lines = read_transcript(args.transcript)
    if not lines:
        sys.exit(f"no '[mm:ss] Name: text' lines in {args.transcript}")
    cfg = dict(settings.LLM, summary_words=settings.LLM["summary_words"] or settings.LLM_LIVE_SUMMARY_WORDS)
    if args.no_wait:
        cfg["min_seconds_between_calls"] = 0
    model = LLMSummarizer(cfg)
    n = len(make_chunks(lines, cfg["chunk_seconds"]))
    gap = cfg.get("min_seconds_between_calls") or 0
    print(f"{len(lines)} lines, {mmss(lines[-1].start)} long; {model.version}; {n} chunks of {cfg['chunk_seconds']} s"
          + (f", one call every {gap} s: about {mmss((n - 1) * gap)} (mm:ss) in total" if gap else ""))

    def show(part, total, notes):
        print(f"  [{mmss(time.monotonic() - started)}] chunk {part}/{total}: {len(notes.summary.split())}-word summary, "
              f"{len(notes.action_items)} action items", flush=True)

    started = time.monotonic()

    try:
        notes = model.notes(lines, on_update=show)
    except LLMError as e:
        sys.exit(f"LLM error: {e}")
    print_notes(notes)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"model": model.version, "usage": model.provider.usage, **notes.to_dict()}, f, indent=2)
        print(f"\nwrote {args.json}")
    u = model.provider.usage
    print(f"\n{u['calls']} calls, {u['input_tokens']} input + {u['output_tokens']} output tokens, served by {u['served_by']}")


if __name__ == "__main__":
    main()
