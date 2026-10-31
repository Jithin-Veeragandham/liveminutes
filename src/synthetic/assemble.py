"""Turn a parsed LLM draft into a canonical meeting record.

The model writes speakers, text, dialogue-act types and gold labels, and refers to
segments by number. This code assigns segment IDs and synthetic timestamps
deterministically, so timestamps are monotonic by construction and never depend on
the model's arithmetic.
"""
from __future__ import annotations

import hashlib
import json
import re

WORDS_PER_SEC = 2.6
START_OFFSET = 5.0


class DraftError(ValueError):
    pass


def parse_draft(text: str) -> dict:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j < i:
        raise DraftError("response contains no JSON object")
    try:
        return json.loads(t[i:j + 1])
    except json.JSONDecodeError as e:
        raise DraftError(f"response JSON does not parse: {e}") from e


def _gap(meeting_id: str, n: int) -> float:
    h = int(hashlib.sha256(f"{meeting_id}:{n}".encode()).hexdigest()[:8], 16)
    return 0.15 + (h % 1000) / 1000 * 0.85  # 0.15 .. 1.0 s


def _sid(meeting_id: str, n) -> str:
    return f"{meeting_id}.s{int(n):04d}" if isinstance(n, int) or str(n).isdigit() else f"{meeting_id}.s?{n}"


def assemble(draft: dict, mplan: dict, prior: list[dict], provenance: dict) -> tuple[dict, list[str]]:
    errors: list[str] = []
    mid = mplan["meeting_id"]
    if not isinstance(draft, dict):
        raise DraftError("top-level JSON is not an object")

    names = {}
    if prior:
        names = {p["label"]: p.get("name") for p in prior[0]["participants"]}
    elif isinstance(draft.get("names"), dict):
        names = {k: v for k, v in draft["names"].items() if isinstance(v, str)}
    participants = [{"label": p["label"], "role": p["role"], "name": names.get(p["label"]),
                     "global_id": None, "status": p["status"]} for p in mplan["participants"]]

    segments, t = [], START_OFFSET
    raw_segs = draft.get("segments") or []
    for i, s in enumerate(raw_segs, start=1):
        if s.get("n") != i:
            errors.append(f"segment numbering broken at position {i} (n={s.get('n')!r}); n must be 1..N in order")
            break
        text = str(s.get("text", "")).strip()
        dur = max(0.4, round(len(text.split()) / WORDS_PER_SEC, 2))
        start = round(t, 2)
        end = round(t + dur, 2)
        segments.append({"id": _sid(mid, i), "speaker": s.get("speaker"), "start": start, "end": end,
                         "text": text, "da_type": s.get("da_type")})
        t = end + _gap(mid, i)

    def refs(ns):
        return [_sid(mid, n) for n in (ns or [])]

    ai_ids = {}
    action_items = []
    for k, a in enumerate(draft.get("action_items") or [], start=1):
        aid = f"{mid}.ai{k}"
        ai_ids[a.get("n", k)] = aid
        action_items.append({
            "id": aid,
            "description": a.get("description", ""),
            "owner": a.get("owner"),
            "owner_unclear": bool(a.get("owner_unclear", False)),
            "status": a.get("status"),
            "previous_owners": a.get("previous_owners") or [],
            "history": [{"segment": _sid(mid, h.get("segment")), "event": h.get("event"),
                         "by": h.get("by"), "owner": h.get("owner")} for h in (a.get("history") or [])],
            "evidence_segments": refs(a.get("evidence_segments")),
            "owner_source": "gold",
        })

    qa_facts = []
    for k, f in enumerate(draft.get("qa_facts") or [], start=1):
        rel = f.get("related_action_item")
        if rel is not None and rel not in ai_ids:
            errors.append(f"qa_fact {k} related_action_item {rel!r} is not an action item n")
        qa_facts.append({
            "id": f"{mid}.f{k}",
            "type": f.get("type"),
            "fact": f.get("fact", ""),
            # agreed is only meaningful for decisions and proposals
            "agreed": f.get("agreed") if f.get("type") in ("decision", "unagreed_proposal") else None,
            "related_action_item": ai_ids.get(rel) if rel is not None else None,
            "refers_to_meeting": f.get("refers_to_meeting"),
            "evidence_segments": refs(f.get("evidence_segments")),
        })

    edge_cases = [{"scenario_id": e.get("scenario_id"), "segments": refs(e.get("segments")),
                   "note": e.get("note", "")} for e in (draft.get("edge_cases") or [])]

    summ = draft.get("summary") or {}
    meeting = {
        "schema_version": "1.0",
        "source": "synthetic",
        "meeting_id": mid,
        "series_id": mplan["series_id"],
        "meeting_letter": mplan["meeting_letter"],
        "scenario_type": "scenario",
        "stage": mplan["stage"],
        "duration_sec": round(segments[-1]["end"] + 3.0, 2) if segments else 0.0,
        "participants": participants,
        "segments": segments,
        "summary": {k: list(summ.get(k) or []) for k in ("abstract", "actions", "decisions", "problems")},
        "action_items": action_items,
        "qa_facts": qa_facts,
        "edge_cases": edge_cases,
        "provenance": {**provenance, "feature": mplan["feature"], "product": mplan["product"],
                       "planned_scenarios": mplan["scenario_ids"]},
    }
    return meeting, errors
