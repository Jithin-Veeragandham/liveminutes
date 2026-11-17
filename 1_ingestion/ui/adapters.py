"""Where the meeting page's summary, action items and answers come from: the only place teammates
need to change to plug in their real code. Shapes: docs/UI_CONTRACT.md.

  summary_source(meeting)          -> {"items": [summary item], "sample": bool, "label": str|None, "note": str}
  actions_source(meeting)          -> {"items": [action item],  "sample": bool, "label": str|None, "note": str}
  ask_source(question, meeting)    -> {"question", "answer", "source_meetings": [...], "sample": bool}

Today they return sample data (ui/fixtures/), labelled in the page. Demo meetings get the summary and
action items from their script (labelled Demo). `meeting` is a ui.meeting.Meeting: .sid, .lines(),
.rows(), .people(), .consented(), .live, .demo, .line_id_for(start_ms).

Edits are NOT the adapters' job: people's edits live in the edit log (ui/editlog.py) and are applied on
top of whatever these return. Keep item ids stable between calls (an item that changes keeps its id,
and its "version" goes up), so edits and source links stay attached to it.
"""

import json
from functools import lru_cache
from pathlib import Path

from ui import demo

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


def summary_source(meeting):
    """Summary items: {"id", "text", "source_line_ids", "version", "type": "point" | "decision"}."""
    if meeting.demo:
        d = demo.state(meeting.sid)
        return {"items": _resolve(meeting, d.get("keys", {}), d.get("summary", [])), "sample": True, "label": "Demo",
                "note": "Demo meeting: the summary is part of the script and updates as the meeting goes."}
    return {"items": [dict(i) for i in fixture("sample_summary.json")["items"]], "sample": True,
            "label": "Sample data", "note": SAMPLE_NOTE}


def actions_source(meeting):
    """Action items: {"id", "text", "owner", "due" (YYYY-MM-DD or ""), "status", "source_line_ids", "version"}."""
    if meeting.demo:
        d = demo.state(meeting.sid)
        return {"items": _resolve(meeting, d.get("keys", {}), d.get("actions", [])), "sample": True, "label": "Demo",
                "note": "Demo meeting: action items are part of the script."}
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


def ask_suggestions():
    return list(fixture("sample_answers.json").get("suggestions", []))
