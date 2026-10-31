"""Convert AMI NXT manual annotations (ami_public_manual_1.6.2) to the canonical schema.

Sources used, per meeting:
  corpusResources/meetings.xml   type="scenario" meetings, nxt_agent letter, role, global_name, duration
  words/<m>.<X>.words.xml        word tokens with times (w elements; vocalsound/disfmarker/gap skipped)
  dialogueActs/<m>.<X>.dialog-act.xml   dialogue acts -> segments (fallback: segments/<m>.<X>.segments.xml)
  ontologies/da-types.xml        dialogue-act type names
  abstractive/<m>.abssumm.xml    abstract / actions / decisions / problems
  extractive/<m>.summlink.xml    links from abstractive sentences to dialogue acts (used as evidence)

AMI has no owner field on actions. The owner is resolved from the role mentioned in
the action sentence ("The industrial designer will ...") when exactly one role is
named, and is null otherwise. Status is "active": AMI's actions section lists the
tasks standing at the end of the meeting.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .plan import STAGES

NITE = "{http://nite.sourceforge.net/}"
HREF = re.compile(r"#id\(([^)]+)\)(?:\.\.id\(([^)]+)\))?")
ROLE_PATTERNS = {
    "PM": re.compile(r"\bproject manager\b", re.I),
    "ME": re.compile(r"\bmarketing\b", re.I),
    "UI": re.compile(r"\b(user interface|interface (designer|specialist|expert)|UI)\b", re.I),
    "ID": re.compile(r"\bindustrial designer\b", re.I),
}


def _nid(el):
    return el.get(NITE + "id")


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def load_meetings_index(ami: Path) -> dict:
    root = ET.parse(ami / "corpusResources" / "meetings.xml").getroot()
    out = {}
    for m in root.iter("meeting"):
        if m.get("type") != "scenario":
            continue
        out[m.get("observation")] = {
            "duration": _f(m.get("duration")),
            "speakers": sorted(({"label": s.get("nxt_agent"), "role": s.get("role"),
                                 "global_id": s.get("global_name")} for s in m.iter("speaker")),
                               key=lambda s: s["label"]),
        }
    return out


def load_da_types(ami: Path) -> dict:
    root = ET.parse(ami / "ontologies" / "da-types.xml").getroot()
    return {_nid(e): e.get("name") for e in root.iter("da-type")}


class Words:
    def __init__(self, path: Path):
        self.items = []  # (id, start, end, text|None, punc)
        for el in ET.parse(path).getroot():
            tag = el.tag
            text = el.text if tag == "w" else None
            self.items.append((_nid(el), _f(el.get("starttime")), _f(el.get("endtime")),
                               text, el.get("punc") == "true"))
        self.pos = {it[0]: i for i, it in enumerate(self.items)}

    def span(self, href: str):
        mt = HREF.search(href)
        a = self.pos[mt.group(1)]
        b = self.pos[mt.group(2)] if mt.group(2) else a
        return a, b

    def text(self, a, b):
        out = ""
        for _, _, _, t, punc in self.items[a:b + 1]:
            if t is None:
                continue
            out += t if (punc or not out) else " " + t
        return out.strip()

    def times(self, a, b):
        starts = [it[1] for it in self.items[a:b + 1] if it[1] is not None]
        ends = [it[2] for it in self.items[a:b + 1] if it[2] is not None]
        start = starts[0] if starts else self._nearest(a, -1)
        end = ends[-1] if ends else start
        return start, end

    def _nearest(self, i, step):
        while 0 <= i < len(self.items):
            if self.items[i][1] is not None:
                return self.items[i][1]
            i += step
        return None


def _segments(ami: Path, mid: str, labels: list[str], da_types: dict):
    segs, source, skipped = [], None, 0
    for lab in labels:
        wpath = ami / "words" / f"{mid}.{lab}.words.xml"
        if not wpath.exists():
            continue
        words = Words(wpath)
        dpath = ami / "dialogueActs" / f"{mid}.{lab}.dialog-act.xml"
        if dpath.exists():
            source = "dialogueActs"
            units = []
            for d in ET.parse(dpath).getroot().iter("dact"):
                child = d.find(NITE + "child")
                ptr = next((p for p in d.findall(NITE + "pointer") if p.get("role") == "da-aspect"), None)
                da = da_types.get(HREF.search(ptr.get("href")).group(1)) if ptr is not None else None
                units.append((_nid(d), child.get("href"), da))
        else:
            source = source or "segments"
            units = []
            for s in ET.parse(ami / "segments" / f"{mid}.{lab}.segments.xml").getroot().iter("segment"):
                child = s.find(NITE + "child")
                if child is not None:
                    units.append((_nid(s), child.get("href"), None))
        for uid, href, da in units:
            a, b = words.span(href)
            text = words.text(a, b)
            if not text:
                skipped += 1  # unit made only of vocal sounds / gaps
                continue
            start, end = words.times(a, b)
            if start is None:
                skipped += 1
                continue
            segs.append({"id": uid, "speaker": lab, "start": round(start, 3),
                         "end": round(max(end, start), 3), "text": text, "da_type": da})
    segs.sort(key=lambda s: (s["start"], s["end"], s["speaker"], s["id"]))
    return segs, source, skipped


def _abstractive(ami: Path, mid: str):
    summ = {"abstract": [], "actions": [], "decisions": [], "problems": []}
    sent_ids = {k: [] for k in summ}
    p = ami / "abstractive" / f"{mid}.abssumm.xml"
    if p.exists():
        root = ET.parse(p).getroot()
        for sec in summ:
            for block in root.iter(sec):
                for s in block.iter("sentence"):
                    txt = " ".join((s.text or "").split())
                    if txt:
                        summ[sec].append(txt)
                        sent_ids[sec].append(_nid(s))
    links = {}
    lp = ami / "extractive" / f"{mid}.summlink.xml"
    if lp.exists():
        for sl in ET.parse(lp).getroot().iter("summlink"):
            ptrs = {q.get("role"): HREF.search(q.get("href")).group(1) for q in sl.findall(NITE + "pointer")}
            if "abstractive" in ptrs and "extractive" in ptrs:
                links.setdefault(ptrs["abstractive"], []).append(ptrs["extractive"])
    return summ, sent_ids, links


def convert_meeting(ami: Path, mid: str, meta: dict, da_types: dict) -> dict:
    speakers = meta["speakers"]
    labels = [s["label"] for s in speakers]
    label_of_role = {s["role"]: s["label"] for s in speakers}
    segs, seg_source, skipped = _segments(ami, mid, labels, da_types)
    seg_ids = {s["id"] for s in segs}
    summ, sent_ids, links = _abstractive(ami, mid)

    def evidence(sent_id):
        return [x for x in links.get(sent_id, []) if x in seg_ids]

    action_items = []
    for k, (txt, sid) in enumerate(zip(summ["actions"], sent_ids["actions"]), start=1):
        roles = [r for r, pat in ROLE_PATTERNS.items() if pat.search(txt)]
        owner = label_of_role.get(roles[0]) if len(roles) == 1 else None
        action_items.append({"id": f"{mid}.ai{k}", "description": txt, "owner": owner,
                             "owner_unclear": owner is None, "status": "active",
                             "previous_owners": [], "history": [],
                             "evidence_segments": evidence(sid),
                             "owner_source": "role_mention" if owner else None})
    qa_facts = [{"id": f"{mid}.f{k}", "type": "decision", "fact": txt, "agreed": True,
                 "related_action_item": None, "refers_to_meeting": None, "evidence_segments": evidence(sid)}
                for k, (txt, sid) in enumerate(zip(summ["decisions"], sent_ids["decisions"]), start=1)]
    return {
        "schema_version": "1.0",
        "source": "ami",
        "meeting_id": mid,
        "series_id": mid[:-1],
        "meeting_letter": mid[-1],
        "scenario_type": "scenario",
        "stage": STAGES.get(mid[-1]),
        "duration_sec": meta["duration"],
        "participants": [{"label": s["label"], "role": s["role"], "name": None,
                          "global_id": s["global_id"], "status": "present"} for s in speakers],
        "segments": segs,
        "summary": summ,
        "action_items": action_items,
        "qa_facts": qa_facts,
        "edge_cases": [],
        "provenance": {"converter": "synthetic.ami", "ami_release": "ami_public_manual_1.6.2",
                       "segments_from": seg_source, "skipped_units_without_words": skipped,
                       "has_abstractive": any(summ.values()),
                       "action_item_status": "assumed active (AMI abstractive actions = tasks standing at meeting end)"},
    }


def convert_all(ami: Path, meeting_ids: list[str] | None = None):
    index = load_meetings_index(ami)
    da_types = load_da_types(ami)
    for mid in sorted(meeting_ids or index):
        if mid not in index:
            raise KeyError(f"{mid} is not a scenario meeting in meetings.xml")
        if not (ami / "words" / f"{mid}.{index[mid]['speakers'][0]['label']}.words.xml").exists():
            continue  # listed but no transcription released (e.g. IS1002a, IS1005d)
        yield convert_meeting(ami, mid, index[mid], da_types)
