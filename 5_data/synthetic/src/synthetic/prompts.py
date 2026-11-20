"""Render versioned prompt templates (prompts/<version>/*.txt) for one meeting."""
from __future__ import annotations

from string import Template

from .plan import ROLE_NAMES
from .util import resolve, sha256_text

TEMPLATE_FILES = ("system.txt", "user.txt", "repair.txt")


def load_templates(version: str) -> dict[str, str]:
    base = resolve(f"prompts/{version}")
    out = {}
    for name in TEMPLATE_FILES:
        with open(base / name, encoding="utf-8", newline="") as f:
            out[name] = f.read().replace("\r\n", "\n")
    return out


def template_hashes(templates: dict[str, str]) -> dict[str, str]:
    return {k: sha256_text(v) for k, v in sorted(templates.items())}


def _names_from_prior(prior: list[dict]) -> dict:
    if not prior:
        return {}
    return {p["label"]: p.get("name") for p in prior[0]["participants"]}


def _prior_block(prior: list[dict]) -> str:
    if not prior:
        return "This is the first meeting of the series. Introduce the project and the team."
    lines = ["EARLIER MEETINGS IN THIS SERIES",
             "Stay consistent with them. Refer back to them, follow up on their open action items, and keep the same product and team."]
    for m in prior:
        lines.append(f"\n[{m['meeting_id']}, {m['stage']}]")
        s = m["summary"]
        for sec in ("abstract", "decisions", "actions", "problems"):
            if s[sec]:
                lines.append(f"  {sec}: " + " ".join(s[sec]))
        for ai in m["action_items"]:
            owner = ai["owner"] or "no owner"
            lines.append(f"  action item {ai['id']}: {ai['description']} (owner {owner}, {ai['status']})")
        for f in m["qa_facts"]:
            if f["type"] in ("unagreed_proposal", "absence", "plan_change", "action_item_removed"):
                lines.append(f"  fact ({f['type']}): {f['fact']}")
    return "\n".join(lines)


def render(meeting_plan: dict, scenarios_by_id: dict, prior: list[dict], templates: dict,
           attempt: int, max_attempts: int, errors: list[str] | None) -> dict:
    names = _names_from_prior(prior)
    plines = []
    for p in meeting_plan["participants"]:
        name = f"  {names[p['label']]}" if names.get(p["label"]) else ""
        status = "ABSENT, never speaks" if p["status"] == "absent" else "present"
        plines.append(f"{p['label']}  {ROLE_NAMES[p['role']]} ({p['role']}){name}  {status}")
    slines = []
    for sid in meeting_plan["scenario_ids"]:
        sc = scenarios_by_id[sid]
        slines.append(f"- {sid} [{sc['feature']}]: {sc['description']}\n  Required gold labels: {', '.join(sc['required_gold'])}.")
    if prior:
        names_shape = ""
        prior_rule = (" Because this meeting follows earlier ones, include at least one fact of type "
                      "reference_to_prior_meeting, with refers_to_meeting set, at a point where someone "
                      "refers back to an earlier meeting.")
    else:
        names_shape = '  "names": {"A": "first name", "B": "...", "C": "...", "D": "..."},\n'
        prior_rule = ""
    lo, hi = meeting_plan["target_segments"]
    user = Template(templates["user.txt"]).substitute(
        meeting_id=meeting_plan["meeting_id"],
        meeting_letter=meeting_plan["meeting_letter"],
        stage=meeting_plan["stage"],
        series_id=meeting_plan["series_id"],
        product=meeting_plan["product"],
        participants_block="\n".join(plines),
        prior_block=_prior_block(prior),
        scenarios_block="\n".join(slines),
        min_segments=lo, max_segments=hi,
        prior_fact_rule=prior_rule,
        names_shape=names_shape,
    )
    if attempt > 0:
        user += Template(templates["repair.txt"]).substitute(
            attempt=attempt + 1, max_attempts=max_attempts,
            errors_block="\n".join(f"- {e}" for e in (errors or [])[:40]))
    return {"system": templates["system.txt"], "user": user}
