"""Meeting minutes as Markdown: title, date, attendees, summary, decisions, action items with owners,
and a note on the edit history. All text from people (transcript, edits) is escaped, so the file can't
carry HTML or break the table into something else when a Markdown viewer shows it."""

import re
from datetime import date, datetime, timedelta, timezone

STATUS_WORDS = {"pending": "Pending", "approved": "Approved", "done": "Done"}
_SPECIAL = re.compile(r"([\\`*_\[\]#|~])")


def esc(text):
    text = " ".join(str(text or "").split())
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return _SPECIAL.sub(r"\\\1", text)


def _tz(offset_min):
    """offset_min is the browser's Date.getTimezoneOffset() (minutes behind UTC, e.g. 300 for New York)."""
    try:
        offset = max(-14 * 60, min(14 * 60, int(offset_min)))
    except (TypeError, ValueError):
        offset = 0
    return timezone(-timedelta(minutes=offset))


def _when(ms, tz, fmt):
    return datetime.fromtimestamp(ms / 1000, tz).strftime(fmt) if ms else ""


def _clock(ms, tz):
    return _when(ms, tz, "%I:%M %p").lstrip("0")


def _mmss(seconds):
    s = max(0, int(seconds or 0))
    return f"{s // 60:02d}:{s % 60:02d}"


def markdown(snap, tz_offset_min=0, exported_ms=None):
    tz = _tz(tz_offset_min)
    session, minutes = snap["session"], snap["session"]["minutes"]
    lines = {l["id"]: l for l in snap["lines"]}
    out = [f"# {esc(session['title'])}", ""]

    start, end = session.get("started_ms"), session.get("ended_ms")
    when = _when(start, tz, "%a, %b %d, %Y").replace(" 0", " ")
    if start:
        when += f", {_clock(start, tz)}"
        if end and end > start:
            when += f" to {_clock(end, tz)} ({max(1, round((end - start) / 60000))} min)"
    platform = {"meet": "Google Meet", "teams": "Microsoft Teams"}.get(session.get("platform"), "Meeting")
    out.append(f"*Meeting minutes · {platform}" + (f" · {when}" if when else "") + "*")
    out.append("")

    attendees = [p["name"] for p in snap["people"] if p.get("decision") == "yes"]
    out.append(f"**Attendees:** {', '.join(esc(n) for n in attendees) or 'none recorded'}  ")
    if minutes.get("approved"):
        a = minutes["approved"]
        status = f"Approved by {esc(a['by'])} on {_when(a['ms'], tz, '%b %d, %Y').replace(' 0', ' ')} at {_clock(a['ms'], tz)}"
        if minutes.get("changed_since"):
            status += " (edited after approval)"
    else:
        status = "Draft, not approved yet"
    out.append(f"**Status:** {status}")
    out.append("")

    if session.get("demo"):
        out += ["> Demo meeting: the people and the conversation are invented (LiveMinutes Demo mode).", ""]
    elif snap["summary"].get("sample") or snap["actions"].get("sample"):
        out += ["> Summary and action items are **sample data** (the summarizer isn't connected yet), not from this meeting.", ""]

    def sources(item):
        times = list(dict.fromkeys(_mmss(lines[i]["start"]) for i in item.get("source_line_ids", []) if i in lines))
        return f" *(at {', '.join(times)})*" if times else ""

    points = [i for i in snap["summary"]["items"] if i.get("type") != "decision"]
    decisions = [i for i in snap["summary"]["items"] if i.get("type") == "decision"]
    out.append("## Summary")
    out += [f"- {esc(i['text'])}{sources(i)}" for i in points] or ["- (nothing yet)"]
    out.append("")
    out.append("## Decisions")
    out += [f"- {esc(i['text'])}{sources(i)}" for i in decisions] or ["- (none recorded)"]
    out.append("")

    out.append("## Action items")
    actions = snap["actions"]["items"]
    if actions:
        out += ["| # | Task | Owner | Due | Status |", "|---|---|---|---|---|"]
        for n, a in enumerate(actions, 1):
            due = ""
            if a.get("due"):
                try:
                    due = date.fromisoformat(a["due"]).strftime("%a, %b %d").replace(" 0", " ")
                except ValueError:
                    due = esc(a["due"])
            out.append(f"| {n} | {esc(a['text'])} | {esc(a.get('owner')) or 'Unassigned'} | {due or '-'} | "
                       f"{STATUS_WORDS.get(a.get('status'), esc(a.get('status')))} |")
    else:
        out.append("- (none)")
    out.append("")

    out.append("## Edit history")
    log = snap.get("log") or {}
    if log.get("changes"):
        people = ", ".join(esc(p) for p in log.get("people", []))
        out.append(f"{log['changes']} change{'s' if log['changes'] != 1 else ''} by {people}. "
                   "Every change (who, when, before and after) is kept in the meeting's edit log.")
    else:
        out.append("No edits: these are the notes as generated.")
    out += ["", "---", f"*Exported from LiveMinutes on {_when(exported_ms, tz, '%b %d, %Y').replace(' 0', ' ')}"
            f" at {_clock(exported_ms, tz)}.*" if exported_ms else "*Exported from LiveMinutes.*", ""]
    return "\n".join(out)
