"""The participants' shared live view (/m/<id>), editing with an append-only edit log, the redesigned
organizer pages, and Demo mode. See docs/UI.md and docs/UI_CONTRACT.md.

bridge.py calls only these three:
  setup(app, bridge)   add the routes and the request guard
  share_message(s)     once per meeting, after everyone said yes: the chat line with the link + access code
  meeting_ended(s)     the bot left: the access code expires VIEW_ACCESS_DAYS from now
"""

import settings
from ui import access
from ui.meeting import write_meta


def setup(app, bridge):
    from ui import routes
    routes.setup(app, bridge)


def view_url(sid):
    """(participants' link, True if it is a public URL). Without PUBLIC_BASE_URL: localhost (organizer only)."""
    base = access.public_base_url()
    return f"{(base or settings.LIVE_VIEW_URL or 'http://localhost:8776').rstrip('/')}/m/{sid}", bool(base)


def share_message(s):
    """The one chat message with the link and a new access code. None if it was posted before."""
    if s.declined or access.read_record(s.bot_id) is not None:
        return None
    code = access.create_code(s.bot_id)
    write_meta(s.bot_id, platform="teams" if s.is_teams() else "meet")
    url, _ = view_url(s.bot_id)
    days = settings.VIEW_ACCESS_DAYS
    return (f"Live notes for everyone: {url} (access code {access.pretty(code)}). "
            f"Open it, enter the code and pick your name. It stops working {days:g} days after the meeting.")


def meeting_ended(s):
    if s.declined:
        return
    access.mark_ended(s.bot_id)
    write_meta(s.bot_id, ended_ms=access.now_ms(), t0_ms=s.t0_ms)
