"""One meeting (one bot): who is in it, the consent state, the transcript, and its files on disk.

sessions/<bot_id>/
  transcript.txt   "[mm:ss] Name: text", the output the later pipeline steps read
  pieces.jsonl     one line per Groq upload: speaker, timings, text
  consent.jsonl    every consent event (kept even when someone says no)
  events.jsonl     every webhook from Attendee, raw
  captions.txt     the platform's own captions, for comparison
  notes.json       live notes from the LLM: summary, action items, a log of every call (notes.py)
  pieces/NNNN.wav  the audio that was uploaded
"""

import asyncio
import json
import re
import shutil
import time
from dataclasses import dataclass

import settings
from live_view import activity_line


def now_ms():
    return int(time.time() * 1000)


def mmss(ms):
    s = max(0, ms) // 1000
    return f"{s // 60:02d}:{s % 60:02d}"


def talk_spans(events, until_ms):
    """[(timestamp, "speech_start" | "speech_stop")] -> [(start, end)] talking spans.
    Webhooks can arrive out of order, so sort by their own timestamps first.
    A start without a stop yet means still talking: the span runs to until_ms."""
    spans, started = [], None
    for ts, kind in sorted(events):
        if kind == "speech_start" and started is None:
            started = ts
        elif kind == "speech_stop" and started is not None:
            spans.append((started, ts))
            started = None
    if started is not None:
        spans.append((started, until_ms))
    return spans



@dataclass
class Person:
    name: str
    present: bool = False
    decision: str | None = None   # "yes", "no", or None (no answer yet)
    asked_ms: int | None = None   # when they were asked, for the reminder/leave timers
    reminded: bool = False
    vouched_by: str | None = None  # who answered for them (a room, phone line or bot)


class Session:
    def __init__(self, bot_id, consent_required):
        self.bot_id = bot_id
        self.dir = settings.SESSIONS_DIR / bot_id
        (self.dir / "pieces").mkdir(parents=True, exist_ok=True)
        self.events = open(self.dir / "events.jsonl", "a")
        self.piece_log = open(self.dir / "pieces.jsonl", "a")
        self.bot_name = None
        self.state = "starting"  # Attendee's bot state, for the live view
        self.demo = False        # Demo mode (ui/demo.py): a scripted meeting, no real bot behind it

        # audio and transcript
        self.names = {}       # participant uuid -> display name
        self.speakers = {}    # stream uuid -> audio.Speaker
        self.t0_ms = None     # wall clock of the first audio: transcript times count from here
        self.lines = []       # (start_ms, end_ms, speaker, text), one per transcribed piece
        self.n = 0            # pieces uploaded so far
        # Teams: speech start/stop events per participant, used to name the mixed audio.
        self.talk = {}        # uuid -> [(timestamp_ms, "speech_start" | "speech_stop"), ...]

        # consent
        self.consent_required = consent_required
        self.people = {}      # uuid -> Person
        self.recording = not consent_required  # THE GATE: audio is only kept while this is True
        self.prompted = False   # consent request sent
        self.declined = False   # someone said no
        self.leaving = False    # timed out waiting for answers
        self.sent_texts = set()  # our own chat messages, which come back through the chat webhook

        # live notes (notes.py): summary + action items from the LLM, None until the first audio
        self.notes = None

        # live view
        self.activity = []    # [{time, kind, text}], the "Bot activity" column
        self.link_posted = False
        self.version = 0      # goes up on every change the live view shows (touch)
        self._changed = asyncio.Event()

    def touch(self):
        """Something the live view shows changed: answer the pages waiting in wait_for_change."""
        self.version += 1
        self._changed.set()
        self._changed = asyncio.Event()

    async def wait_for_change(self, version, timeout_s):
        """Return once version has moved past `version`, or after timeout_s (a long poll)."""
        if self.version == version:
            changed = self._changed
            try:
                await asyncio.wait_for(changed.wait(), timeout_s)
            except asyncio.TimeoutError:
                pass

    def name(self, uuid):
        return self.names.get(uuid) or f"?{uuid[-6:]}"

    def present(self):
        return [p for p in self.people.values() if p.present]

    def is_teams(self):
        # Teams participant ids look like "8:orgid:…" or "8:teamsvisitor:…"; Meet's don't.
        return any(re.match(r"^\d+:", uuid or "") for uuid in self.people)

    def chat_sender(self, uuid, name):
        """The Person who sent a chat message. Chat ids can differ from participant ids,
        so fall back to the display name (exact, then ignoring case) if it fits one person."""
        if uuid in self.people:
            return self.people[uuid]
        exact = [p for p in self.present() if p.name == name]
        any_case = [p for p in self.present() if (p.name or "").casefold() == (name or "").casefold()]
        matches = exact or any_case
        return matches[0] if len(matches) == 1 else None

    def talked(self, lo, hi):
        """Teams: {uuid: ms that person talked between lo and hi}, from the speech start/stop events."""
        talk_ms = {}
        for uuid, events in self.talk.items():
            ms = sum(max(0, min(hi, end) - max(lo, start)) for start, end in talk_spans(events, hi))
            if ms > 0:
                talk_ms[uuid] = ms
        return talk_ms

    def who_spoke(self, piece):
        """Teams: name a piece of the mixed audio from the speech start/stop events.
        Never guesses: one name, "Multiple speakers (A, B)", or "Unknown speaker"."""
        lo, hi = piece.start_ms, piece.speech_end_ms
        talk_ms = self.talked(lo, hi)  # uuid -> ms talked inside this piece
        piece.talk_ms = {self.name(u): ms for u, ms in talk_ms.items()}
        # Enough talk to be named: 25% of the piece, and at least MIN_TALK_MS so a brief blip in a long
        # piece doesn't count. A one-word piece can be shorter than MIN_TALK_MS, so there half the piece is enough.
        need = max(0.25 * (hi - lo), min(settings.MIN_TALK_MS, 0.5 * (hi - lo)))
        names = [self.name(u) for u, ms in sorted(talk_ms.items(), key=lambda kv: -kv[1]) if ms >= need]
        if len(names) == 1:
            return names[0]
        if names:
            return f"Multiple speakers ({', '.join(names)})"
        return "Unknown speaker"

    # ---- logs ----
    def note(self, kind, text):
        """A line in the live view's activity column (memory only)."""
        self.activity.append({"time": time.strftime("%H:%M:%S"), "ms": now_ms(), "kind": kind, "text": text})
        self.touch()

    def consent_log(self, event, **fields):
        """Record a consent event in consent.jsonl and show it in the live view."""
        entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "ms": now_ms(), "event": event, **fields}
        with open(self.dir / "consent.jsonl", "a") as f:
            f.write(json.dumps(entry) + "\n")
        self.note(*activity_line(event, fields))

    # ---- transcript ----
    def merged(self):
        """Transcript lines [(mm:ss, speaker, text)], sorted by start time, with back-to-back
        pieces from the same speaker joined. Rebuilt every time, so a late piece still lands right."""
        out = []  # [start_ms, end_ms, speaker, text]
        for start, end, who, text in sorted(self.lines):
            last = out[-1] if out else None
            if last and last[2] == who and settings.MERGE_GAP_MS > 0 and start - last[1] <= settings.MERGE_GAP_MS:
                last[1] = max(last[1], end)
                last[3] += " " + text
            else:
                out.append([start, end, who, text])
        return [(mmss(start - self.t0_ms), who, text) for start, _, who, text in out]

    def write_transcript(self):
        if not self.declined:
            lines = [f"[{t}] {who}: {text}\n" for t, who, text in self.merged()]
            (self.dir / "transcript.txt").write_text("".join(lines))

    # ---- end ----
    def wipe(self):
        """Someone said no: delete everything except consent.jsonl (the record of the no)."""
        self.events.close()
        self.piece_log.close()
        self.lines.clear()
        self.notes = None
        for path in self.dir.iterdir():
            if path.name != "consent.jsonl":
                shutil.rmtree(path) if path.is_dir() else path.unlink()

    def close(self):
        self.write_transcript()
        self.events.close()
        self.piece_log.close()
