"""A task said across two LLM updates (the bug from the team meeting): the request at the end of one update's lines,
the answer ("Sure, by Friday") in the next one, or a line that was transcribed late. A fake LLM, no network.

Run from the repo root:  .venv/bin/python -m pytest 1_ingestion/tests/test_notes_split.py
"""

import asyncio
import re

import notes
from session import now_ms
from tests.test_consent import FakeBridge

REQUEST = "Priya, could you send the budget sheet to the client?"
ANSWER = "Sure, I'll do it by Friday."


class CarefulLLM:
    """Adds the budget task only when it can see both the request and the answer in one call (in the new lines or
    in <earlier_lines>), like a model that won't record a request nobody has taken on yet."""
    name = "careful"

    def __init__(self):
        self.prompts, self.systems = [], []
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "served_by": []}

    def generate_json(self, system, user, schema, max_tokens):
        self.prompts.append(user)
        self.systems.append(system)
        items = re.findall(r'"task": "([^"]+)"', user)   # keep what the minutes so far have
        if "send the budget sheet" in user and "Sure, I'll do it by Friday" in user and not items:
            items = ["Send the budget sheet to the client"]
        return {"summary": "Budget talk.", "action_items": [{"task": t, "owner": "Priya", "due": "Friday"} for t in items]}


def setup():
    ln = notes.LiveNotes(provider=CarefulLLM())
    b = FakeBridge()
    s = b.session("bot_split")
    s.t0_ms = now_ms() - 300_000
    return ln, s


async def due(ln, s):
    ln.state(s).next_due_ms = now_ms() - 1
    await ln.maybe_update(s)


def test_a_task_asked_at_the_end_of_one_update_and_accepted_in_the_next_is_kept():
    ln, s = setup()
    t0 = s.t0_ms
    s.lines = [(t0 + 5_000, t0 + 8_000, "Sam", "Let's look at the budget."),
               (t0 + 25_000, t0 + 28_000, "Sam", REQUEST)]                     # the last line of update 1
    asyncio.run(due(ln, s))
    assert ln.state(s).notes.action_items == []                               # nobody took it on yet
    s.lines.append((t0 + 35_000, t0 + 37_000, "Priya", ANSWER))               # the answer, in update 2
    asyncio.run(due(ln, s))
    second = ln.provider.prompts[-1]
    assert "<earlier_lines>" in second and REQUEST in second.split("<transcript")[0]   # the request comes back as context
    assert "A task can be split across the two parts" in ln.provider.systems[-1]       # and may now be added
    assert [a.task for a in ln.state(s).notes.action_items] == ["Send the budget sheet to the client"]


def test_a_line_transcribed_late_is_sent_with_the_next_update():
    ln, s = setup()
    t0 = s.t0_ms
    s.lines = [(t0 + 5_000, t0 + 8_000, "Sam", REQUEST), (t0 + 40_000, t0 + 42_000, "Sam", "Next: the venue.")]
    asyncio.run(due(ln, s))
    assert ANSWER not in ln.provider.prompts[-1]
    # Priya's answer was said at 0:20 but Groq was backed up: it lands after update 1 (whose lines went up to 0:40).
    s.lines.append((t0 + 20_000, t0 + 22_000, "Priya", ANSWER))
    asyncio.run(due(ln, s))
    assert ANSWER in ln.provider.prompts[-1].split("<transcript")[1]          # sent now, not skipped for good
    assert [a.task for a in ln.state(s).notes.action_items] == ["Send the budget sheet to the client"]
    asyncio.run(due(ln, s))                                                  # and only once
    assert len(ln.provider.prompts) == 2 and ln.state(s).history[-1]["status"] == "skipped"
