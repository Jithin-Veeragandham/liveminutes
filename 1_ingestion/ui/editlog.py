"""Edits to a meeting's summary, action items, title and minutes: an append-only log per meeting.

sessions/<id>/edits.jsonl, one JSON line per changed field:
  {"seq", "ms", "who", "role", "change", "op", "kind", "item", "field", "before", "after", "version", "undo_of"?}
  op: create | update | delete | undo | approve.  One save that changes 2 fields = 2 lines, same "change" id.

Nothing is ever rewritten or removed, so the history is complete. The current state is the generated
items (ui/adapters.py) with the log replayed on top. Every item has a version: a save must name the
version it started from (base_version); if someone saved in between, it is refused with Conflict, and
the page shows both versions instead of silently overwriting. Undo writes new lines that put the old
values back (it never deletes a line), and only for an item's latest change.
"""

import asyncio
import json
import os
import re
import secrets
from datetime import date

import settings
from ui.access import now_ms

STATUSES = {"summary": ("draft", "approved"), "action": ("pending", "approved", "done")}
EDITABLE = {"summary": ("text", "status"), "action": ("text", "owner", "due", "status"), "meeting": ("title",)}
TEXT_LIMIT = {"summary": 600, "action": 300}
OWNER_LIMIT, TITLE_LIMIT = 80, 120


class Invalid(Exception):
    pass


class NotFound(Exception):
    pass


class Conflict(Exception):
    """Someone else saved this item after the version the editor started from."""

    def __init__(self, item, last_change, reason="changed"):
        super().__init__(reason)
        self.item, self.last_change, self.reason = item, last_change, reason


def _text(value, limit, field):
    text = " ".join(str(value if value is not None else "").split())
    if not text:
        raise Invalid(f"{field} can't be empty.")
    if len(text) > limit:
        raise Invalid(f"{field} is too long (at most {limit} characters).")
    return text


def validate(kind, changes):
    """Clean, checked values for the fields being changed. Raises Invalid."""
    if kind not in EDITABLE:
        raise Invalid("Unknown item kind.")
    if not isinstance(changes, dict) or not changes:
        raise Invalid("Nothing to change.")
    out = {}
    for field, value in changes.items():
        if field not in EDITABLE[kind]:
            raise Invalid(f"{field} can't be edited here.")
        if field == "text":
            out[field] = _text(value, TEXT_LIMIT[kind], "Text")
        elif field == "title":
            out[field] = _text(value, TITLE_LIMIT, "Title")
        elif field == "owner":
            owner = " ".join(str(value or "").split())
            if len(owner) > OWNER_LIMIT:
                raise Invalid("Owner name is too long.")
            out[field] = owner
        elif field == "due":
            due = str(value or "").strip()
            if due:
                try:
                    due = date.fromisoformat(due).isoformat()
                except ValueError:
                    raise Invalid("Due date must look like 2026-11-21.") from None
            out[field] = due
        elif field == "status":
            if value not in STATUSES[kind]:
                raise Invalid(f"Status must be one of: {', '.join(STATUSES[kind])}.")
            out[field] = value
    return out


def _read(path):
    rows = []
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass  # a half-written last line (power cut) is skipped, never "fixed" in place
    return rows


class EditLog:
    def __init__(self, sid):
        self.sid = sid
        self.path = settings.SESSIONS_DIR / sid / "edits.jsonl"
        self.entries = _read(self.path)
        self.lock = None

    # ---------- state ----------
    def state(self, summary, actions, title):
        """Replay the log over the generated items. Returns {"summary": [...], "actions": [...],
        "meeting": {...}, "minutes": {...}, "by_id": {...}}; deleted items are only in by_id."""
        by_id, order = {}, []

        def add(item, kind, origin):
            it = {**item, "kind": kind, "version": int(item.get("version") or 1), "origin": origin,
                  "edited_by": None, "edited_ms": None, "last_change": None, "deleted": False}
            if kind == "summary":
                it.setdefault("status", "draft")
                it.setdefault("type", "point")
            else:
                it.setdefault("status", "pending")
                it.setdefault("owner", "")
                it.setdefault("due", "")
            it.setdefault("source_line_ids", [])
            by_id[it["id"]] = it
            order.append(it["id"])

        for item in summary:
            add(item, "summary", "generated")
        for item in actions:
            add(item, "action", "generated")
        by_id["meeting"] = {"id": "meeting", "kind": "meeting", "title": title, "version": 1,
                            "edited_by": None, "edited_ms": None, "last_change": None}
        by_id["minutes"] = {"id": "minutes", "kind": "minutes", "approved": None, "version": 1,
                            "edited_by": None, "edited_ms": None, "last_change": None}

        last_item_edit_ms = 0
        for e in self.entries:
            if e.get("op") == "create":
                if e["item"] not in by_id:
                    add({**(e.get("after") or {}), "id": e["item"]}, e["kind"], "added")
                it = by_id[e["item"]]
                it["created_by"] = e.get("who")
            else:
                it = by_id.get(e.get("item"))
                if it is None:
                    continue  # its generated item is gone; the lines stay in the log
                field = e.get("field")
                if field:
                    it[field] = e.get("after")
                if field == "status":
                    approved = e.get("after") == "approved"
                    it["approved_by"] = e.get("who") if approved else None
                    it["approved_ms"] = e.get("ms") if approved else None
            it["version"] = int(e.get("version") or it["version"])
            it["edited_by"], it["edited_ms"], it["last_change"] = e.get("who"), e.get("ms"), e.get("change")
            if e.get("kind") in ("summary", "action", "meeting"):
                last_item_edit_ms = max(last_item_edit_ms, int(e.get("ms") or 0))

        minutes = by_id["minutes"]
        approved = minutes.get("approved")
        minutes["changed_since"] = bool(approved and last_item_edit_ms > int(approved.get("ms") or 0))
        live = [by_id[i] for i in order if not by_id[i]["deleted"]]
        return {"summary": [i for i in live if i["kind"] == "summary"],
                "actions": [i for i in live if i["kind"] == "action"],
                "meeting": by_id["meeting"], "minutes": minutes, "by_id": by_id}

    # ---------- writing ----------
    def _lock(self):
        if self.lock is None:
            self.lock = asyncio.Lock()
        return self.lock

    def _append(self, rows):
        if not self.path.parent.is_dir():
            raise NotFound("This meeting was deleted.")
        with open(self.path, "a") as f:
            f.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
            f.flush()
            os.fsync(f.fileno())
        self.entries.extend(rows)
        return rows

    def _rows(self, who, role, op, kind, item, fields, version, undo_of=None):
        change = "c" + secrets.token_hex(5)
        ms = now_ms()
        rows = []
        for field, before, after in fields:
            row = {"seq": len(self.entries) + len(rows) + 1, "ms": ms, "who": who, "role": role, "change": change,
                   "op": op, "kind": kind, "item": item, "field": field, "before": before, "after": after,
                   "version": version}
            if undo_of:
                row["undo_of"] = undo_of
            rows.append(row)
        return rows

    def _last_change(self, item_id):
        rows = [e for e in self.entries if e.get("item") == item_id]
        if not rows:
            return None
        last = rows[-1]
        return {"change": last["change"], "who": last["who"], "ms": last["ms"],
                "fields": [e["field"] for e in rows if e["change"] == last["change"]]}

    def _current(self, get_state, item_id, base_version):
        it = get_state()["by_id"].get(item_id)
        if it is None:
            raise NotFound("No such item.")
        if base_version is not None and int(base_version) != it["version"]:
            raise Conflict(it, self._last_change(item_id))
        return it

    async def update(self, get_state, who, role, item_id, base_version, changes):
        async with self._lock():
            it = self._current(get_state, item_id, base_version)
            if it.get("deleted"):
                raise Invalid("This item was deleted.")
            clean = validate(it["kind"], changes)
            fields = [(f, it.get(f), v) for f, v in clean.items() if it.get(f) != v]
            if not fields:
                return []
            op = "approve" if clean.get("status") == "approved" and len(fields) == 1 else "update"
            return self._append(self._rows(who, role, op, it["kind"], item_id, fields, it["version"] + 1))

    async def create(self, get_state, who, role, kind, fields):
        if kind != "action":
            raise Invalid("Only action items can be added.")
        async with self._lock():
            clean = validate(kind, {"owner": "", "due": "", "status": "pending", **fields})
            item_id = "u" + secrets.token_hex(4)
            after = {**clean, "source_line_ids": []}
            return self._append(self._rows(who, role, "create", kind, item_id, [(None, None, after)], 1))

    async def delete(self, get_state, who, role, item_id, base_version):
        async with self._lock():
            it = self._current(get_state, item_id, base_version)
            if it["kind"] not in ("summary", "action") or it.get("deleted"):
                raise Invalid("This item can't be deleted.")
            return self._append(self._rows(who, role, "delete", it["kind"], item_id, [("deleted", False, True)],
                                           it["version"] + 1))

    async def undo(self, get_state, who, role, item_id, change):
        async with self._lock():
            it = get_state()["by_id"].get(item_id)
            rows = [e for e in self.entries if e.get("item") == item_id and e.get("change") == change]
            if it is None or not rows:
                raise NotFound("No such change.")
            if it.get("last_change") != change:
                raise Conflict(it, self._last_change(item_id), reason="newer")
            if rows[0]["op"] == "create":
                fields = [("deleted", it.get("deleted", False), True)]
            else:
                fields = [(e["field"], it.get(e["field"]), e["before"]) for e in rows if e.get("field")]
            return self._append(self._rows(who, role, "undo", it["kind"], item_id, fields, it["version"] + 1,
                                           undo_of=change))

    async def approve_minutes(self, get_state, who, role, base_version):
        async with self._lock():
            it = self._current(get_state, "minutes", base_version)
            after = {"by": who, "ms": now_ms()}
            return self._append(self._rows(who, role, "approve", "minutes", "minutes",
                                           [("approved", it.get("approved"), after)], it["version"] + 1))

    # ---------- reading ----------
    def changes(self, item_id=None, who=None):
        """Log lines grouped by change, newest first. Optionally for one item, or one person."""
        groups = {}
        for e in self.entries:
            if item_id and e.get("item") != item_id:
                continue
            if who and e.get("who") != who:
                continue
            g = groups.setdefault(e["change"], {"change": e["change"], "who": e.get("who"), "role": e.get("role"),
                                                "ms": e.get("ms"), "op": e.get("op"), "kind": e.get("kind"),
                                                "item": e.get("item"), "version": e.get("version"),
                                                "undo_of": e.get("undo_of"), "seq": e.get("seq", 0), "fields": []})
            g["fields"].append({"field": e.get("field"), "before": e.get("before"), "after": e.get("after")})
        return sorted(groups.values(), key=lambda g: (-(g["ms"] or 0), -(g["seq"] or 0)))

    def people(self):
        return sorted({e.get("who") for e in self.entries if e.get("who")})


_LOGS = {}
SID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def get(sid):
    """The edit log of a meeting (cached). A wiped session (someone said no) starts empty again."""
    log = _LOGS.get(sid)
    if log is None or (log.entries and not log.path.exists()):
        log = _LOGS[sid] = EditLog(sid)
    return log


def forget(sid):
    _LOGS.pop(sid, None)
