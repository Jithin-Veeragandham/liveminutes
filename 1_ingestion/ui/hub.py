"""Wakes the open live streams (Server-Sent Events) when something the UI shows changes, and keeps
presence: who is looking at a meeting now, and who is editing which item ("Jithin is editing")."""

import asyncio
import time

PRESENCE_TTL_S = 15   # an "is editing" hint disappears this long after the last ping

_versions = {}        # meeting id -> int
_events = {}          # meeting id -> asyncio.Event, replaced on every touch
_editing = {}         # meeting id -> {name: (item_id, monotonic time)}
_online = {}          # meeting id -> {name: open streams}


def version(sid):
    return _versions.get(sid, 0)


def touch(sid):
    """Something changed for this meeting (an edit, a demo summary, presence): wake its streams."""
    _versions[sid] = _versions.get(sid, 0) + 1
    event = _events.pop(sid, None)
    if event is not None:
        event.set()


async def wait(sid, seen, session=None, session_seen=None, timeout=15.0):
    """Return when touch(sid) happened after version `seen`, the live Session changed after
    `session_seen`, or after `timeout` seconds."""
    if version(sid) != seen or (session is not None and session.version != session_seen):
        return
    event = _events.setdefault(sid, asyncio.Event())
    waiters = [asyncio.ensure_future(event.wait())]
    if session is not None:
        waiters.append(asyncio.ensure_future(session.wait_for_change(session_seen, timeout)))
    try:
        await asyncio.wait(waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for w in waiters:
            w.cancel()


def set_editing(sid, name, item_id):
    people = _editing.setdefault(sid, {})
    before = people.get(name, (None,))[0]
    if item_id:
        people[name] = (str(item_id)[:40], time.monotonic())
    else:
        people.pop(name, None)
    if before != item_id:
        touch(sid)


def join(sid, name):
    if name:
        people = _online.setdefault(sid, {})
        people[name] = people.get(name, 0) + 1
        touch(sid)


def leave(sid, name):
    if name and sid in _online:
        people = _online[sid]
        people[name] = people.get(name, 1) - 1
        if people[name] <= 0:
            people.pop(name)
            _editing.get(sid, {}).pop(name, None)
        touch(sid)


def presence(sid):
    now = time.monotonic()
    editing = {name: item for name, (item, at) in _editing.get(sid, {}).items() if now - at < PRESENCE_TTL_S}
    return {"online": sorted(_online.get(sid, {})), "editing": editing}


def forget(sid):
    for table in (_versions, _editing, _online):
        table.pop(sid, None)
    event = _events.pop(sid, None)
    if event is not None:
        event.set()
