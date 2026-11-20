"""The LLM's live notes as the meeting page's summary items and action items (docs/UI_CONTRACT.md).

The notes come from Jithin's code, unchanged: every 30 s notes.py sends the new transcript lines and the notes so
far to 2_summarization/llm_summarizer.py, which returns {"summary": prose, "action_items": [{task, owner, due}]}.
This module turns each new version of those notes into contract items, once per LLM update (not per page build):

  ids        stable across updates. Each new summary sentence / action item is matched to the items of the last
             update (best first, by text similarity: word overlap or difflib ratio, whichever is higher). A match
             keeps its id, and its version goes up if the text changed; anything else gets a new id from a
             counter that only grows (llm-s1, llm-s2 ...; llm-a1 ...). So an item a person edited, approved or
             deleted keeps its id, and the edit log keeps it frozen (ui/editlog.py) whatever the LLM writes next.
  summary    the LLM writes prose; each sentence becomes one summary point ("decision" if it says decided /
             agreed / chose ...).
  owners     matched to the people who said yes ("Jithin Veeragandham" is "Veeragandham, Jithin"; a first name
             counts if only one person has it). No match: "" and the text ends with "(owner said: X)".
  due        "by Friday", "tomorrow", "tonight", "November 21", ISO dates: a YYYY-MM-DD date counted from the
             meeting's day. Phrases that name no day ("next meeting") stay in due_text and due is "".
  sources    the LLM gives none. A new action item is mapped to the lines of the update it first appeared in (the
             lines sent that time, plus the context lines resent with them), then to the 1-2 lines there that share
             the most words with the task. A summary point: the 1-3 lines of the whole meeting that share the most
             words with it. Matched items keep their lines.

Kept in sessions/<meeting>/llm_items.json (ids, counters, which lines were sent), so a bridge restart keeps the ids.
"""
import difflib
import json
import re
from datetime import date, datetime, timedelta

STATE_FILE = "llm_items.json"
SUMMARY_MATCH = 0.5      # similarity needed to be "the same point" as an item of the last update
ACTION_MATCH = 0.5
CONTEXT_LINES = 6        # lines of the previous update resent as context (llm_summarizer: context_lines)
_books = {}              # meeting id -> Book

STOP = set("""a an the and or but so to of in on at for with from by about into this that these those it its is are
was were be been being have has had do does did will would can could should shall may might must they them their
he she his her we our you your i me my not no yes also then than just very more most some any all each which who
what when where how as if there here up out over""".split())
NOBODY = {"unassigned", "none", "nobody", "no one", "everyone", "everybody", "the team", "team", "all", "unknown",
          "the group", "group"}
DECISION = re.compile(r"\b(decided|agreed|agree on|chose|chosen|settled|approved|confirmed|will go with|went with)\b", re.I)


def words(text):
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in STOP and len(w) > 2}


def similarity(a, b):
    wa, wb = words(a), words(b)
    jac = len(wa & wb) / len(wa | wb) if wa and wb else 0.0
    return max(jac, difflib.SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio())


def pair_up(new, old, threshold):
    """{index in new: index in old}, one to one, best first, similarity >= threshold."""
    scored = sorted(((similarity(n, o), i, j) for i, n in enumerate(new) for j, o in enumerate(old)), reverse=True)
    out, used = {}, set()
    for score, i, j in scored:
        if score < threshold:
            break
        if i not in out and j not in used:
            out[i] = j
            used.add(j)
    return out


# ================= summary sentences =================

def sentences(summary):
    """The summary as points: one sentence each (paragraphs first, if the LLM used any)."""
    out = []
    for para in re.split(r"\n\s*\n|\n", summary or ""):
        para = " ".join(para.split())
        for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])", para):
            s = s.strip()
            if not s:
                continue
            if out and len(s.split()) < 4:   # a fragment ("Fine.") goes with the sentence before it
                out[-1] += " " + s
            else:
                out.append(s)
    return out


# ================= owners and due dates =================

def tokens(name):
    return [t for t in re.findall(r"[a-z0-9]+", (name or "").lower())]


def match_owner(said, names):
    """The consented name the LLM's owner means, or None. Order-free ("Jithin Veeragandham" = "Veeragandham, Jithin");
    a first name or a part of a name counts if it fits only one person."""
    t = tokens(said)
    if not t:
        return None
    if " ".join(t) in NOBODY:
        return None
    exact = [n for n in names if sorted(tokens(n)) == sorted(t)]
    if len(exact) == 1:
        return exact[0]
    part = [n for n in names if set(t) <= set(tokens(n))] or [n for n in names if set(tokens(n)) <= set(t)]
    return part[0] if len(part) == 1 else None


WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]


def resolve_due(text, day):
    """YYYY-MM-DD for a due phrase, counted from the meeting's day; "" if it names no day."""
    t = (text or "").lower()
    if not t.strip():
        return ""
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", t)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
        except ValueError:
            return ""
    if re.search(r"\b(today|tonight|this (morning|afternoon|evening)|end of (the )?day|eod)\b", t):
        return day.isoformat()
    if re.search(r"\btomorrow\b", t):
        return (day + timedelta(days=1)).isoformat()
    if re.search(r"\b(end of (the |this )?week|this week|the weekend)\b", t):
        return (day + timedelta(days=(4 - day.weekday()) % 7)).isoformat()
    month = "|".join(MONTHS) + "|" + "|".join(m[:3] for m in MONTHS)
    m = re.search(rf"\b({month})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b|\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({month})\b", t)
    if m:
        name, num = (m[1], m[2]) if m[1] else (m[4], m[3])
        month_no = next(i for i, full in enumerate(MONTHS, 1) if full.startswith(name[:3]))
        try:
            d = date(day.year, month_no, int(num))
        except ValueError:
            return ""
        return (d if d >= day else date(day.year + 1, month_no, int(num))).isoformat()
    m = re.search(r"\b(" + "|".join(WEEKDAYS) + r")\b", t)
    if m and "next week" not in t:
        ahead = (WEEKDAYS.index(m[1]) - day.weekday()) % 7 or 7
        return (day + timedelta(days=ahead)).isoformat()
    return ""


# ================= source lines =================

def line_ms(line):
    return int(line["id"][1:]) if line["id"][1:].isdigit() else 0


def best_lines(text, lines, k):
    """Ids of the k lines that share the most words with text (at least 2 shared words), in time order."""
    want = words(text)
    scored = []
    for line in lines:
        shared = len(want & words(line["text"]))
        if shared >= 2:
            scored.append((shared, -line["start"], line))
    top = sorted(scored, key=lambda x: (x[0], x[1]), reverse=True)[:k]
    return [l["id"] for _, _, l in sorted(top, key=lambda x: x[2]["start"])]


# ================= the book: one meeting's items =================

class Book:
    def __init__(self, d=None):
        d = d or {}
        self.key = d.get("key")
        self.next_s, self.next_a = d.get("next_s", 1), d.get("next_a", 1)
        self.summary, self.actions = d.get("summary", []), d.get("actions", [])
        self.seen = list(d.get("seen", []))        # ids of the lines already sent to the LLM, in order
        self.updated_ms = d.get("updated_ms")

    def to_dict(self):
        return {"key": self.key, "next_s": self.next_s, "next_a": self.next_a, "summary": self.summary,
                "actions": self.actions, "seen": self.seen, "updated_ms": self.updated_ms}

    @staticmethod
    def public(items):
        return [{k: v for k, v in i.items() if not k.startswith("_")} for i in items]


def source_of(meeting):
    """(key, notes dict, lines sent so far: wall-clock ms or None = all, updated ms) of the meeting's latest LLM
    notes, or None if there are none."""
    s = getattr(meeting, "s", None)
    st = getattr(s, "notes", None) if s is not None else None
    if st is not None and st.notes is not None:
        return f"live:{st.part}", st.notes.to_dict(), st.sent_until_ms, st.updated_ms
    folder = getattr(meeting, "folder", None)
    path = folder / "notes.json" if folder is not None else None
    if path is not None and path.exists():
        try:
            d = json.loads(path.read_text())
        except ValueError:
            return None
        if d.get("summary") or d.get("action_items"):
            return f"saved:{d.get('updated_ms')}", d, None, d.get("updated_ms")
    return None


def load(meeting):
    if meeting.sid not in _books:
        path = meeting.folder / STATE_FILE
        try:
            _books[meeting.sid] = Book(json.loads(path.read_text()) if path.exists() else None)
        except ValueError:
            _books[meeting.sid] = Book()
    return _books[meeting.sid]


def forget(sid):
    _books.pop(sid, None)


def meeting_day(meeting):
    ms = meeting.started_ms()
    return datetime.fromtimestamp(ms / 1000).date() if ms else date.today()


def current(meeting):
    """The meeting's LLM items (a Book), brought up to date with the latest notes; None if the LLM wrote none."""
    src = source_of(meeting)
    if src is None:
        return None
    key, notes, sent_until, updated_ms = src
    book = load(meeting)
    if book.key != key:
        rebuild(book, meeting, notes, sent_until)
        book.key, book.updated_ms = key, updated_ms
        if meeting.folder.is_dir():
            (meeting.folder / STATE_FILE).write_text(json.dumps(book.to_dict()))
    remap(book, meeting)
    return book


def remap(book, meeting):
    """Keep source_line_ids pointing at lines that exist. A line's id is its first piece's start; when later pieces
    merge lines (or a late piece starts one earlier), an id an item stored can stop being a line id. Map each stored
    id (a piece start) to the line that holds that piece now. Only redone when the transcript changed."""
    rows = meeting.rows()
    if getattr(book, "_rows", None) == len(rows):
        return
    from ui.meeting import merge_rows
    line_of = {start: f"t{line[0]}" for line in merge_rows(rows) for start in line[4]}
    for item in book.summary + book.actions:
        ids = []
        for lid in item.get("source_line_ids", []):
            new = line_of.get(int(lid[1:])) if lid[1:].isdigit() else None
            if new and new not in ids:
                ids.append(new)
        item["source_line_ids"] = ids
    book._rows = len(rows)


def rebuild(book, meeting, notes, sent_until):
    lines = meeting.lines()
    sent = [l for l in lines if sent_until is None or line_ms(l) <= sent_until]
    seen = set(book.seen)
    new_lines = [l for l in sent if l["id"] not in seen]
    window = [l for l in sent if l["id"] in seen][-CONTEXT_LINES:] + new_lines
    book.seen = [l["id"] for l in sent]
    names = meeting.consented()
    day = meeting_day(meeting)

    # summary points
    texts = [t[:600] for t in sentences(notes.get("summary", ""))]
    pairs = pair_up(texts, [i["text"] for i in book.summary], SUMMARY_MATCH)
    out = []
    for n, text in enumerate(texts):
        old = book.summary[pairs[n]] if n in pairs else None
        if old is None:
            old = {"id": f"llm-s{book.next_s}", "version": 0, "source_line_ids": []}
            book.next_s += 1
        changed = old.get("text") != text
        out.append({"id": old["id"], "type": "decision" if DECISION.search(text) else "point", "text": text,
                    "version": old["version"] + 1 if changed else old["version"],
                    "source_line_ids": old["source_line_ids"] if not changed and old["source_line_ids"]
                    else best_lines(text, sent, 3)})
    book.summary = out

    # action items
    raw = [a for a in notes.get("action_items", []) if (a.get("task") or "").strip()]
    pairs = pair_up([a["task"] for a in raw], [i.get("_task", i["text"]) for i in book.actions], ACTION_MATCH)
    out = []
    for n, a in enumerate(raw):
        old = book.actions[pairs[n]] if n in pairs else None
        if old is None:
            old = {"id": f"llm-a{book.next_a}", "version": 0,
                   "source_line_ids": best_lines(a["task"], window, 2) or best_lines(a["task"], sent, 2)}
            book.next_a += 1
        owner_said, due_text = (a.get("owner") or "").strip(), (a.get("due") or "").strip()
        owner = match_owner(owner_said, names) or ""
        text = a["task"].strip()
        if owner_said and not owner and tokens(owner_said) and " ".join(tokens(owner_said)) not in NOBODY:
            text = f"{text} (owner said: {owner_said})"   # someone not in the meeting, or not matched
        item = {"id": old["id"], "text": text[:300], "owner": owner, "due": resolve_due(due_text, day),
                "due_text": due_text, "status": "pending", "source_line_ids": old["source_line_ids"],
                "_task": a["task"]}
        same = all(old.get(k) == item[k] for k in ("text", "owner", "due", "due_text"))
        item["version"] = old["version"] if same else old["version"] + 1
        out.append(item)
    book.actions = out


# ================= the status line on the page =================

def status(meeting, notes_job, organizer):
    """What the page shows about the live notes: on/off, when the last update was, when the next one is due,
    whether one is running (and since when), the last error, and for the organizer each update's latency.
    notes_job: the bridge's notes.LiveNotes (None: unknown, e.g. adapters called without the bridge)."""
    if meeting.demo:
        return None
    s = meeting.s
    st = getattr(s, "notes", None) if s is not None else None
    saved = None
    if st is None:
        path = meeting.folder / "notes.json"
        try:
            saved = json.loads(path.read_text()) if path.exists() else None
        except ValueError:
            saved = None
    enabled = bool(notes_job and notes_job.enabled)
    has_notes = (st is not None and st.notes is not None) or saved is not None
    if not enabled and not has_notes:
        if notes_job is None:
            return None
        return {"enabled": False, "reason": notes_job.reason if organizer else ""}
    hist = (st.history if st is not None else (saved or {}).get("history", [])) or []
    ok = [h for h in hist if h.get("status") == "ok"]
    out = {
        "enabled": enabled or st is None,                 # a finished meeting's saved notes count as on
        "reason": notes_job.reason if (organizer and notes_job and not enabled and st is not None) else "",
        "live": meeting.live and st is not None,
        "model": (notes_job.model if enabled else (saved or {}).get("model", "")).split(":", 1)[-1],
        "interval_s": (saved or {}).get("interval_s") or __import__("settings").NOTES_INTERVAL_S,
        "updates": st.part if st is not None else len(ok),
        "updated_ms": st.updated_ms if st is not None else (saved or {}).get("updated_ms"),
        "next_due_ms": st.next_due_ms if st is not None and meeting.live else None,
        "busy": bool(st is not None and st.busy),
        "busy_since_ms": getattr(st, "busy_since_ms", None) if st is not None and st.busy else None,
        "error": (st.error or "")[:160] if st is not None else "",
        "last_seconds": ok[-1].get("seconds") if ok else None,
        "waiting": "first audio" if (s is not None and s.t0_ms is None and meeting.live) else "",
    }
    if organizer:
        out["history"] = [{k: h.get(k) for k in ("time", "ms", "part", "status", "from", "to", "lines", "words",
                                                 "seconds", "input_tokens", "output_tokens", "error", "note", "sent")}
                          for h in hist[-30:]]
        if notes_job is not None and enabled:
            import time as _t
            now = int(_t.time() * 1000)
            out["rate"] = {"minute": notes_job.calls_since(now - 60_000),
                           "minute_limit": __import__("settings").NOTES_RPM_LIMIT}
    return out
