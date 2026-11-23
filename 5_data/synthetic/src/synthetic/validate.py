"""Schema + semantic validation for canonical meetings (AMI and synthetic).

validate_meeting() returns a list of human-readable error strings; empty means valid.
The same function runs during generation (to trigger retries), in `build`, and in
the standalone `validate` command.
"""
from __future__ import annotations

from functools import lru_cache

import jsonschema

from .util import SCHEMA_PATH, read_json

DA_TYPES = {"bck", "stl", "fra", "inf", "sug", "ass", "el.inf", "el.sug", "el.ass", "el.und",
            "off", "und", "be.pos", "be.neg", "oth", None}


@lru_cache(maxsize=1)
def _validator():
    return jsonschema.Draft202012Validator(read_json(SCHEMA_PATH))


def schema_errors(m: dict) -> list[str]:
    out = []
    for e in sorted(_validator().iter_errors(m), key=lambda e: list(e.path)):
        loc = "/".join(str(p) for p in e.path) or "<root>"
        out.append(f"schema: {loc}: {e.message[:200]}")
    return out


# ---------------------------------------------------------------- scenario checks
def _tagged(m, sid):
    return [s for e in m["edge_cases"] if e["scenario_id"] == sid for s in e["segments"]]


def _check(name, arg, m, sid, seg_index, labels, absent):
    tagged = _tagged(m, sid)
    idx = sorted(seg_index[s] for s in tagged if s in seg_index)
    if name == "min_tagged_segments":
        return [] if len(set(tagged)) >= arg else [f"{sid}: needs >= {arg} tagged segments, has {len(set(tagged))}"]
    if name == "contiguous_tagged":
        return [] if idx and idx[-1] - idx[0] + 1 == len(set(idx)) else [f"{sid}: tagged segments must be one contiguous run"]
    if name == "non_contiguous_tagged":
        return [] if idx and idx[-1] - idx[0] + 1 > len(set(idx)) else [f"{sid}: tagged segments must alternate (not one contiguous run)"]
    if name == "same_speaker_tagged":
        sp = {m["segments"][i]["speaker"] for i in idx}
        return [] if len(sp) == 1 else [f"{sid}: self-correction segments must share one speaker, got {sorted(sp)}"]
    if name == "absent_participant":
        return [] if absent else [f"{sid}: meeting has no absent participant"]
    if name == "absent_owner":
        ok = any(a["owner"] in absent for a in m["action_items"])
        return [] if ok else [f"{sid}: no action item is owned by an absent participant"]
    if name == "offered_taken_by_other":
        for a in m["action_items"]:
            offers = {h["by"] for h in a["history"] if h["event"] == "offered"}
            if a["owner"] and offers and any(o != a["owner"] for o in offers):
                return []
        return [f"{sid}: no action item offered by one person and owned by another"]
    if name == "status_in":
        return [] if any(a["status"] in arg for a in m["action_items"]) else [f"{sid}: no action item with status in {arg}"]
    if name == "vague_owner":
        ok = any(a["owner"] is None and a.get("owner_unclear") for a in m["action_items"])
        return [] if ok else [f"{sid}: no action item with owner null and owner_unclear true"]
    if name == "qa_fact_type":
        return [] if any(f["type"] == arg for f in m["qa_facts"]) else [f"{sid}: no qa_fact of type {arg}"]
    if name == "unagreed_not_in_decisions":
        errs = []
        for f in m["qa_facts"]:
            if f["type"] == "unagreed_proposal" and f.get("agreed") is not False:
                errs.append(f"{sid}: unagreed_proposal {f['id']} must have agreed=false")
        return errs
    return [f"{sid}: unknown check {name!r}"]


def scenario_errors(m, scenarios_by_id, seg_index, labels, absent) -> list[str]:
    errs = []
    for sid in m.get("provenance", {}).get("planned_scenarios", []):
        sc = scenarios_by_id[sid]
        if not _tagged(m, sid):
            errs.append(f"{sid}: planned edge case is not tagged in edge_cases")
            continue
        for chk in sc.get("checks", []):
            name, arg = (next(iter(chk.items())) if isinstance(chk, dict) else (chk, None))
            errs += _check(name, arg, m, sid, seg_index, labels, absent)
    return errs


# ---------------------------------------------------------------- main entry
def validate_meeting(m: dict, scenarios_by_id: dict | None = None, mplan: dict | None = None,
                     prior_ids: list[str] | None = None) -> list[str]:
    errs = schema_errors(m)
    if errs:
        return errs  # semantic checks assume the shape is right
    synthetic = m["source"] == "synthetic"
    labels = {p["label"] for p in m["participants"]}
    absent = {p["label"] for p in m["participants"] if p["status"] == "absent"}
    seg_index = {}
    prev_start = -1.0
    for i, s in enumerate(m["segments"]):
        if s["id"] in seg_index:
            errs.append(f"duplicate segment id {s['id']}")
        seg_index[s["id"]] = i
        if s["speaker"] not in labels:
            errs.append(f"{s['id']}: speaker {s['speaker']!r} is not a participant")
        if s["speaker"] in absent:
            errs.append(f"{s['id']}: absent participant {s['speaker']} speaks")
        if s["end"] < s["start"]:
            errs.append(f"{s['id']}: end before start")
        if s["start"] < prev_start:
            errs.append(f"{s['id']}: timestamps not monotonic")
        prev_start = s["start"]
        if synthetic and s.get("da_type") not in DA_TYPES:
            errs.append(f"{s['id']}: unknown da_type {s.get('da_type')!r}")

    def check_refs(where, ids):
        for x in ids:
            if x not in seg_index:
                errs.append(f"{where}: references missing segment {x}")

    if synthetic and not m["summary"]["abstract"]:
        errs.append("summary.abstract is empty")
    for a in m["action_items"]:
        w = f"action_item {a['id']}"
        check_refs(w, a.get("evidence_segments", []))
        check_refs(w, [h["segment"] for h in a.get("history", [])])
        if a["owner"] is not None and a["owner"] not in labels:
            errs.append(f"{w}: owner {a['owner']!r} is not a listed participant")
        if synthetic:
            if a["owner"] is None and not a.get("owner_unclear"):
                errs.append(f"{w}: owner is null but owner_unclear is not true")
            if a["status"] == "unknown":
                errs.append(f"{w}: status 'unknown' is only allowed for AMI data")
            if a["status"] == "reassigned" and not a.get("previous_owners"):
                errs.append(f"{w}: reassigned but previous_owners is empty")
            if not a.get("evidence_segments"):
                errs.append(f"{w}: no evidence_segments")
        for po in a.get("previous_owners", []):
            if po not in labels:
                errs.append(f"{w}: previous owner {po!r} is not a listed participant")
        for h in a.get("history", []):
            for k in ("by", "owner"):
                if h.get(k) is not None and h[k] not in labels:
                    errs.append(f"{w}: history {k} {h[k]!r} is not a listed participant")
    for f in m["qa_facts"]:
        check_refs(f"qa_fact {f['id']}", f.get("evidence_segments", []))
        if synthetic and not f.get("evidence_segments"):
            errs.append(f"qa_fact {f['id']}: no evidence_segments")
        r = f.get("refers_to_meeting")
        if r is not None and prior_ids is not None and r not in prior_ids:
            errs.append(f"qa_fact {f['id']}: refers_to_meeting {r!r} is not an earlier meeting of this series")
    for e in m["edge_cases"]:
        check_refs(f"edge_case {e['scenario_id']}", e["segments"])

    if synthetic and scenarios_by_id is not None:
        planned = set(m.get("provenance", {}).get("planned_scenarios", []))
        for e in m["edge_cases"]:
            if e["scenario_id"] not in planned:
                errs.append(f"edge_case {e['scenario_id']!r} was not planned for this meeting")
        errs += scenario_errors(m, scenarios_by_id, seg_index, labels, absent)
    if synthetic and mplan is not None:
        lo, hi = mplan["target_segments"]
        n = len(m["segments"])
        if n < int(lo * 0.8) or n > int(hi * 1.25):
            errs.append(f"{n} segments, outside tolerance of target {lo}-{hi}")
        if mplan["meeting_letter"] == "a":
            if any(not p.get("name") for p in m["participants"]):
                errs.append("first meeting must give every participant a name")
        elif not any(f["type"] == "reference_to_prior_meeting" and f.get("refers_to_meeting")
                     for f in m["qa_facts"]):
            errs.append("later meeting has no reference_to_prior_meeting fact (series continuity)")
    return errs
