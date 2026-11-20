"""Live meeting notes: a running summary + action items, updated during the meeting by the LLM summarizer.

Every NOTES_INTERVAL_S (settings.py) the transcript lines that came in since the last update go to the LLM
together with the notes so far (2_summarization/llm_summarizer.py, the same code as the offline command), and
the updated notes replace the old ones. One LLM call at a time for the whole bridge, and never more than
NOTES_RPM_LIMIT calls in a minute.

sessions/<bot_id>/notes.json   the latest notes + a log of every call (what was sent, how long it took, tokens)

The live view shows the summary, the action items, a countdown to the next update, the rate-limit use and
the call log (live_view.notes_view).
"""

import asyncio
import importlib.util
import json
import sys
import time
from collections import deque

import settings
from session import mmss, now_ms

MAX_HISTORY = 200   # calls kept in notes.json / shown on the page


def load_summarizer():
    """(llm_summarizer module, the summarization settings module). The summarization folder has its own
    settings.py, which would clash with ours by name, so that one is loaded under another name."""
    folder = settings.SUMMARIZATION_DIR
    if str(folder) not in sys.path:
        sys.path.append(str(folder))   # appended: our own settings.py stays first on the path
    import llm_summarizer
    spec = importlib.util.spec_from_file_location("summarization_settings", folder / "settings.py")
    summ = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(summ)
    return llm_summarizer, summ


class NotesState:
    """One meeting's notes and update bookkeeping."""

    def __init__(self, summarizer):
        self.summarizer = summarizer     # llm_summarizer.LLMSummarizer
        self.notes = None                # llm_summarizer.Notes, None until the first update
        self.part = 0                    # updates done
        self.sent_until_ms = 0           # the latest start of a line sent so far
        self.sent = set()                # (start ms, speaker, text) of every line sent. A line transcribed late
                                         # (Groq backlog, retries) that starts before sent_until_ms is still sent
                                         # next time; with only a time watermark it was skipped for good.
        self.next_due_ms = None          # when the next update runs
        self.busy = False                # a call is running now
        self.busy_since_ms = None        # when it started (the page says "delayed" if it runs long)
        self.error = None                # the last call's error, cleared by the next good one
        self.updated_ms = None
        self.history = []                # one dict per call (or skipped update), newest last
        self.context = []                # last lines of the previous update, sent again as context


class LiveNotes:
    """Runs the note updates for every session of the bridge."""

    def __init__(self, provider=None):
        self.enabled, self.reason = False, ""
        self.calls = deque()             # time (ms) of every LLM call in the last 24 h, for the rate bars
        self.lock = asyncio.Lock()       # one call at a time across all meetings
        self.tokens = {"input": 0, "output": 0}
        if not settings.NOTES_ENABLED:
            self.reason = "turned off (LIVE_NOTES=0)"
            return
        try:
            self.mod, summ = load_summarizer()
            self.cfg = dict(summ.LLM, summary_words=summ.LLM["summary_words"] or summ.LLM_LIVE_SUMMARY_WORDS,
                            chunk_seconds=settings.NOTES_INTERVAL_S, min_seconds_between_calls=0)
            if provider is None:
                from llm_providers import make_provider
                provider = make_provider(self.cfg)
            self.provider = provider
            self.model = f"{provider.name}:{self.cfg['model']}"
            self.enabled = True
        except SystemExit as e:          # e.g. no API key: the bridge still runs, the page says why
            self.reason = str(e)
        except Exception as e:           # summarizer code missing or broken: same
            self.reason = f"{type(e).__name__}: {e}"

    def state(self, s):
        if s.notes is None:
            s.notes = NotesState(self.mod.LLMSummarizer(self.cfg, provider=self.provider))
        return s.notes

    def calls_since(self, ms):
        return sum(1 for t in self.calls if t >= ms)

    async def watch(self, sessions):
        """Background job: check every session once a second, update the ones that are due."""
        while True:
            for s in list(sessions.values()):
                try:
                    await self.maybe_update(s)
                except Exception as e:   # never let one meeting stop the loop
                    print(f"notes: {s.bot_id}: {type(e).__name__}: {e}", flush=True)
            await asyncio.sleep(1)

    async def maybe_update(self, s, final=False):
        """Send the new lines if an update is due. final=True (the meeting ended): send what's left now."""
        if not self.enabled or s.declined or s.t0_ms is None or getattr(s, "demo", False):   # Demo: scripted notes
            return
        st = self.state(s)
        now = now_ms()
        if st.next_due_ms is None:       # first update one interval after the first audio
            st.next_due_ms = s.t0_ms + settings.NOTES_INTERVAL_S * 1000
            s.touch()
        if st.busy or (now < st.next_due_ms and not final):
            return
        if self.calls_since(now - 60_000) >= settings.NOTES_RPM_LIMIT:
            st.next_due_ms = now + 5_000   # our own per-minute cap: try again shortly
            s.touch()
            return
        # Lines still being transcribed can land slightly out of order, so leave the last few seconds for next time.
        settle = 0 if final else settings.NOTES_SETTLE_MS
        new = sorted(l for l in s.lines if (l[0], l[2], l[3]) not in st.sent and l[0] <= now - settle)
        st.next_due_ms = now + settings.NOTES_INTERVAL_S * 1000
        if not new:
            if not final:
                self.log(st, {"status": "skipped", "note": "no new speech since the last update"})
                s.touch()
            return
        await self.update(s, st, new)

    async def update(self, s, st, new):
        Line = self.mod.Line
        chunk = [Line(who, (start - s.t0_ms) / 1000, text) for start, _, who, text in new]
        before = dict(self.provider.usage)
        entry = {"from": mmss(new[0][0] - s.t0_ms), "to": mmss(new[-1][0] - s.t0_ms), "lines": len(chunk),
                 "words": sum(len(l.text.split()) for l in chunk), "sent": st.summarizer.as_transcript(chunk)}
        st.busy, st.busy_since_ms = True, now_ms()
        s.touch()
        started = time.monotonic()
        try:
            async with self.lock:
                self.calls.append(now_ms())
                while self.calls and self.calls[0] < now_ms() - 86_400_000:
                    self.calls.popleft()
                notes = await asyncio.to_thread(st.summarizer.update, st.notes or self.mod.Notes(), chunk, st.part + 1,
                                                None, st.context)
        except SystemExit as e:          # no API key, or it was rejected: stop the notes (the page says why)
            st.error = str(e)[:300]
            entry.update(status="error", error=st.error)
            self.enabled, self.reason = False, str(e)
        except Exception as e:           # LLMError, network, ...: keep the old notes, resend these lines next time
            st.error = str(e)[:300]
            entry.update(status="error", error=st.error)
        else:
            st.notes, st.error, st.updated_ms = notes, None, now_ms()
            st.part += 1
            st.sent.update((l[0], l[2], l[3]) for l in new)
            st.sent_until_ms = max(st.sent_until_ms, new[-1][0])
            st.context = chunk[-self.cfg.get("context_lines", 0):] if self.cfg.get("context_lines") else []
            entry.update(status="ok", summary_words=len(notes.summary.split()), action_items=len(notes.action_items))
        finally:
            st.busy = False
        entry["seconds"] = round(time.monotonic() - started, 2)
        entry["input_tokens"] = self.provider.usage.get("input_tokens", 0) - before.get("input_tokens", 0)
        entry["output_tokens"] = self.provider.usage.get("output_tokens", 0) - before.get("output_tokens", 0)
        self.tokens["input"] += entry["input_tokens"]
        self.tokens["output"] += entry["output_tokens"]
        self.log(st, entry)
        if not s.declined:
            self.save(s, st)
        s.touch()

    def log(self, st, entry):
        st.history.append({"time": time.strftime("%H:%M:%S"), "ms": now_ms(), "part": st.part, **entry})
        del st.history[:-MAX_HISTORY]

    def save(self, s, st):
        out = {"model": self.model, "updated_ms": st.updated_ms, "interval_s": settings.NOTES_INTERVAL_S,
               **(st.notes.to_dict() if st.notes else {"summary": "", "action_items": []}), "history": st.history}
        (s.dir / "notes.json").write_text(json.dumps(out, indent=1))

    async def finish(self, s):
        """The meeting ended: one last update with whatever wasn't sent yet."""
        if self.enabled and not s.declined and s.t0_ms is not None:
            st = self.state(s)
            for _ in range(30):              # wait for a call that is running now
                if not st.busy:
                    break
                await asyncio.sleep(1)
            await self.maybe_update(s, final=True)
