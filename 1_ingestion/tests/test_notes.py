"""Offline tests for live notes (notes.py) and the notes panel data (live_view.notes_view). A fake LLM, no network.

Run (from 1_ingestion/):  python -m unittest tests.test_notes
"""

import asyncio
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.test_consent import FakeBridge  # noqa: E402  (also points settings at a temp sessions dir)
import live_view  # noqa: E402
import notes  # noqa: E402
import settings  # noqa: E402
from session import now_ms  # noqa: E402


class FakeLLM:
    """Answers every call with notes that count the calls; can be told to fail."""
    name = "fake"

    def __init__(self):
        self.prompts, self.fail = [], False
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "served_by": ["fake-1"]}

    def generate_json(self, system, user, schema, max_tokens):
        if self.fail:
            raise RuntimeError("service unavailable")
        self.prompts.append(user)
        self.usage["calls"] += 1
        self.usage["input_tokens"] += 100
        self.usage["output_tokens"] += 20
        n = len(self.prompts)
        return {"summary": f"summary {n}", "action_items": [{"task": "Send the budget", "owner": "Priya", "due": "Friday"}]}


class NotesTest(unittest.TestCase):
    def setUp(self):
        self.llm = FakeLLM()
        self.ln = notes.LiveNotes(provider=self.llm)
        self.assertTrue(self.ln.enabled, self.ln.reason)
        self.b = FakeBridge()
        self.s = self.b.session("bot_notes")
        self.s.t0_ms = now_ms() - 120_000          # the meeting started 2 min ago
        self.s.lines = [(self.s.t0_ms + 5_000, self.s.t0_ms + 8_000, "Priya", "I will send the budget by Friday."),
                        (self.s.t0_ms + 30_000, self.s.t0_ms + 33_000, "Sam", "Great, thanks.")]

    def tearDown(self):
        self.s.events.close()
        self.s.piece_log.close()

    def run_(self, coro):
        return asyncio.run(coro)

    def due_now(self):
        self.ln.state(self.s).next_due_ms = now_ms() - 1

    def test_first_update_one_interval_after_first_audio(self):
        self.s.t0_ms = now_ms() - 10_000           # audio started 10 s ago
        self.run_(self.ln.maybe_update(self.s))
        self.assertEqual(self.s.notes.next_due_ms, self.s.t0_ms + settings.NOTES_INTERVAL_S * 1000)
        self.assertEqual(self.llm.prompts, [])     # not due yet

    def test_update_sends_new_lines_and_saves(self):
        self.due_now()
        self.run_(self.ln.maybe_update(self.s))
        st = self.s.notes
        self.assertEqual(len(self.llm.prompts), 1)
        self.assertIn("[00:05] Priya: I will send the budget by Friday.", self.llm.prompts[0])
        self.assertIn("meeting still going", self.llm.prompts[0])
        self.assertEqual(st.notes.summary, "summary 1")
        self.assertEqual(st.part, 1)
        self.assertGreater(st.next_due_ms, now_ms() + (settings.NOTES_INTERVAL_S - 2) * 1000)
        saved = json.loads((self.s.dir / "notes.json").read_text())
        self.assertEqual(saved["action_items"][0]["owner"], "Priya")
        self.assertEqual(saved["history"][-1]["status"], "ok")
        self.assertEqual(saved["history"][-1]["input_tokens"], 100)

    def test_next_update_sends_only_new_lines_with_old_notes(self):
        self.due_now()
        self.run_(self.ln.maybe_update(self.s))
        self.s.lines.append((now_ms() - 20_000, now_ms() - 18_000, "Sam", "I'll book the room."))
        self.due_now()
        self.run_(self.ln.maybe_update(self.s))
        second = self.llm.prompts[1]
        self.assertIn("I'll book the room.", second)
        self.assertNotIn("send the budget", second.split("<transcript")[1])  # old line not in the new part
        self.assertIn("summary 1", second)                       # the notes so far go along
        self.assertIn("<earlier_lines>", second)                 # and the last lines before, as context
        self.assertIn("Great, thanks.", second.split("<earlier_lines>")[1].split("</earlier_lines>")[0])

    def test_lines_still_settling_wait(self):
        self.s.lines.append((now_ms() - 1_000, now_ms(), "Sam", "just said"))
        self.due_now()
        self.run_(self.ln.maybe_update(self.s))
        self.assertNotIn("just said", self.llm.prompts[0])
        self.due_now()
        self.run_(self.ln.maybe_update(self.s, final=True))   # at the end, everything goes
        self.assertIn("just said", self.llm.prompts[1])

    def test_no_new_speech_is_skipped(self):
        self.due_now()
        self.run_(self.ln.maybe_update(self.s))
        self.due_now()
        self.run_(self.ln.maybe_update(self.s))
        self.assertEqual(len(self.llm.prompts), 1)
        self.assertEqual(self.s.notes.history[-1]["status"], "skipped")

    def test_failure_keeps_notes_and_resends(self):
        self.due_now()
        self.run_(self.ln.maybe_update(self.s))
        self.s.lines.append((now_ms() - 20_000, now_ms() - 18_000, "Sam", "I'll book the room."))
        self.llm.fail = True
        self.due_now()
        self.run_(self.ln.maybe_update(self.s))
        st = self.s.notes
        self.assertEqual(st.notes.summary, "summary 1")
        self.assertIn("service unavailable", st.error)
        self.assertEqual(st.history[-1]["status"], "error")
        self.llm.fail = False
        self.due_now()
        self.run_(self.ln.maybe_update(self.s))
        self.assertIn("I'll book the room.", self.llm.prompts[-1])
        self.assertIsNone(st.error)

    def test_rate_cap_postpones(self):
        self.ln.calls.extend([now_ms()] * settings.NOTES_RPM_LIMIT)
        self.due_now()
        self.run_(self.ln.maybe_update(self.s))
        self.assertEqual(self.llm.prompts, [])
        self.assertLess(self.s.notes.next_due_ms, now_ms() + 10_000)

    def test_declined_session_gets_nothing(self):
        self.s.declined = True
        self.due_now()
        self.run_(self.ln.maybe_update(self.s))
        self.assertEqual(self.llm.prompts, [])

    def test_panel_data(self):
        self.due_now()
        self.run_(self.ln.maybe_update(self.s))
        v = live_view.notes_view(self.ln, self.s)
        self.assertEqual(v["summary"], "summary 1")
        self.assertEqual(v["action_items"], [{"task": "Send the budget", "owner": "Priya", "due": "Friday"}])
        self.assertEqual(v["interval_s"], settings.NOTES_INTERVAL_S)
        self.assertEqual(v["rate"]["minute"], 1)
        self.assertEqual(v["history"][-1]["lines"], 2)
        self.assertIn("[00:30] Sam: Great, thanks.", v["history"][-1]["sent"])
        saved = live_view.saved_notes("bot_notes")
        self.assertTrue(saved["saved"])
        self.assertEqual(saved["updates"], 1)

    def test_turned_off_without_key(self):
        class NoKey:
            name = "x"

            def __init__(self):
                raise SystemExit("No Gemini API key found")
        # A provider that can't start (no key): the bridge must keep running and the page must say why.
        import llm_providers   # on the path once notes.LiveNotes() has loaded the summarizer (setUp)
        saved = llm_providers.make_provider
        llm_providers.make_provider = lambda cfg: NoKey()
        try:
            ln = notes.LiveNotes()
            self.assertFalse(ln.enabled)
            self.assertIn("No Gemini API key", ln.reason)
            self.assertFalse(live_view.notes_view(ln, self.s)["enabled"])
        finally:
            llm_providers.make_provider = saved


if __name__ == "__main__":
    unittest.main()
