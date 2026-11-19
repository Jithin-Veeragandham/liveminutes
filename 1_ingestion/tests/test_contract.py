"""Contract test for the adapters in 1_ingestion/ui/adapters.py (shapes: docs/UI_CONTRACT.md).

Run this after plugging in your summarizer, action-item extraction or knowledge base:
  .venv/bin/python -m pytest 1_ingestion/tests/test_contract.py -v

It calls YOUR summary_source / actions_source / ask_source on three meetings (a live one with a real-looking
transcript, an empty one, and a finished Demo meeting) and checks:
  - every field the page needs is there, with the right type and allowed values;
  - ids are unique, stable between calls, and don't collide with the ids people's items get;
  - source_line_ids point at lines that exist in that meeting; owners are people in the meeting;
  - summary_source and actions_source answer fast (they're called on every page update);
  - Demo mode still works (the demo branch at the top of each adapter is kept);
  - the knowledge-base hooks have the right signatures (they are NOT called: no test data gets indexed).
"""

import asyncio
import inspect
import json
import re
import statistics
import time
from pathlib import Path

import pytest

import settings
from tests.test_consent import FakeBridge
from ui import adapters, demo
from ui.meeting import Meeting

FAST_S = 0.05                          # summary_source / actions_source: median time per call
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ITEM_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,39}$")
PEOPLES_IDS = re.compile(r"^u[0-9a-f]{8}$")   # ids of items people add by hand (ui/editlog.py): LLM ids must differ
RESERVED = {"meeting", "minutes", "__new"}
SCRIPT = json.loads((Path(adapters.__file__).parent / "fixtures" / "demo_meeting.json").read_text())


class Bridge(FakeBridge):
    def __init__(self):
        super().__init__()
        self.live_url = "http://localhost:8776"


async def _build(tmp):
    settings.SESSIONS_DIR = tmp
    b = Bridge()
    people = SCRIPT["people"]
    meetings = {}
    for sid, with_lines in (("bot_contract_live", True), ("bot_contract_empty", False)):
        s = b.session(sid)
        for n, key in enumerate(("maya", "daniel", "priya"), 1):
            await b.on_join_leave(s, {"participant_uuid": f"u{n}", "participant_name": people[key], "event_type": "join"})
        await b.on_bot_state(s, {"new_state": "joined_recording"})
        for n, key in enumerate(("maya", "daniel", "priya"), 1):
            await b.on_chat(s, {"text": "yes", "sender_uuid": f"u{n}", "sender_name": people[key]})
        t0 = 1_790_000_000_000
        s.t0_ms = t0
        if with_lines:  # the demo meeting's words, as a real meeting would have them
            for ev in [e for e in SCRIPT["events"] if "say" in e][:24]:
                start = t0 + int(ev["t"] * 1000)
                s.lines.append((start, start + int(ev["dur"] * 1000), people[ev["say"]], ev["text"]))
        meetings[sid] = sid
    run = demo.start(b, speed=60)
    await asyncio.wait_for(run.task, 30)
    meetings[run.sid] = run.sid
    return b, meetings


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    old = settings.SESSIONS_DIR
    b, sids = asyncio.run(_build(tmp_path_factory.mktemp("contract")))
    yield b, sids
    settings.SESSIONS_DIR = old


def meetings_of(world):
    b, sids = world
    return [Meeting(b, sid) for sid in sids]


def check_envelope(out, where):
    assert isinstance(out, dict), f"{where}: must return a dict, got {type(out).__name__}"
    missing = {"items", "sample", "label", "note"} - set(out)
    assert not missing, f"{where}: missing keys {sorted(missing)}"
    assert isinstance(out["items"], list), f"{where}: items must be a list"
    assert isinstance(out["sample"], bool), f"{where}: sample must be true or false"
    assert out["label"] is None or isinstance(out["label"], str), f"{where}: label must be a string or null"
    assert isinstance(out["note"], str), f"{where}: note must be a string"


def check_item(item, where, line_ids, limit):
    assert isinstance(item, dict), f"{where}: each item must be a dict"
    for key in ("id", "text", "source_line_ids", "version"):
        assert key in item, f"{where}: item without {key!r}: {item}"
    iid = item["id"]
    assert isinstance(iid, str) and ITEM_ID.match(iid), f"{where}: bad id {iid!r} (1-40 chars: letters, digits, _ . : -)"
    assert iid not in RESERVED and not PEOPLES_IDS.match(iid), f"{where}: id {iid!r} is reserved for the UI's own items"
    assert isinstance(item["text"], str) and item["text"].strip(), f"{where}: {iid}: text must be a non-empty string"
    assert len(" ".join(item["text"].split())) <= limit, f"{where}: {iid}: text longer than {limit} characters"
    assert isinstance(item["version"], int) and not isinstance(item["version"], bool) and item["version"] >= 1, \
        f"{where}: {iid}: version must be an integer >= 1"
    src = item["source_line_ids"]
    assert isinstance(src, list) and all(isinstance(x, str) for x in src), f"{where}: {iid}: source_line_ids must be a list of strings"
    unknown = [x for x in src if x not in line_ids]
    assert not unknown, f"{where}: {iid}: source_line_ids {unknown} aren't lines of this meeting (use meeting.lines() ids)"


def check_summary(out, m):
    where = f"summary_source({m.sid})"
    check_envelope(out, where)
    line_ids = {l["id"] for l in m.lines()}
    for item in out["items"]:
        check_item(item, where, line_ids, 600)
        assert item.get("type", "point") in ("point", "decision"), f"{where}: {item['id']}: type must be point or decision"
        assert item.get("status", "draft") in ("draft", "approved"), f"{where}: {item['id']}: status must be draft or approved"
    ids = [i["id"] for i in out["items"]]
    assert len(ids) == len(set(ids)), f"{where}: duplicate ids {ids}"


def check_actions(out, m):
    where = f"actions_source({m.sid})"
    check_envelope(out, where)
    line_ids = {l["id"] for l in m.lines()}
    people = set(m.consented())
    for item in out["items"]:
        check_item(item, where, line_ids, 300)
        for key in ("owner", "due", "status"):
            assert key in item, f"{where}: {item['id']}: missing {key!r}"
        assert item["owner"] == "" or item["owner"] in people, \
            f"{where}: {item['id']}: owner {item['owner']!r} isn't one of {sorted(people)} (or \"\" for nobody)"
        assert item["due"] == "" or (isinstance(item["due"], str) and DATE.match(item["due"])), \
            f"{where}: {item['id']}: due must be YYYY-MM-DD or \"\""
        assert item["status"] in ("pending", "approved", "done"), f"{where}: {item['id']}: status must be pending, approved or done"
    ids = [i["id"] for i in out["items"]]
    assert len(ids) == len(set(ids)), f"{where}: duplicate ids {ids}"


def timed(fn, *args):
    times, out = [], None
    for _ in range(5):
        t = time.perf_counter()
        out = fn(*args)
        times.append(time.perf_counter() - t)
    return out, statistics.median(times)


@pytest.mark.parametrize("name", ["summary_source", "actions_source"])
def test_summary_and_actions_match_the_contract(world, name):
    for m in meetings_of(world):
        fn = getattr(adapters, name)
        assert not inspect.iscoroutinefunction(fn), f"{name} must be a plain (sync) function: run slow work in background()"
        out, median = timed(fn, m)
        (check_summary if name == "summary_source" else check_actions)(out, m)
        assert median < FAST_S, (f"{name}({m.sid}) took {median * 1000:.0f} ms per call; it's called on every page "
                                 f"update, so return a cached result (under {FAST_S * 1000:.0f} ms)")
        again = fn(m)
        assert [(i["id"], i["version"]) for i in again["items"]] == [(i["id"], i["version"]) for i in out["items"]], \
            f"{name}({m.sid}): ids or versions changed between two calls with nothing new: keep them stable"


def test_summary_and_action_ids_dont_collide(world):
    for m in meetings_of(world):
        s = {i["id"] for i in adapters.summary_source(m)["items"]}
        a = {i["id"] for i in adapters.actions_source(m)["items"]}
        assert not s & a, f"{m.sid}: the same id is used for a summary item and an action item: {sorted(s & a)}"


def test_demo_mode_still_works(world):
    b, sids = world
    m = Meeting(b, next(s for s in sids if s.startswith("demo_")))
    for name in ("summary_source", "actions_source"):
        out = getattr(adapters, name)(m)
        assert out["label"] == "Demo" and out["items"], (f"{name} no longer returns the demo script's items for demo "
                                                         "meetings: keep the `if meeting.demo:` branch at the top")


def test_ask_matches_the_contract(world):
    m = meetings_of(world)[0]
    for question in ("What did we decide about the launch date?", "zebra?"):
        out = adapters.ask_source(question, m)
        if inspect.isawaitable(out):
            out = asyncio.run(out)
        assert isinstance(out, dict) and out.get("question") == question, "ask_source: return the question as asked"
        assert isinstance(out.get("answer"), str) and out["answer"].strip(), "ask_source: answer must be non-empty text"
        assert isinstance(out.get("sample", False), bool), "ask_source: sample must be true or false"
        assert isinstance(out.get("source_meetings"), list), "ask_source: source_meetings must be a list"
        for src in out["source_meetings"]:
            for key in ("id", "title", "date"):
                assert isinstance(src.get(key), str) and src[key], f"ask_source: each source needs {key!r}"
            assert DATE.match(src["date"]), "ask_source: source date must be YYYY-MM-DD"
            assert "snippet" not in src or isinstance(src["snippet"], str), "ask_source: snippet must be text"


def test_ask_info_matches_the_contract():
    info = adapters.ask_info()
    assert isinstance(info, dict) and isinstance(info.get("sample"), bool), "ask_info: needs sample: true/false"
    assert isinstance(info.get("note", ""), str), "ask_info: note must be text"
    assert isinstance(info.get("suggestions", []), list) and all(isinstance(q, str) for q in info.get("suggestions", [])), \
        "ask_info: suggestions must be a list of questions"


def test_hooks_have_the_right_shape():
    for name, args in (("minutes_approved", ("bot_x", {})), ("minutes_unapproved", ("bot_x",))):
        fn = getattr(adapters, name, None)
        assert callable(fn), f"adapters.{name} must exist (it can do nothing)"
        try:
            inspect.signature(fn).bind(*args)
        except TypeError:
            pytest.fail(f"adapters.{name} must accept {len(args)} argument(s): {name}{('(meeting_id, record)' if len(args) == 2 else '(meeting_id)')}")
    bg = getattr(adapters, "background", None)
    if bg is not None:
        assert inspect.iscoroutinefunction(bg), "adapters.background must be `async def background(bridge)`"
        inspect.signature(bg).bind(object())
