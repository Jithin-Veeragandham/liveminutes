"""The live view in the browser: http://localhost:8776/live

  /live                    home.html, two tabs: Sessions, and Send bot (send a bot + watch its logs)
  /live/<bot_id>           the page (live.html): transcript, live summary + action items (notes.py), who said
                           yes, bot activity, and what was sent to the LLM
  /live/<bot_id>/data      JSON for the page (?after=N: only activity lines from N on; ?v=V: wait until
                           something changes after version V, a long poll, so new lines show at once)
  /live/<bot_id>/logs      JSON the Send bot tab polls: Attendee's record of the bot + bridge activity + Groq uploads
  /live/api/sessions       JSON list of session folders, newest first
  /live/api/send           POST {"link", "name"}: create an Attendee bot for that meeting (like send_bot.sh)
"""

import json
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import web

import settings
from chat import plain


def now_ms():
    return int(time.time() * 1000)

BOT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")         # also stops "../" tricks in file paths
TRANSCRIPT_LINE = re.compile(r"^\[(\d\d:\d\d)\] ([^:]+): (.*)$")
MEETING_HOSTS = {"meet.google.com": "meet", "teams.microsoft.com": "teams", "teams.live.com": "teams"}
WEBHOOK_TRIGGERS = ["bot.state_change", "participant_events.join_leave", "chat_messages.update",
                    "participant_events.speech_start_stop", "transcript.update"]
MAX_LOG_LINES = 500
LONG_POLL_S = 20  # /data?v=N waits up to this long for a change


def activity_line(event, f):
    """(kind, text) for the activity column, from a consent event and its fields."""
    if event == "bot_chat":
        return "bot", plain(f.get("text"))
    if event == "reply":
        text = f"{f.get('name') or '?'}: {f.get('text')}"
        if f.get("decision"):
            text += f"   → {f['decision']}"
        if f.get("on_behalf_of"):
            text += f" for {f['on_behalf_of']}"
        return "chat", text
    if event in ("joined", "left"):
        return "people", f"{f.get('name')} {event}"
    if event == "asked":
        return "consent", "asked for consent: " + ", ".join(f.get("people") or [])
    if event == "paused":
        return "consent", f"recording paused ({f.get('reason')})"
    if event == "resumed":
        return "consent", "recording on: everyone said yes"
    if event == "vouched":
        return "consent", f"{f.get('by')} answered {f.get('decision')} for {f.get('name')}"
    if event == "declined":
        return "consent", f"{f.get('name')} said no: recording stopped, session deleted"
    if event == "timeout_leave":
        return "consent", "no answer from " + ", ".join(f.get("waiting") or []) + ": leaving"
    return "consent", event.replace("_", " ")


def notes_view(ln, s):
    """The notes panel for a running session: summary, action items, when the next update runs, rate-limit use,
    and the log of what was sent to the LLM. ln: the bridge's notes.LiveNotes."""
    now = now_ms()
    view = {"enabled": ln.enabled, "reason": ln.reason, "interval_s": settings.NOTES_INTERVAL_S, "now_ms": now,
            "rate": {"minute": ln.calls_since(now - 60_000), "minute_limit": settings.NOTES_RPM_LIMIT,
                     "day": ln.calls_since(now - 86_400_000), "day_limit": settings.NOTES_RPD_LIMIT},
            "tokens": dict(ln.tokens)}
    if not ln.enabled:
        return view
    view["model"] = ln.model
    st = s.notes
    if st is None or s.declined:
        return {**view, "waiting": "declined" if s.declined else "first audio"}
    view.update(summary=st.notes.summary if st.notes else "",
                action_items=[vars(a) for a in st.notes.action_items] if st.notes else [],
                updates=st.part, next_due_ms=st.next_due_ms, busy=st.busy, error=st.error,
                updated_ms=st.updated_ms, history=st.history[-50:])
    return view


def saved_notes(bot_id):
    """The notes panel for a session that is only on disk (notes.json)."""
    path = settings.SESSIONS_DIR / bot_id / "notes.json"
    if not path.exists():
        return {"enabled": False, "reason": "no notes saved for this session"}
    try:
        d = json.loads(path.read_text())
    except ValueError:
        return {"enabled": False, "reason": "notes.json is unreadable"}
    return {"enabled": True, "saved": True, "model": d.get("model"), "interval_s": d.get("interval_s"),
            "summary": d.get("summary", ""), "action_items": d.get("action_items", []),
            "updated_ms": d.get("updated_ms"), "history": d.get("history", [])[-50:],
            "updates": sum(1 for h in d.get("history", []) if h.get("status") == "ok")}


def live_data(s, after, ln=None):
    """Page data for a session the bridge is running now."""
    return {
        "notes": notes_view(ln, s) if ln else None,
        "bot_id": s.bot_id, "bot_name": s.bot_name, "state": s.state, "live": True,
        "recording": s.recording, "declined": s.declined, "consent": s.consent_required,
        "people": [{"name": p.name, "present": p.present, "decision": p.decision, "vouched_by": p.vouched_by}
                   for p in s.people.values()],
        "transcript": [{"t": t, "who": who, "text": text} for t, who, text in s.merged()] if s.t0_ms is not None else [],
        "activity": s.activity[after:], "next": len(s.activity), "version": s.version,
    }


def read_jsonl(path):
    """Rows of a .jsonl file; a missing file or a half-written last line is skipped."""
    rows = []
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    return rows


def saved_activity(bot_id):
    """Activity lines rebuilt from consent.jsonl, for a session that is only on disk."""
    out = []
    for e in read_jsonl(settings.SESSIONS_DIR / bot_id / "consent.jsonl"):
        kind, text = activity_line(e.pop("event", ""), e)
        out.append({"time": e.get("time", "")[11:19], "ms": e.get("ms"), "kind": kind, "text": text})  # HH:MM:SS
    return out


def saved_data(bot_id, after):
    """Page data for a session that is only on disk (finished, or the bridge restarted)."""
    folder = settings.SESSIONS_DIR / bot_id
    transcript = []
    if (folder / "transcript.txt").exists():
        for line in (folder / "transcript.txt").read_text().splitlines():
            m = TRANSCRIPT_LINE.match(line)
            if m:
                transcript.append({"t": m[1], "who": m[2], "text": m[3]})
    activity = saved_activity(bot_id)
    return {"notes": saved_notes(bot_id),
            "bot_id": bot_id, "bot_name": None, "state": "saved" if folder.exists() else "not found",
            "live": False, "recording": False, "declined": False, "consent": True, "people": [],
            "transcript": transcript, "activity": activity[after:], "next": len(activity)}


# ================= Send bot tab =================

def meeting_link(text):
    """(link, None) for a Google Meet or Teams link, else (None, reason). Adds https:// if missing."""
    link = (text or "").strip()
    if not link:
        return None, "Paste a Google Meet or Teams meeting link."
    if "://" not in link:
        link = "https://" + link
    parts = urlsplit(link)
    if parts.scheme != "https" or (parts.hostname or "").lower() not in MEETING_HOSTS or len(parts.path) < 2:
        return None, "That is not a Google Meet or Teams meeting link (https://meet.google.com/... or https://teams...)."
    if any(c.isspace() for c in link) or len(link) > 2000:
        return None, "The link has spaces in it or is too long."
    return link, None


def bot_name(text):
    return " ".join((text or "").split())[:60] or "LiveMinutes"


def bot_payload(link, name, bridge_host):
    """Body for Attendee's POST /api/v1/bots. The same request as send_bot.sh: keep the two in step.
    Meet gives one audio stream per person. Teams gives one mixed stream, so the bridge also
    needs "who is speaking" events to put names on it."""
    teams = MEETING_HOSTS.get((urlsplit(link).hostname or "").lower()) == "teams"
    audio = "audio" if teams else "per_participant_audio"
    return {
        "meeting_url": link,
        "bot_name": name,
        "metadata": {"bot_name": name},
        "recording_settings": {"format": "none", "record_chat_messages_when_paused": True,
                               "record_participant_speech_start_stop_events": teams},
        "websocket_settings": {audio: {"url": f"wss://{bridge_host}:{settings.WS_PORT}/attendee-websocket"}},
        "webhooks": [{"url": f"http://{bridge_host}:{settings.WEBHOOK_PORT}/webhook", "triggers": WEBHOOK_TRIGGERS}],
    }


def iso_ms(text):
    try:
        return int(datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def words(code):
    return str(code or "").replace("_", " ")


def bot_logs(bot_id, s, attendee_status, attendee_body):
    """Everything known about one bot, as one time-sorted log:
      attendee  Attendee's own record of the bot (joining, waiting room, fatal errors and why)
      bridge    what the bridge saw: state webhooks, people, consent, chat, Groq errors
      groq      one line per upload to Groq (pieces.jsonl)"""
    log = []
    if attendee_status == 200 and isinstance(attendee_body, dict):
        attendee = {k: attendee_body.get(k) for k in ("state", "recording_state", "transcription_state", "meeting_url")}
        for e in attendee_body.get("events") or []:
            text = words(e.get("type")) + (f" ({words(e['sub_type'])})" if e.get("sub_type") else "")
            bad = "fatal" in text or "could not" in text or "error" in text
            log.append({"ms": iso_ms(e.get("created_at")), "source": "attendee", "kind": "error" if bad else "state",
                        "text": text})
    else:
        why = "not reachable" if attendee_status == 0 else f"answered HTTP {attendee_status}"
        attendee = {"error": f"Attendee {why}: {str(attendee_body)[:300]}"}

    for a in (s.activity if s else saved_activity(bot_id)):
        log.append({"ms": a.get("ms"), "source": "bridge", "kind": a["kind"], "text": a["text"]})

    for p in read_jsonl(settings.SESSIONS_DIR / bot_id / "pieces.jsonl"):
        text = (f"piece {p.get('n')} · {p.get('speaker')} · {p.get('audio_s')} s audio · "
                f"Groq {p.get('status') or 'network error'} in {p.get('groq_ms')} ms · "
                f"{p.get('delay_after_speech_end_s')} s after speech end")
        if p.get("text"):
            text += f' · "{p["text"][:100]}"'
        log.append({"ms": p.get("done_ms"), "source": "groq", "kind": "upload" if p.get("status") == 200 else "error",
                    "text": text})

    log.sort(key=lambda e: e["ms"] or 0)
    return {
        "bot_id": bot_id,
        "attendee": attendee,
        "bridge": ({"session": "live", "state": s.state, "recording": s.recording, "declined": s.declined} if s else
                   {"session": "saved" if (settings.SESSIONS_DIR / bot_id).exists() else "none"}),
        "log": log[-MAX_LOG_LINES:],
        "hidden": max(0, len(log) - MAX_LOG_LINES),
    }


def allowed_to_send(request):
    """Only the live view itself may create bots. A web page on another site could otherwise make
    your browser POST here and send a bot with your Attendee key. The Host check also stops DNS
    rebinding; the JSON-only rule makes cross-site browsers ask first (and we never say yes)."""
    allowed = {"localhost", "127.0.0.1", "[::1]", urlsplit(settings.LIVE_VIEW_URL).hostname}
    host = request.host or ""
    hostname = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
    origin = request.headers.get("Origin")
    return (hostname in allowed and request.content_type == "application/json"
            and (origin is None or urlsplit(origin).netloc == host))


def checked_bot_id(request):
    bot_id = request.match_info["bot_id"]
    if not BOT_ID.match(bot_id):
        raise web.HTTPNotFound()
    return bot_id


def add_routes(app, bridge):
    """bridge: the running Bridge. bridge.sessions is {bot_id: Session} for the meetings running now."""
    sessions = bridge.sessions
    here = Path(__file__).parent

    async def home(request):
        return web.FileResponse(here / "home.html")

    async def session_list(request):
        folders = sorted((d for d in settings.SESSIONS_DIR.glob("*") if d.is_dir()), key=lambda d: -d.stat().st_mtime)
        return web.json_response([
            {"bot_id": d.name, "changed_ms": int(d.stat().st_mtime * 1000),
             "live": d.name in sessions and sessions[d.name].state != "ended",
             "has_transcript": (d / "transcript.txt").exists()}
            for d in folders[:50] if BOT_ID.match(d.name)])

    async def send(request):
        if not allowed_to_send(request):
            return web.json_response({"error": "Bots can only be sent from the live view page."}, status=403)
        try:
            body = await request.json()
        except ValueError:
            return web.json_response({"error": "Bad request."}, status=400)
        link, problem = meeting_link(body.get("link") if isinstance(body, dict) else None)
        if problem:
            return web.json_response({"error": problem}, status=400)
        if not settings.ATTENDEE_API_KEY:
            return web.json_response({"error": "ATTENDEE_API_KEY is missing in the repo's .env "
                                               "(./make_account.sh <email> creates one)."}, status=500)
        name = bot_name(body.get("name"))
        status, out = await bridge.attendee_request("POST", "bots", bot_payload(link, name, settings.BRIDGE_HOST))
        if status != 201 or not isinstance(out, dict) or not out.get("id"):
            why = "is not reachable" if status == 0 else f"answered HTTP {status}"
            if status in (401, 403):
                why += ": ATTENDEE_API_KEY in .env is wrong or stale (run ./make_account.sh again)"
            return web.json_response({"error": f"Attendee {why}.", "detail": out}, status=502)
        return web.json_response({"bot_id": out["id"], "state": out.get("state"), "name": name, "link": link})

    async def page(request):
        checked_bot_id(request)
        return web.FileResponse(here / "live.html")

    async def data(request):
        """With ?v=<version from the last answer>, this is a long poll: it answers as soon as
        something changes (a transcript line, consent, chat...) or after LONG_POLL_S, so a new
        line reaches the page right away instead of at the next 1 s poll."""
        bot_id = checked_bot_id(request)
        try:
            after = int(request.query.get("after", 0))
            version = int(request.query["v"]) if "v" in request.query else None
        except ValueError:
            raise web.HTTPBadRequest()
        s = sessions.get(bot_id)
        if s and version is not None:
            await s.wait_for_change(version, LONG_POLL_S)
        return web.json_response(live_data(s, after, bridge.notes) if s else saved_data(bot_id, after))

    async def logs(request):
        bot_id = checked_bot_id(request)
        status, body = await bridge.attendee_request("GET", f"bots/{bot_id}")
        return web.json_response(bot_logs(bot_id, sessions.get(bot_id), status, body))

    async def root(request):
        raise web.HTTPFound("/live")

    app.router.add_get("/", root)
    app.router.add_get("/live", home)
    app.router.add_get("/live/api/sessions", session_list)
    app.router.add_post("/live/api/send", send)
    app.router.add_get("/live/{bot_id}", page)
    app.router.add_get("/live/{bot_id}/data", data)
    app.router.add_get("/live/{bot_id}/logs", logs)
