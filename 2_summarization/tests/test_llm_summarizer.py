"""Offline tests for the LLM summarizer: a fake provider stands in for the API, so no key and no cost.

    python -m unittest discover -s 2_summarization/tests
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_summarizer import SCHEMA, Line, LLMSummarizer, Notes, make_chunks, read_transcript  # noqa: E402

CFG = {"provider": "fake", "model": "fake-1", "chunk_seconds": 300, "summary_words": 160, "max_tokens": 1000}


class FakeProvider:
    """Records every request and answers with notes that mention which chunk it saw."""
    name = "fake"

    def __init__(self):
        self.calls = []
        self.usage = {"calls": 0}

    def generate_json(self, system, user, schema, max_tokens):
        self.calls.append(user)
        n = len(self.calls)
        return {"summary": f"summary after chunk {n}",
                "action_items": [{"task": f"task {i}", "owner": "Priya" if i == 1 else "", "due": ""}
                                 for i in range(1, n + 1)]}


def lines_every(seconds, count, speaker="Priya"):
    return [Line(speaker, i * seconds, f"line {i}") for i in range(count)]


class TestChunks(unittest.TestCase):
    def test_cut_every_chunk_seconds(self):
        chunks = make_chunks(lines_every(60, 12), 300)   # 0..660 s, one line a minute
        self.assertEqual([len(c) for c in chunks], [5, 5, 2])

    def test_short_meeting_is_one_chunk(self):
        self.assertEqual(len(make_chunks(lines_every(10, 5), 300)), 1)


class TestRollingNotes(unittest.TestCase):
    def setUp(self):
        self.fake = FakeProvider()
        self.model = LLMSummarizer(CFG, speaker_names={"PM": "Project Manager"}, provider=self.fake)

    def test_one_call_per_chunk_and_notes_carried_forward(self):
        notes = self.model.notes(lines_every(60, 12))
        self.assertEqual(len(self.fake.calls), 3)
        # First call starts from empty notes; each later call gets the previous reply as its minutes so far.
        self.assertIn('"summary": ""', self.fake.calls[0])
        self.assertIn("summary after chunk 1", self.fake.calls[1])
        self.assertIn("summary after chunk 2", self.fake.calls[2])
        self.assertIn('part="3 of 3"', self.fake.calls[2])
        self.assertEqual(notes.summary, "summary after chunk 3")
        self.assertEqual(len(notes.action_items), 3)
        self.assertEqual(notes.action_items[0].owner, "Priya")
        self.assertEqual(self.model.last_notes, notes)

    def test_each_call_sees_only_its_own_chunk(self):
        self.model.notes(lines_every(60, 12))
        self.assertIn("line 4", self.fake.calls[0])
        self.assertNotIn("line 5", self.fake.calls[0])
        self.assertIn("[05:00] Priya: line 5", self.fake.calls[1])

    def test_previous_lines_sent_as_context(self):
        model = LLMSummarizer(dict(CFG, context_lines=2), provider=self.fake)
        model.notes(lines_every(60, 12))
        self.assertNotIn("<earlier_lines>", self.fake.calls[0])
        earlier = self.fake.calls[1].split("<earlier_lines>")[1].split("</earlier_lines>")[0]
        self.assertIn("line 3", earlier)
        self.assertIn("line 4", earlier)
        self.assertNotIn("line 2", earlier)   # only the last 2 lines of the previous chunk

    def test_speaker_codes_become_names(self):
        self.model.notes([Line("PM", 0, "let's start")])
        self.assertIn("[00:00] Project Manager: let's start", self.fake.calls[0])

    def test_summarize_returns_text(self):
        self.assertEqual(self.model.summarize(lines_every(60, 3)), "summary after chunk 1")

    def test_system_prompt_has_length(self):
        self.assertIn("under 160 words", self.model.system)


class TestPacing(unittest.TestCase):
    def test_calls_spaced_by_min_seconds(self):
        now = [0.0]
        slept = []

        def sleep(s):
            slept.append(round(s, 2))
            now[0] += s

        fake = FakeProvider()
        cfg = dict(CFG, chunk_seconds=45, min_seconds_between_calls=45)
        model = LLMSummarizer(cfg, provider=fake, clock=lambda: now[0], sleep=sleep)
        original = fake.generate_json

        def slow_call(*a):   # each API call takes 2 s
            now[0] += 2
            return original(*a)
        fake.generate_json = slow_call
        model.notes(lines_every(15, 12))   # 0..165 s -> 4 chunks of 45 s
        self.assertEqual(len(fake.calls), 4)
        self.assertEqual(slept, [43, 43, 43])   # first call at once, then 45 s apart (call time counted)

    def test_no_wait_when_gap_is_zero(self):
        slept = []
        model = LLMSummarizer(dict(CFG, chunk_seconds=45, min_seconds_between_calls=0), provider=FakeProvider(),
                              sleep=slept.append)
        model.notes(lines_every(15, 12))
        self.assertEqual(slept, [])


class TestSchemaAndParsing(unittest.TestCase):
    def test_schema_is_strict(self):
        self.assertFalse(SCHEMA["additionalProperties"])
        item = SCHEMA["properties"]["action_items"]["items"]
        self.assertEqual(set(item["required"]), {"task", "owner", "due"})
        self.assertFalse(item["additionalProperties"])

    def test_notes_round_trip(self):
        d = {"summary": " s ", "action_items": [{"task": " t ", "owner": " Sam ", "due": ""}]}
        notes = Notes.from_dict(d)
        self.assertEqual(notes.to_dict(), {"summary": "s", "action_items": [{"task": "t", "owner": "Sam", "due": ""}]})

    def test_read_bot_transcript(self):
        text = "[00:05] Priya: hello there\n[125:03] Unknown speaker: Yes: agreed.\nnot a line\n"
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(text)
        try:
            lines = read_transcript(f.name)
        finally:
            os.remove(f.name)
        self.assertEqual(lines, [Line("Priya", 5, "hello there"), Line("Unknown speaker", 125 * 60 + 3, "Yes: agreed.")])


class TestProviderFactory(unittest.TestCase):
    def test_unknown_provider_is_a_clear_error(self):
        from llm_providers import make_provider
        with self.assertRaises(SystemExit):
            make_provider({"provider": "nope", "model": "x"})

    def test_anthropic_request_shape(self):
        """The Anthropic provider sends the schema as structured output and parses the JSON text block."""
        from types import SimpleNamespace
        from llm_providers import AnthropicProvider
        p = AnthropicProvider.__new__(AnthropicProvider)   # skip __init__: no client, no key
        import anthropic
        p.anthropic, p.model, p.effort, p.fallbacks = anthropic, "claude-sonnet-5-5", "medium", "default"
        p.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "served_by": []}
        sent = {}
        reply = SimpleNamespace(stop_reason="end_turn", model="claude-sonnet-5-5",
                                usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                                content=[SimpleNamespace(type="thinking"),
                                         SimpleNamespace(type="text", text=json.dumps({"summary": "x", "action_items": []}))])
        p.client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(
            create=lambda **kw: (sent.update(kw), reply)[1])))
        out = p.generate_json("sys", "user", SCHEMA, 1000)
        self.assertEqual(out, {"summary": "x", "action_items": []})
        self.assertEqual(sent["output_config"], {"format": {"type": "json_schema", "schema": SCHEMA}, "effort": "medium"})
        self.assertEqual(sent["fallbacks"], "default")
        self.assertEqual(sent["betas"], ["server-side-fallback-2026-07-01"])
        self.assertEqual(p.usage["calls"], 1)


if __name__ == "__main__":
    unittest.main()


class TestResumeFromCache(unittest.TestCase):
    """run.py saves every LLM call; a run stopped by an error resumes without asking again what it already has."""

    def test_stopped_run_resumes(self):
        import run

        class StopsAfterTwo(FakeProvider):
            model = "fake-1"

            def generate_json(self, *a):
                if len(self.calls) == 2:
                    raise RuntimeError("quota")
                return super().generate_json(*a)

        lines = lines_every(60, 15)   # 3 chunks of 300 s
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "llm_cache.jsonl")
            first = LLMSummarizer(dict(CFG, min_seconds_between_calls=0), provider=StopsAfterTwo())
            run.add_llm_cache(first, path)
            with self.assertRaises(RuntimeError):
                first.summarize(lines)
            fresh = FakeProvider()
            fresh.model = "fake-1"
            second = LLMSummarizer(dict(CFG, min_seconds_between_calls=0), provider=fresh)
            run.add_llm_cache(second, path)
            second.summarize(lines)
            self.assertEqual(len(fresh.calls), 1)               # only the third chunk was asked again
            self.assertEqual(fresh.usage["cache_hits"], 2)
