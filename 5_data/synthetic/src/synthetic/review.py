"""Human review: export, inter-reviewer agreement, and applying decisions.

review-export  data/raw -> review/export/<meeting>.md  (readable transcript + gold, edge cases highlighted)
                         review/forms/<meeting>.review.json (one item per gold label)
agreement      Cohen's kappa + percent agreement between reviewer_1 and reviewer_2
review-apply   forms + data/raw -> data/reviewed/<release>/ (new, immutable; cache/ and data/raw/ untouched)

Each form item has:
  reviewer_1 / reviewer_2: {"name", "decision": accept|reject|edit|null, "edit": <replacement>, "comment"}
  resolution:              same fields, filled in by the third reviewer when 1 and 2 disagree
Final decision for an item = resolution.decision if set, else the shared decision of
reviewers 1 and 2 (for "edit", both edits must be identical, otherwise it needs a resolution).
"""
from __future__ import annotations

import copy
from pathlib import Path

from .plan import ROLE_NAMES
from .util import read_json, sha256_file, write_json, write_text
from .validate import validate_meeting

DECISIONS = ("accept", "reject", "edit")


def _blank():
    return {"name": "", "decision": None, "edit": None, "comment": ""}


def _items(m: dict) -> list[dict]:
    items = [{"item_id": "meeting", "kind": "meeting",
              "gold": "Accept = usable meeting. Reject = exclude the whole meeting from the release."},
             {"item_id": "transcript", "kind": "transcript",
              "gold": "Edit = a list of {\"id\": segment id, \"text\": new text} corrections."}]
    for sec in ("abstract", "actions", "decisions", "problems"):
        items.append({"item_id": f"summary.{sec}", "kind": "summary", "gold": m["summary"][sec]})
    for a in m["action_items"]:
        items.append({"item_id": f"action_items/{a['id']}", "kind": "action_item", "gold": a})
    for f in m["qa_facts"]:
        items.append({"item_id": f"qa_facts/{f['id']}", "kind": "qa_fact", "gold": f})
    for e in m["edge_cases"]:
        items.append({"item_id": f"edge_cases/{e['scenario_id']}", "kind": "edge_case", "gold": e})
    for it in items:
        it.update(reviewer_1=_blank(), reviewer_2=_blank(), resolution=_blank())
    return items


def _markdown(m: dict) -> str:
    who = {p["label"]: p for p in m["participants"]}
    tagged = {}
    for e in m["edge_cases"]:
        for s in e["segments"]:
            tagged.setdefault(s, []).append(e["scenario_id"])
    L = [f"# {m['meeting_id']} ({m.get('stage') or 'n/a'})", "",
         f"Series {m['series_id']}, source {m['source']}. "
         f"Planned edge cases: {', '.join(m.get('provenance', {}).get('planned_scenarios', [])) or 'none'}.", "",
         "## Participants", ""]
    for p in m["participants"]:
        L.append(f"- **{p['label']}** {p.get('name') or ''}: {ROLE_NAMES.get(p['role'], p['role'])}, {p['status']}")
    L += ["", "## Edge cases", ""]
    for e in m["edge_cases"]:
        L.append(f"- **{e['scenario_id']}**: segments {', '.join(s.rsplit('.', 1)[1] for s in e['segments'])}. {e.get('note', '')}")
    L += ["", "## Transcript", "", "Lines marked ▶ are tagged edge-case segments.", ""]
    for s in m["segments"]:
        mark = f" ▶ `{'/'.join(tagged[s['id']])}`" if s["id"] in tagged else ""
        L.append(f"`{s['id'].rsplit('.', 1)[1]}` [{s['start']:.1f}] **{s['speaker']}**: {s['text']}{mark}  ")
    L += ["", "## Gold: summary (F1)", ""]
    for sec in ("abstract", "actions", "decisions", "problems"):
        L.append(f"**{sec}**")
        L += [f"- {x}" for x in m["summary"][sec]] or ["- (none)"]
        L.append("")
    L += ["## Gold: action items (F2)", ""]
    for a in m["action_items"]:
        owner = a["owner"] or "NO OWNER"
        if a["owner"] and who.get(a["owner"], {}).get("status") == "absent":
            owner += " (absent)"
        prev = f", previously {', '.join(a['previous_owners'])}" if a.get("previous_owners") else ""
        L.append(f"- `{a['id']}` {a['description']}: owner **{owner}**, status **{a['status']}**{prev}")
        for h in a.get("history", []):
            L.append(f"  - {h['segment'].rsplit('.', 1)[1]}: {h['event']} by {h.get('by')} -> {h.get('owner')}")
    L += ["", "## Gold: Q&A facts (F3)", ""]
    for f in m["qa_facts"]:
        extra = f", agreed={f['agreed']}" if f.get("agreed") is not None else ""
        ref = f", refers to {f['refers_to_meeting']}" if f.get("refers_to_meeting") else ""
        L.append(f"- `{f['id']}` [{f['type']}{extra}{ref}] {f['fact']}")
    L.append("")
    return "\n".join(L)


def export(raw_dir: Path, review_dir: Path, overwrite: bool = False) -> int:
    index = read_json(raw_dir / "index.json")
    n = 0
    for row in index:
        m = read_json(raw_dir / row["file"])
        mid = m["meeting_id"]
        write_text(review_dir / "export" / f"{mid}.md", _markdown(m))
        form_path = review_dir / "forms" / f"{mid}.review.json"
        if form_path.exists() and not overwrite:
            continue  # never clobber reviewers' work
        write_json(form_path, {"meeting_id": mid, "raw_sha256": sha256_file(raw_dir / row["file"]),
                               "instructions": __doc__.split("Each form item has:")[1].strip(),
                               "items": _items(m)})
        n += 1
    return n


# ---------------------------------------------------------------- agreement
def cohen_kappa(pairs: list[tuple[str, str]]) -> float | None:
    if not pairs:
        return None
    n = len(pairs)
    po = sum(a == b for a, b in pairs) / n
    cats = set(DECISIONS)
    pe = sum((sum(a == c for a, _ in pairs) / n) * (sum(b == c for _, b in pairs) / n) for c in cats)
    return 1.0 if pe == 1 else round((po - pe) / (1 - pe), 4)


def agreement(review_dir: Path) -> dict:
    pairs, by_kind, pending, disagreements = [], {}, 0, []
    for p in sorted((review_dir / "forms").glob("*.review.json")):
        form = read_json(p)
        for it in form["items"]:
            a, b = it["reviewer_1"]["decision"], it["reviewer_2"]["decision"]
            if a is None or b is None:
                pending += 1
                continue
            pairs.append((a, b))
            by_kind.setdefault(it["kind"], []).append((a, b))
            if a != b or (a == b == "edit" and it["reviewer_1"]["edit"] != it["reviewer_2"]["edit"]):
                disagreements.append({"meeting_id": form["meeting_id"], "item_id": it["item_id"],
                                      "reviewer_1": a, "reviewer_2": b,
                                      "resolved": it["resolution"]["decision"] is not None})
    pct = round(sum(a == b for a, b in pairs) / len(pairs), 4) if pairs else None
    return {"items_compared": len(pairs), "items_pending": pending,
            "percent_agreement": pct, "cohen_kappa": cohen_kappa(pairs),
            "by_kind": {k: {"n": len(v), "percent_agreement": round(sum(a == b for a, b in v) / len(v), 4),
                            "cohen_kappa": cohen_kappa(v)} for k, v in sorted(by_kind.items())},
            "disagreements": disagreements}


# ---------------------------------------------------------------- apply
class ReviewError(RuntimeError):
    pass


def _final(it: dict, mid: str):
    r = it["resolution"]
    if r["decision"] is not None:
        return r["decision"], r["edit"]
    a, b = it["reviewer_1"], it["reviewer_2"]
    if a["decision"] is None or b["decision"] is None:
        raise ReviewError(f"{mid} {it['item_id']}: not reviewed by both reviewers")
    if a["decision"] != b["decision"] or (a["decision"] == "edit" and a["edit"] != b["edit"]):
        raise ReviewError(f"{mid} {it['item_id']}: reviewers disagree and there is no resolution")
    return a["decision"], a["edit"]


def apply(raw_dir: Path, review_dir: Path, reviewed_root: Path, release: str) -> dict:
    out = reviewed_root / release
    if out.exists():
        raise ReviewError(f"release {out} already exists; releases are immutable, pick a new name")
    index = read_json(raw_dir / "index.json")
    forms = {}
    for row in index:
        fp = review_dir / "forms" / f"{row['meeting_id']}.review.json"
        if not fp.exists():
            raise ReviewError(f"missing review form {fp}")
        form = read_json(fp)
        if form["raw_sha256"] != sha256_file(raw_dir / row["file"]):
            raise ReviewError(f"{row['meeting_id']}: form was exported from a different raw build")
        forms[row["meeting_id"]] = (row, form, fp)

    results, kept, excluded, problems = {}, [], [], []
    for mid, (row, form, fp) in forms.items():
        m = copy.deepcopy(read_json(raw_dir / row["file"]))
        stats = {"accept": 0, "reject": 0, "edit": 0}
        decisions = {it["item_id"]: (_final(it, mid), it) for it in form["items"]}
        if decisions["meeting"][0][0] == "reject":
            excluded.append(mid)
            continue
        for item_id, ((dec, edit), it) in decisions.items():
            if dec not in DECISIONS:
                raise ReviewError(f"{mid} {item_id}: invalid decision {dec!r}")
            stats[dec] += 1
            if item_id == "meeting" or dec == "accept":
                continue
            if item_id == "transcript":
                if dec == "edit":
                    seg = {s["id"]: s for s in m["segments"]}
                    for e in edit or []:
                        seg[e["id"]]["text"] = e["text"]
                continue
            kind, key = (item_id.split(".", 1) if item_id.startswith("summary.") else item_id.split("/", 1))
            if kind == "summary":
                m["summary"][key] = edit if dec == "edit" else []
                continue
            coll = m[kind]
            field = "scenario_id" if kind == "edge_cases" else "id"
            pos = next(i for i, x in enumerate(coll) if x[field] == key)
            if dec == "reject":
                coll.pop(pos)
            else:
                coll[pos] = edit
        m.setdefault("provenance", {})["review"] = {"release": release, "form_sha256": sha256_file(fp),
                                                    "decisions": stats}
        errs = validate_meeting(m)
        if errs:
            problems.append({"meeting_id": mid, "errors": errs})
        results[mid] = m
        kept.append(mid)
    if problems:
        raise ReviewError("edited meetings fail validation; fix the edits:\n" +
                          "\n".join(f"{p['meeting_id']}: {e}" for p in problems for e in p["errors"][:5]))
    for mid, m in results.items():
        write_json(out / "meetings" / f"{mid}.json", m)
    files = {p.relative_to(out).as_posix(): sha256_file(p) for p in sorted(out.rglob("*")) if p.is_file()}
    manifest = {"release": release, "raw_manifest_sha256": sha256_file(raw_dir / "manifest.json"),
                "kept": sorted(kept), "excluded": sorted(excluded),
                "forms": {mid: sha256_file(fp) for mid, (_, _, fp) in sorted(forms.items())},
                "files": files}
    write_json(out / "manifest.json", manifest)
    return manifest
