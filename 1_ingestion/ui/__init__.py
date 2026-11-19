"""The participants' shared live view (/m/<id>), editing with an append-only edit log, the redesigned
organizer pages, and Demo mode. See docs/UI.md and docs/UI_CONTRACT.md.

bridge.py calls only these:
  setup(app, bridge)   add the routes and the request guard
  recording_allowed(s) each time everyone said yes: (first time?, the chat line with the link + access code)
  link_not_posted(s)   Attendee refused that chat message
  meeting_ended(s)     the bot left: the access code expires VIEW_ACCESS_DAYS from now
  meeting_deleted(s)   someone said no and the session was wiped: the knowledge base must forget it

For teammates' code (docs/INTEGRATION.md):
  notify(meeting_id)          push a change to everyone viewing the meeting (e.g. a new summary)
  approved_minutes(meeting_id) / all_approved_minutes()   the approved records, for (re)indexing
"""

import settings
from ui import access
from ui.meeting import read_meta, write_meta


def notify(meeting_id):
    """Something the meeting page shows changed (e.g. the summarizer has new items): update open pages now."""
    from ui import hub
    hub.touch(meeting_id)


def approved_minutes(meeting_id):
    """The meeting's approved minutes record in effect now, or None (never approved, approval undone, deleted)."""
    from ui import editlog
    if not (settings.SESSIONS_DIR / meeting_id).is_dir():
        return None
    return editlog.get(meeting_id).approved_record()


def all_approved_minutes():
    """Every approved record on this computer, e.g. to rebuild the knowledge base's index."""
    from live_view import BOT_ID
    out = []
    for folder in sorted(settings.SESSIONS_DIR.glob("*")):
        if folder.is_dir() and BOT_ID.match(folder.name) and (folder / "edits.jsonl").exists():
            record = approved_minutes(folder.name)
            if record:
                out.append(record)
    return out


def meeting_deleted(s):
    """Called by bridge.decline() after the session folder is wiped: tell the knowledge base to forget it."""
    import asyncio
    from ui import editlog, routes
    editlog.forget(s.bot_id)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    routes.fire_hook("minutes_unapproved", s.bot_id)


def setup(app, bridge):
    from ui import routes
    routes.setup(app, bridge)


def view_url(sid):
    """(participants' link, True if it is a public URL). Without PUBLIC_BASE_URL: localhost (organizer only)."""
    base = access.public_base_url()
    return f"{(base or settings.LIVE_VIEW_URL or 'http://localhost:8776').rstrip('/')}/m/{sid}", bool(base)


def link_text(url, code):
    """The chat text with the participants' link and access code. One line: Teams types it key by key."""
    return (f"Live notes for everyone: {url} (access code {access.pretty(code)}). "
            f"Open it, enter the code and pick your name. It stops working {settings.VIEW_ACCESS_DAYS:g} days "
            "after the meeting.")


def recording_allowed(s, share=True):
    """bridge.evaluate() calls this every time everyone present has said yes and recording opens.
    Returns (first, link):
      first  True only the first time in this meeting. Kept in meta.json, so a bridge restart doesn't repeat it.
      link   the chat text with the participants' link and access code, or None. Given once, the first time,
             and only with a public address (tunnel.sh): a localhost link is no use to participants. Without
             one, nothing is posted, the organizer view shows a warning, and Share > "Make a new code" posts
             the link once the tunnel runs. Demo meetings post the local link: they run on this computer.
      share  False when the bridge has no live view (LIVE_VIEW_URL=""): only `first` is wanted."""
    if s.declined or getattr(s, "ui_allowed_ms", None) or read_meta(s.bot_id).get("allowed_ms"):
        return False, None
    s.ui_allowed_ms = access.now_ms()
    if not share:
        write_meta(s.bot_id, allowed_ms=s.ui_allowed_ms)
        return True, None
    url, public = view_url(s.bot_id)
    # Reuse a code the organizer already made (Share > Make a new code before everyone agreed), else make one.
    code = access.CODES.get(s.bot_id) if access.read_record(s.bot_id) else None
    code = code or access.create_code(s.bot_id)
    post = public or s.demo
    write_meta(s.bot_id, allowed_ms=s.ui_allowed_ms, platform="teams" if s.is_teams() else "meet",
               chat_link="posted" if post else "no_public_url")
    return True, (link_text(url, code) if post else None)


def link_not_posted(s):
    """Attendee refused the chat message with the link: the organizer view says so (Share > Make a new code)."""
    write_meta(s.bot_id, chat_link="failed")


def meeting_ended(s):
    if s.declined:
        return
    access.mark_ended(s.bot_id)
    write_meta(s.bot_id, ended_ms=access.now_ms(), t0_ms=s.t0_ms)
