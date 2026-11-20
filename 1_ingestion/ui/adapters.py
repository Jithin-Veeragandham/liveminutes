"""Where the meeting page's summary, action items and answers come from: the only place teammates
need to change to plug in their real code. Shapes: docs/UI_CONTRACT.md. How: docs/INTEGRATION.md.
Check your code against the contract:  .venv/bin/python -m pytest 1_ingestion/tests/test_contract.py

  summary_source(meeting)          -> {"items": [summary item], "sample": bool, "label": str|None, "note": str}
  actions_source(meeting)          -> {"items": [action item],  "sample": bool, "label": str|None, "note": str}
      SYNC and FAST (well under 50 ms): called every time a meeting page is built, which is on page load and
      on every live update for every viewer (several times a second in a busy meeting). Never call an LLM
      here: run it in background() and return your cached result.
  ask_source(question, meeting)    -> {"question", "answer", "source_meetings": [...], "sample": bool}
      Called once per question. Sync (runs in a thread) or async; may be slow (an LLM call is fine).
  ask_info()                       -> {"sample": bool, "note": str, "suggestions": [str]}: the Ask panel's label
  minutes_approved(meeting_id, record) / minutes_unapproved(meeting_id)
      Hooks for the knowledge base: index ONLY approved minutes. Called in the background (sync ones in a
      thread), after "Approve minutes", after undoing an approval, and when a meeting is deleted.
  background(bridge)               optional: async, started once with the bridge (e.g. a summarizer loop)

Summary and action items come from the LLM's live notes (Jithin's notes.py + 2_summarization/llm_summarizer.py,
turned into items by ui/live_notes.py) when the bridge has an LLM key. Without one (or for a finished meeting
that has no notes), they are sample data (ui/fixtures/), labelled "Sample data" in the page. Demo meetings get
them from their script (labelled Demo). Ask is still sample data until the knowledge base is plugged in. `meeting` is a ui.meeting.Meeting: .sid, .lines(),
.rows(), .people(), .consented(), .live, .demo, .line_id_for(start_ms).

Edits are NOT the adapters' job: people's edits live in the edit log (ui/editlog.py) and are applied on
top of whatever these return. Keep item ids stable between calls (an item that changes keeps its id,
and its "version" goes up), so edits and source links stay attached to it.
"""

import json
from functools import lru_cache
from pathlib import Path

from ui import demo, live_notes

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_NOTE = ("Sample data: the live summarizer isn't connected yet, so these items are examples, "
               "not from this meeting. You can still edit them to try the editing features.")
SAMPLE_ASK_NOTE = "Preview with sample data: answers come from invented past meetings until the knowledge base is connected."


@lru_cache(maxsize=None)
def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def _resolve(meeting, keys, items):
    """Demo items point at script line keys; turn those into this meeting's transcript line ids."""
    out = []
    for item in items:
        ids = []
        for key in item.get("source", []):
            line_id = meeting.line_id_for(keys[key]) if key in keys else None
            if line_id and line_id not in ids:
                ids.append(line_id)
        out.append({k: v for k, v in item.items() if k != "source"} | {"source_line_ids": ids})
    return out


BRIDGE = None   # the running bridge, set by background(); its .notes is the LLM job (notes.LiveNotes)


def notes_job():
    return getattr(BRIDGE, "notes", None)


def _llm_waiting(meeting):
    """The LLM is on and this meeting is live, but it hasn't written notes yet: show nothing, not samples."""
    job = notes_job()
    s = getattr(meeting, "s", None)
    return bool(job and job.enabled and s is not None and not s.declined)


def summary_source(meeting):
    """Summary items: {"id", "text", "source_line_ids", "version", "type": "point" | "decision"}."""
    if meeting.demo:
        d = demo.state(meeting.sid)
        return {"items": _resolve(meeting, d.get("keys", {}), d.get("summary", [])), "sample": True, "label": "Demo",
                "note": "Demo meeting: the summary is part of the script and updates as the meeting goes."}
    book = live_notes.current(meeting)
    if book is not None or _llm_waiting(meeting):
        return {"items": live_notes.Book.public(book.summary) if book else [], "sample": False, "label": None, "note": ""}
    return {"items": [dict(i) for i in fixture("sample_summary.json")["items"]], "sample": True,
            "label": "Sample data", "note": SAMPLE_NOTE}


def actions_source(meeting):
    """Action items: {"id", "text", "owner", "due" (YYYY-MM-DD or ""), "status", "source_line_ids", "version"}."""
    if meeting.demo:
        d = demo.state(meeting.sid)
        return {"items": _resolve(meeting, d.get("keys", {}), d.get("actions", [])), "sample": True, "label": "Demo",
                "note": "Demo meeting: action items are part of the script."}
    book = live_notes.current(meeting)
    if book is not None or _llm_waiting(meeting):
        return {"items": live_notes.Book.public(book.actions) if book else [], "sample": False, "label": None, "note": ""}
    return {"items": [dict(i) for i in fixture("sample_actions.json")["items"]], "sample": True,
            "label": "Sample data", "note": SAMPLE_NOTE}


def ask_source(question, meeting=None):
    """An answer about past meetings: {"question", "answer", "source_meetings": [{"id", "title", "date",
    "snippet"}], "sample": bool}. Today: the best keyword match among invented sample answers."""
    data = fixture("sample_answers.json")
    words = {w.strip("?.,!").lower() for w in question.split()}
    best, score = None, 0
    for a in data["answers"]:
        hits = sum(1 for m in a["match"] if m in words or any(w.startswith(m) for w in words))
        if hits > score:
            best, score = a, hits
    chosen = best or data["fallback"]
    return {"question": question, "answer": chosen["answer"], "source_meetings": chosen["source_meetings"],
            "sample": True, "note": SAMPLE_ASK_NOTE}


def ask_info():
    """What the Ask panel shows before any question: {"sample": bool, "note": str, "suggestions": [str]}.
    "sample": true shows the "Preview · sample data" label and the note. Set it to false with the real knowledge base."""
    return {"sample": True, "note": SAMPLE_ASK_NOTE, "suggestions": list(fixture("sample_answers.json").get("suggestions", []))}


def minutes_approved(meeting_id, record):
    """A meeting's minutes were approved (or approved again after changes): index `record`, replacing any
    earlier version of this meeting. `record` is described in docs/INTEGRATION.md ("Approved records only").
    Skip it if record["demo"] or record["sample"] is true. Today: nothing (no knowledge base yet)."""
    return None


def minutes_unapproved(meeting_id):
    """The approval was undone, or the meeting was deleted (someone said no): remove it from the index."""
    return None


async def background(bridge):
    """Started with the bridge. The LLM job itself (notes.LiveNotes.watch, every 30 s) is started by bridge.main();
    here we only keep a reference so the adapters can see whether it is on. Ashmit's start-up work can go here too."""
    global BRIDGE
    BRIDGE = bridge
