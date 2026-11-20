"""The LLM's live notes (notes.py) on the meeting page: contract items with stable ids, edits and approvals that
later LLM runs can't change, the status line, failures, Demo mode untouched. A scripted fake LLM, no network.

Run from the repo root:  .venv/bin/python -m pytest 1_ingestion/tests/test_llm_ui.py
"""

import asyncio
import json

import pytest
from aiohttp import web

import live_view
import notes
import settings
import ui
from session import now_ms
from tests.test_ui import Bridge, clean, consented_meeting, edit, join  # noqa: F401  (clean: autouse fixture)
from ui import adapters, demo, live_notes

SID = "bot_ui_test"
T0 = 1_700_000_000_000   # consented_meeting's first audio: 2023-11-14 (a Tuesday), lines long settled


class ScriptedLLM:
    """Returns the next scripted notes on every call (the last one again when the script runs out)."""
    name = "fake"

    def __init__(self, script):
        self.script, self.calls, self.prompts = list(script), 0, []
        self.fail = None   # an exception to raise instead
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "served_by": ["fake-1"]}

    def generate_json(self, system, user, schema, max_tokens):
        if self.fail:
            raise self.fail
        self.prompts.append(user)
        self.usage["calls"] += 1
        self.usage["input_tokens"] += 500
        self.usage["output_tokens"] += 80
        out = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        return json.loads(json.dumps(out))


FIRST = {"summary": "Ann Lee opened the meeting and greeted Bob Stone. The team agreed to ship the beta on Friday. "
                    "Bob Stone raised the tight budget.",
         "action_items": [{"task": "Send the beta notes to the testers", "owner": "Bob", "due": "tomorrow"},
                          {"task": "Book the review room", "owner": "Ann Lee", "due": "next meeting"},
                          {"task": "Ask the client about the logo", "owner": "the client", "due": ""}]}
SECOND = {"summary": "Ann Lee opened the meeting and greeted Bob Stone. The team agreed to ship the beta on Friday "
                     "after the last fixes. Bob Stone raised the tight budget, now 2,000 euros. A new point about the "
                     "launch email came up.",
          "action_items": [{"task": "Send the beta notes to all the testers", "owner": "Bob Stone", "due": "tomorrow"},
                           {"task": "Book the review room", "owner": "Ann", "due": "Thursday"},
                           {"task": "Ask the client about the logo", "owner": "the client", "due": ""},
                           {"task": "Draft the launch email", "owner": "Ann Lee", "due": ""}]}


@pytest.fixture
async def llm_env(aiohttp_client):
    live_notes._books.clear()
    b = Bridge()
    b.notes = notes.LiveNotes(provider=ScriptedLLM([FIRST, SECOND, SECOND]))
    assert b.notes.enabled, b.notes.reason
    app = web.Application()
    live_view.add_routes(app, b)
    ui.setup(app, b)
    client = await aiohttp_client(app)   # starts adapters.background(bridge): the adapters see b.notes
    await asyncio.sleep(0)
    s, code = await consented_meeting(b)
    s.lines += [(T0 + 20_000, T0 + 24_000, "Ann Lee", "Bob, can you send the beta notes to the testers tomorrow?"),
                (T0 + 25_000, T0 + 27_000, "Bob Stone", "Sure, I'll send the beta notes."),
                (T0 + 40_000, T0 + 44_000, "Ann Lee", "We agreed to ship the beta on Friday, right?")]
    yield b, client, s, code
    s.close()
    adapters.BRIDGE = None


async def update(b, s, more=()):
    """One LLM update now (as notes.watch does every 30 s), with `more` lines said since the last one."""
    s.lines += list(more)
    b.notes.state(s).next_due_ms = now_ms() - 1
    await b.notes.maybe_update(s)


async def snap(client, sid=SID):
    return await (await client.get(f"/live/{sid}/api/snapshot")).json()


def by_text(items):
    return {i["text"]: i for i in items}


async def test_llm_notes_become_contract_items_without_the_sample_label(llm_env):
    b, client, s, _ = llm_env
    before = await snap(client)
    assert before["summary"] == {"items": [], "sample": False, "label": None, "note": ""}   # on, nothing yet: no samples
    await update(b, s)
    d = await snap(client)
    assert d["summary"]["sample"] is False and d["summary"]["label"] is None
    assert d["actions"]["sample"] is False and d["actions"]["label"] is None
    assert d["ask"]["sample"] is True                                   # Ask stays sample data (no knowledge base yet)
    points = d["summary"]["items"]
    assert [p["id"] for p in points] == ["llm-s1", "llm-s2", "llm-s3"]
    assert [p["type"] for p in points] == ["point", "decision", "point"]   # "agreed" -> a decision
    line_ids = {l["id"] for l in d["lines"]}
    assert all(set(p["source_line_ids"]) <= line_ids for p in points)
    assert points[1]["source_line_ids"] == [f"t{T0 + 40_000}"]          # "agreed to ship the beta on Friday"
    acts = by_text(d["actions"]["items"])
    send = acts["Send the beta notes to the testers"]
    assert send["owner"] == "Bob Stone" and send["due"] == "2023-11-15" and send["due_text"] == "tomorrow"
    assert send["id"] == "llm-a1" and send["status"] == "pending" and send["version"] == 1
    assert set(send["source_line_ids"]) <= line_ids and send["source_line_ids"]   # the chunk it came from
    room = acts["Book the review room"]
    assert room["owner"] == "Ann Lee" and room["due"] == "" and room["due_text"] == "next meeting"
    assert by_text(d["actions"]["items"])["Ask the client about the logo (owner said: the client)"]["owner"] == ""


async def test_ids_stay_the_same_when_the_llm_rewrites_and_new_items_get_new_ids(llm_env):
    b, client, s, _ = llm_env
    await update(b, s)
    first = await snap(client)
    await update(b, s, [(T0 + 60_000, T0 + 63_000, "Ann Lee", "I'll draft the launch email.")])
    second = await snap(client)
    old = {i["id"]: i for i in first["summary"]["items"]}
    new = {i["id"]: i for i in second["summary"]["items"]}
    assert set(old) <= set(new) and len(new) == 4                         # 3 kept (2 reworded), 1 new
    assert new["llm-s1"]["version"] == 1 and new["llm-s2"]["version"] == 2 and new["llm-s4"]["version"] == 1
    acts = {i["id"]: i for i in second["actions"]["items"]}
    assert acts["llm-a1"]["text"] == "Send the beta notes to all the testers" and acts["llm-a1"]["version"] == 2
    assert acts["llm-a2"]["due"] == "2023-11-16" and acts["llm-a2"]["version"] == 2   # "Thursday" now
    assert acts["llm-a4"]["text"] == "Draft the launch email" and acts["llm-a4"]["owner"] == "Ann Lee"
    assert acts["llm-a4"]["source_line_ids"] == [f"t{T0 + 60_000}"]       # said in this update's lines


async def test_edited_approved_and_deleted_items_survive_later_llm_runs(llm_env):
    b, client, s, code = llm_env
    await update(b, s)
    csrf = await join(client, code, "Ann Lee")
    assert (await edit(client, csrf, "llm-s2", 1, text="We ship the beta on Friday.")).status == 200
    assert (await edit(client, csrf, "llm-a1", 1, status="approved")).status == 200
    r = await client.post(f"/m/{SID}/api/delete", json={"item": "llm-a2", "base_version": 1},
                          headers={"X-CSRF-Token": csrf})
    assert r.status == 200
    await update(b, s, [(T0 + 60_000, T0 + 63_000, "Ann Lee", "I'll draft the launch email.")])
    d = await snap(client)
    points = {i["id"]: i for i in d["summary"]["items"]}
    acts = {i["id"]: i for i in d["actions"]["items"]}
    assert points["llm-s2"]["text"] == "We ship the beta on Friday."     # edited: the LLM's rewrite is ignored
    assert points["llm-s3"]["text"].endswith("now 2,000 euros.")         # untouched: follows the LLM
    assert acts["llm-a1"]["text"] == "Send the beta notes to the testers" and acts["llm-a1"]["status"] == "approved"
    assert "llm-a2" not in acts                                         # deleted stays deleted
    assert "llm-a4" in acts                                             # new items still come in


async def test_approved_minutes_stay_frozen_until_the_approval_is_undone(llm_env):
    b, client, s, code = llm_env
    await update(b, s)
    csrf = await join(client, code, "Ann Lee")
    r = await client.post(f"/m/{SID}/api/approve", json={"base_version": 1}, headers={"X-CSRF-Token": csrf})
    change = (await r.json())["change"]
    approved = {i["id"]: i["text"] for i in (await snap(client))["summary"]["items"]}
    await update(b, s, [(T0 + 60_000, T0 + 63_000, "Ann Lee", "I'll draft the launch email.")])
    d = await snap(client)
    now = {i["id"]: i["text"] for i in d["summary"]["items"]}
    assert all(now[k] == v for k, v in approved.items())                # as approved, not the LLM's rewrite
    assert d["session"]["minutes"]["changed_since"]                     # the LLM's new point is flagged
    await client.post(f"/m/{SID}/api/undo", json={"item": "minutes", "change": change}, headers={"X-CSRF-Token": csrf})
    after = {i["id"]: i["text"] for i in (await snap(client))["summary"]["items"]}
    assert after["llm-s3"].endswith("now 2,000 euros.")                 # not approved any more: live again


async def test_status_line_with_countdown_and_latency_for_the_organizer_only(llm_env):
    b, client, s, code = llm_env
    await update(b, s)
    org = (await snap(client))["session"]["llm"]
    assert org["enabled"] and org["live"] and org["updates"] == 1 and org["interval_s"] == 30
    assert org["next_due_ms"] > now_ms() and org["updated_ms"] and not org["busy"] and org["error"] == ""
    assert org["model"] == "claude-sonnet-5-5" or org["model"]          # the configured model name
    assert org["history"][-1]["status"] == "ok" and org["history"][-1]["seconds"] >= 0
    assert org["history"][-1]["input_tokens"] == 500 and "sent" in org["history"][-1]
    assert org["rate"]["minute"] == 1
    await join(client, code, "Bob Stone")
    part = (await (await client.get(f"/m/{SID}/api/snapshot")).json())["session"]["llm"]
    assert part["updates"] == 1 and "history" not in part and "rate" not in part


async def test_llm_errors_keep_the_page_and_the_last_notes(llm_env):
    b, client, s, _ = llm_env
    await update(b, s)
    before = (await snap(client))["summary"]["items"]
    b.notes.provider.fail = RuntimeError("Gemini HTTP 429: quota exceeded")
    await update(b, s, [(T0 + 60_000, T0 + 63_000, "Ann Lee", "I'll draft the launch email.")])
    d = await snap(client)
    assert d["summary"]["items"] == before                               # the last good notes stay
    st = d["session"]["llm"]
    assert "429" in st["error"] and st["next_due_ms"] > now_ms()         # the page says so; tried again next time
    b.notes.provider.fail = None
    await update(b, s)                                                   # the same lines are sent again
    d = await snap(client)
    assert d["session"]["llm"]["error"] == "" and len(d["summary"]["items"]) == 4
    assert "I'll draft the launch email." in b.notes.provider.prompts[-1]


async def test_a_missing_key_turns_the_notes_off_and_the_page_falls_back(llm_env):
    b, client, s, _ = llm_env
    b.notes.provider.fail = SystemExit("No Gemini API key found: add GEMINI_API_KEY=... to the repo's .env")
    await update(b, s)                                                   # must not stop the notes job's loop
    assert not b.notes.enabled and "GEMINI_API_KEY" in b.notes.reason
    d = await snap(client)
    assert d["summary"]["sample"] is True and d["summary"]["label"] == "Sample data"
    assert d["session"]["llm"] == {"enabled": False, "reason": b.notes.reason}


async def test_demo_meetings_never_call_the_llm_and_keep_their_labels(llm_env):
    b, client, _, _ = llm_env
    run = demo.start(b, speed=60)
    await asyncio.wait_for(run.task, 30)
    s = b.sessions[run.sid]
    b.notes.state(s).next_due_ms = now_ms() - 1
    await b.notes.maybe_update(s, final=True)
    assert b.notes.provider.calls == 0
    d = await snap(client, run.sid)
    assert d["summary"]["label"] == "Demo" and d["actions"]["label"] == "Demo" and d["session"]["llm"] is None


async def test_ids_survive_a_bridge_restart(llm_env):
    b, client, s, _ = llm_env
    await update(b, s)
    ids = [i["id"] for i in (await snap(client))["actions"]["items"]]
    live_notes._books.clear()                       # a restarted bridge: no memory, only the session folder
    from ui.meeting import Meeting
    m = Meeting(None, SID)
    book = live_notes.current(m)
    assert [i["id"] for i in book.actions] == ids and book.next_a == 4


def test_owner_due_and_sentence_helpers():
    from datetime import date
    names = ["Veeragandham, Jithin", "Gupta, Ashmit", "Aatmaj Amol Salunke"]
    assert live_notes.match_owner("Jithin Veeragandham", names) == "Veeragandham, Jithin"
    assert live_notes.match_owner("Ashmit", names) == "Gupta, Ashmit"
    assert live_notes.match_owner("Aatmaj Salunke", names) == "Aatmaj Amol Salunke"
    assert live_notes.match_owner("the team", names) is None and live_notes.match_owner("", names) is None
    wed = date(2026, 11, 18)
    assert [live_notes.resolve_due(x, wed) for x in ("tonight", "by Friday", "tomorrow", "November 21", "2026-12-01",
                                                      "end of the week", "next meeting", "")] == \
        ["2026-11-18", "2026-11-20", "2026-11-19", "2026-11-21", "2026-12-01", "2026-11-20", "", ""]
    assert live_notes.sentences("We met. We agreed on 12.50 euros, e.g. for the case. Fine.") == \
        ["We met.", "We agreed on 12.50 euros, e.g. for the case. Fine."]
