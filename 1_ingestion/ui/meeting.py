"""One meeting as the UI sees it, live (the bridge runs it now) or only on disk (finished, or the
bridge restarted). Gives transcript lines with stable ids, the people and their consent, the
recording state, the platform and a default title.

Transcript line ids are "t<start_ms>" (the wall-clock start of the first piece in the line), so they
are the same live and after a restart, and summary items can point at them (source_line_ids).
Lines are merged like Session.merged(): same speaker, pause up to MERGE_GAP_MS = one line.
"""

import json
import re
import time

import settings
from live_view import BOT_ID, read_jsonl

PLATFORM_NAMES = {"meet": "Google Meet", "teams": "Microsoft Teams"}


def read_json(path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def write_meta(sid, **fields):
    """sessions/<id>/meta.json: what the UI needs after a restart (first-audio time, title, platform)."""
    folder = settings.SESSIONS_DIR / sid
    if not folder.is_dir():
        return
    meta = read_json(folder / "meta.json", {}) or {}
    changed = {k: v for k, v in fields.items() if v is not None and meta.get(k) != v}
    if changed:
        meta.update(changed)
        (folder / "meta.json").write_text(json.dumps(meta))


def merge_rows(rows):
    """[(start_ms, end_ms, speaker, text)] -> merged lines [[start_ms, end_ms, speaker, text, [starts]]]."""
    out = []
    for start, end, who, text in sorted(rows, key=lambda r: r[0]):
        last = out[-1] if out else None
        if last and last[2] == who and settings.MERGE_GAP_MS > 0 and start - last[1] <= settings.MERGE_GAP_MS:
            last[1] = max(last[1], end)
            last[3] += " " + text
            last[4].append(start)
        else:
            out.append([start, end, who, text, [start]])
    return out


class Meeting:
    def __init__(self, bridge, sid):
        if not BOT_ID.match(sid or ""):
            raise ValueError("bad meeting id")
        self.sid = sid
        self.s = bridge.sessions.get(sid) if bridge is not None else None
        self.folder = settings.SESSIONS_DIR / sid
        self.meta = read_json(self.folder / "meta.json", {}) or {}
        self._consent = None

    # ---------- basics ----------
    @property
    def exists(self):
        return self.s is not None or self.folder.is_dir()

    @property
    def demo(self):
        return bool(self.s.demo if self.s is not None else self.meta.get("demo"))

    def consent_rows(self):
        if self._consent is None:
            self._consent = read_jsonl(self.folder / "consent.jsonl")
        return self._consent

    @property
    def declined(self):
        if self.s is not None:
            return self.s.declined
        return any(e.get("event") in ("declined", "session_deleted") for e in self.consent_rows())

    @property
    def live(self):
        """The bridge is running this meeting now (it hasn't ended, nobody said no)."""
        return self.s is not None and self.s.state != "ended" and not self.s.declined and not self.s.leaving

    @property
    def platform(self):
        if self.meta.get("platform"):
            return self.meta["platform"]
        ids = list(self.s.people) if self.s is not None else [e.get("uuid") for e in self.consent_rows()]
        return "teams" if any(re.match(r"^\d+:", u or "") for u in ids) else "meet"

    def started_ms(self):
        if self.s is not None and self.s.t0_ms:
            return self.s.t0_ms
        if self.meta.get("t0_ms"):
            return self.meta["t0_ms"]
        rows = self.rows()
        if rows:
            return min(r[0] for r in rows)
        asked = [e.get("ms") for e in self.consent_rows() if e.get("event") == "asked" and e.get("ms")]
        return asked[0] if asked else None

    def ended_ms(self):
        if self.meta.get("ended_ms"):
            return self.meta["ended_ms"]
        if self.live:
            return None
        rows = self.rows()
        if rows:
            return max(r[1] for r in rows)
        return None

    def default_title(self):
        """The title until someone renames it. Dates are shown by the browser (in its own time zone)."""
        return self.meta.get("title") or "Untitled meeting"

    # ---------- transcript ----------
    def rows(self):
        """Transcribed pieces: [(start_ms, end_ms, speaker, text)]."""
        if self.s is not None:
            return list(self.s.lines)
        return [(int(p["start_ms"]), int(p.get("speech_end_ms") or p["start_ms"]), p.get("speaker") or "?", p["text"])
                for p in read_jsonl(self.folder / "pieces.jsonl")
                if p.get("text") and p.get("start_ms") is not None]

    def lines(self):
        t0 = self.started_ms() or 0
        out = []
        for start, end, who, text, _ in merge_rows(self.rows()):
            out.append({"id": f"t{start}", "speaker": who, "start": round((start - t0) / 1000, 1),
                        "end": round((end - t0) / 1000, 1), "text": text,
                        "multi": who.startswith("Multiple speakers"), "unknown": who == "Unknown speaker"})
        return out

    def line_id_for(self, start_ms):
        """The id of the (merged) line that holds the piece starting at start_ms."""
        for start, _, _, _, starts in merge_rows(self.rows()):
            if start_ms in starts:
                return f"t{start}"
        return None

    # ---------- people and consent ----------
    def people(self):
        """[{"id", "name", "present", "decision", "vouched_by", "asked"}], in joining order."""
        if self.s is not None:
            return [{"id": uuid, "name": p.name, "present": p.present, "decision": p.decision,
                     "vouched_by": p.vouched_by, "asked": p.asked_ms is not None}
                    for uuid, p in self.s.people.items() if p.name]
        people = {}
        for e in self.consent_rows():
            ev = e.get("event")
            if ev in ("joined", "left") and e.get("uuid"):
                p = people.setdefault(e["uuid"], {"id": e["uuid"], "name": e.get("name"), "present": False,
                                                  "decision": None, "vouched_by": None, "asked": True})
                p["name"] = e.get("name") or p["name"]
                p["present"] = False  # not live any more
            elif ev == "reply" and e.get("decision"):
                target = e.get("on_behalf_of") or e.get("matched")
                for p in people.values():
                    if p["name"] == target:
                        p["decision"] = e["decision"]
                        if e.get("on_behalf_of"):
                            p["vouched_by"] = e.get("matched")
        return list(people.values())

    def consented(self):
        return [p["name"] for p in self.people() if p["decision"] == "yes" and p["name"]]

    def status(self):
        """(status, waiting names). status: joining | recording | paused | ended | declined."""
        if self.declined:
            return "declined", []
        if not self.live:
            return "ended", []
        s = self.s
        if s.recording:
            return "recording", []
        waiting = [p.name for p in s.present() if p.decision != "yes"]
        return ("paused" if s.prompted else "joining"), waiting

    def activity(self, after=0):
        """Bot activity (state, chat, consent) for the organizer view."""
        if self.s is not None:
            return self.s.activity[after:]
        from live_view import saved_activity
        return saved_activity(self.sid)[after:]

    def changed_ms(self):
        return int(self.folder.stat().st_mtime * 1000) if self.folder.is_dir() else int(time.time() * 1000)
