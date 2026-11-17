"""HTTP routes for the UI (all on the bridge's port 8776).

Pages
  /m/<id>                participant view: access code, then pick your name, then the meeting page
  /live, /live/<id>      organizer home and meeting page (new design; old pages: add ?classic=1)
API (same handlers; participant = cookie + CSRF token, organizer = this computer + organizer token)
  /m/<id>/api/<action>   me, code, name, forget-name, snapshot, stream (SSE), edit, create, delete, undo,
  /live/<id>/api/<action>  approve, presence, ask, history, export.md; organizer only: editlog, rotate-code
  /live/api/demo  /live/api/meetings  /live/api/public-url  /live/api/ui-config   (organizer only)
Guard (middleware)
  Requests through a tunnel or by a foreign host name may only reach /m/... and /static/... (404 otherwise).
  Every write must come from our own page (Origin), and carry the CSRF token. HTML pages get a strict CSP.
"""

import asyncio
import json
import re
import secrets
from pathlib import Path

from aiohttp import web

import settings
from live_view import BOT_ID
from ui import access, adapters, demo, editlog, export, hub, view_url
from ui.meeting import Meeting

HERE = Path(__file__).parent
STATIC, TEMPLATES = HERE / "static", HERE / "templates"
PUBLIC_PATHS = re.compile(r"^/(m/[A-Za-z0-9_-]{1,64}(/[A-Za-z0-9._-]*)*|static/[A-Za-z0-9._/-]+|favicon\.ico)$")
ORGANIZER_PAGE = re.compile(r"^/live/([A-Za-z0-9_-]{1,64})$")
WRITES = {"POST", "PUT", "PATCH", "DELETE"}
STREAM_HEARTBEAT_S = 15
SECURITY_HEADERS = {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "same-origin",
                    "X-Frame-Options": "DENY", "Permissions-Policy": "camera=(), microphone=(), geolocation=()"}


def fail(status, state, message, **extra):
    """An error the page can show: {"state", "error", ...} with an HTTP status."""
    body = json.dumps({"state": state, "error": message, **extra})
    exc = {400: web.HTTPBadRequest, 401: web.HTTPUnauthorized, 403: web.HTTPForbidden, 404: web.HTTPNotFound,
           409: web.HTTPConflict, 410: web.HTTPGone, 429: web.HTTPTooManyRequests}[status]
    headers = {"Retry-After": str(extra["retry_after"])} if "retry_after" in extra else None
    return exc(text=body, content_type="application/json", headers=headers)


class Viewer:
    def __init__(self, role, name, payload=None):
        self.role, self.name, self.payload = role, name, payload

    @property
    def can_edit(self):
        return bool(self.name)

    @property
    def key(self):
        return f"{self.role}:{self.name}:{(self.payload or {}).get('k', '')}"

    def csrf(self):
        return access.ORGANIZER_TOKEN if self.role == "organizer" else access.csrf_for(self.payload or {})

    def info(self):
        return {"role": self.role, "name": self.name, "can_edit": self.can_edit, "csrf": self.csrf()}


# ================= pages =================

def page(template, title, entry, config, body_class=""):
    nonce = secrets.token_urlsafe(16)
    cfg = json.dumps(config).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    html = (TEMPLATES / template).read_text()
    for key, value in {"NONCE": nonce, "TITLE": title, "ENTRY": entry, "CONFIG": cfg, "BODY_CLASS": body_class}.items():
        html = html.replace("{{" + key + "}}", value)
    csp = (f"default-src 'self'; script-src 'self' 'nonce-{nonce}'; style-src 'self'; img-src 'self' data: blob:; "
           "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
           "form-action 'self'")
    return web.Response(text=html, content_type="text/html",
                        headers={"Content-Security-Policy": csp, "Cache-Control": "no-store", **SECURITY_HEADERS})


def setup(app, bridge):
    sessions = bridge.sessions

    # ---------- helpers ----------
    def meeting(request):
        sid = request.match_info["sid"]
        if not BOT_ID.match(sid):
            raise fail(404, "not_found", "No such meeting.")
        return Meeting(bridge, sid)

    def participant(request, m):
        """The viewer behind the cookie, after checking the meeting may still be opened."""
        if not m.exists:
            raise fail(404, "not_found", "This meeting doesn't exist (or was deleted).")
        if m.declined:
            raise fail(410, "declined", "Recording was stopped at a participant's request, and the notes were deleted.")
        record = access.read_record(m.sid)
        if record is None:
            raise fail(403, "not_shared", "This meeting isn't shared yet: the link and code are posted in the meeting "
                                          "chat once everyone has agreed to recording.")
        expires = access.expires_ms(m.sid, m.live)
        if expires is not None and access.now_ms() > expires:
            raise fail(410, "expired", "These live notes have expired.")
        payload = access.read_cookie(m.sid, request.cookies.get(access.cookie_name(m.sid), ""))
        if not payload or payload.get("g") != record.get("generation"):
            raise fail(401, "need_code", "Enter the access code from the meeting chat.")
        name = payload.get("n")
        return Viewer("participant", name if name in m.consented() else None, payload)

    def organizer(request, m):
        if not m.exists:
            raise fail(404, "not_found", "No such meeting.")
        name = " ".join(request.headers.get("X-LM-Name", "").split())[:80] or "Organizer"
        return Viewer("organizer", name if name in set(m.consented()) | {"Organizer"} else "Organizer")

    def viewer_for(request, m):
        return organizer(request, m) if request.path.startswith("/live/") else participant(request, m)

    def editable(m, v, request):
        if not v.can_edit:
            raise fail(403, "need_name", "Pick your name to edit.")
        if request.headers.get("X-CSRF-Token") != v.csrf():
            raise fail(403, "csrf", "Your session is out of date. Reload the page.")
        if m.declined:
            raise fail(410, "declined", "This meeting was deleted.")
        wait = access.EDITS.hit((m.sid, v.key))
        if wait:
            raise fail(429, "rate_limited", "You're saving very fast. Try again in a moment.", retry_after=wait)

    async def body_of(request, limit=20_000):
        if request.content_type != "application/json":
            raise fail(400, "bad_request", "Send JSON.")
        if (request.content_length or 0) > limit:
            raise fail(400, "bad_request", "Too big.")
        try:
            body = await request.json()
        except ValueError:
            raise fail(400, "bad_request", "Bad JSON.") from None
        if not isinstance(body, dict):
            raise fail(400, "bad_request", "Bad JSON.")
        return body

    def state_of(m):
        summary, actions = adapters.summary_source(m), adapters.actions_source(m)
        st = editlog.get(m.sid).state(summary["items"], actions["items"], m.default_title())
        return summary, actions, st

    def snapshot(m, v):
        summary, actions, st = state_of(m)
        status, waiting = m.status()
        record = access.read_record(m.sid)
        url, public = view_url(m.sid)
        code = access.CODES.get(m.sid)
        log = editlog.get(m.sid)
        run = demo.RUNS.get(m.sid)
        snap = {
            "session": {
                "id": m.sid, "title": st["meeting"]["title"], "title_version": st["meeting"]["version"],
                "platform": m.platform, "demo": m.demo, "status": status, "waiting": waiting,
                "started_ms": m.started_ms(), "ended_ms": m.ended_ms(), "server_ms": access.now_ms(),
                "expires_ms": access.expires_ms(m.sid, m.live) if record else None,
                "shared": record is not None, "minutes": st["minutes"],
                "share": {"url": url, "public": public, "code": access.pretty(code) if code else None},
                "dropped_lines": run.dropped if run else 0,
            },
            "people": m.people(),
            "lines": m.lines(),
            "summary": {"items": st["summary"], "sample": summary["sample"], "label": summary["label"],
                        "note": summary["note"]},
            "actions": {"items": st["actions"], "sample": actions["sample"], "label": actions["label"],
                        "note": actions["note"]},
            "presence": hub.presence(m.sid),
            "me": v.info(),
            "log": {"changes": len({e["change"] for e in log.entries}), "people": log.people()},
            "ask": {"suggestions": adapters.ask_suggestions(), "note": adapters.SAMPLE_ASK_NOTE},
        }
        if v.role == "organizer":
            activity = m.activity()
            snap["activity"] = {"items": activity[-300:], "next": len(activity)}
        return snap

    def edited(m, v, rows):
        hub.set_editing(m.sid, v.name, None)
        hub.touch(m.sid)
        _, _, st = state_of(m)
        item = st["by_id"].get(rows[0]["item"]) if rows else None
        return web.json_response({"ok": True, "item": item, "change": rows[0]["change"] if rows else None,
                                  "minutes": st["minutes"]})

    async def guarded(coro):
        try:
            return await coro
        except editlog.Conflict as c:
            raise fail(409, "conflict", "Someone changed this while you were editing." if c.reason == "changed"
                       else "This item changed after that edit, so it can't be undone here.",
                       item=c.item, last_change=c.last_change, reason=c.reason) from None
        except editlog.Invalid as e:
            raise fail(400, "invalid", str(e)) from None
        except editlog.NotFound as e:
            raise fail(404, "not_found", str(e)) from None

    # ---------- participant-only steps ----------
    async def me(request):
        m = meeting(request)
        try:
            v = participant(request, m)
        except web.HTTPException as e:
            return web.Response(status=200, text=e.text, content_type="application/json")
        out = {"state": "ok" if v.name else "need_name", **v.info()}
        if not v.name:
            out["people"] = m.consented()
        return web.json_response(out)

    async def enter_code(request):
        m = meeting(request)
        body = await body_of(request, 2000)
        ip = access.client_ip(request)
        wait = access.CODE_PER_IP.hit((m.sid, ip)) or access.CODE_PER_MEETING.hit(m.sid)
        if wait:
            raise fail(429, "rate_limited", f"Too many tries. Wait {max(1, round(wait / 60))} min and try again.",
                       retry_after=wait)
        try:
            participant(request, m)
        except web.HTTPException as e:
            if json.loads(e.text)["state"] != "need_code":
                raise
        if not access.check_code(m.sid, body.get("code")):
            raise fail(401, "wrong_code", "That code isn't right. Check the meeting chat and try again.")
        record = access.read_record(m.sid)
        value, payload = access.make_cookie(m.sid, record["generation"])
        r = web.json_response({"state": "need_name", "people": m.consented(), "csrf": access.csrf_for(payload)})
        set_cookie(r, request, m.sid, value)
        return r

    async def pick_name(request):
        m = meeting(request)
        v = participant(request, m)
        if request.headers.get("X-CSRF-Token") != v.csrf():
            raise fail(403, "csrf", "Your session is out of date. Reload the page.")
        name = (await body_of(request, 2000)).get("name")
        if name not in m.consented():
            raise fail(400, "invalid", "Pick a name from the list of people who agreed to recording.")
        value, payload = access.make_cookie(m.sid, v.payload["g"], name=name, nonce=v.payload["k"])
        r = web.json_response({"state": "ok", **Viewer("participant", name, payload).info()})
        set_cookie(r, request, m.sid, value)
        return r

    async def forget_name(request):
        m = meeting(request)
        v = participant(request, m)
        if request.headers.get("X-CSRF-Token") != v.csrf():
            raise fail(403, "csrf", "Your session is out of date. Reload the page.")
        value, payload = access.make_cookie(m.sid, v.payload["g"], nonce=v.payload["k"])
        r = web.json_response({"state": "need_name", "people": m.consented(), "csrf": access.csrf_for(payload)})
        set_cookie(r, request, m.sid, value)
        return r

    def set_cookie(response, request, sid, value):
        """The cookie outlives the code on purpose: every request checks it against the code's generation
        and expiry, so it stops working when they do."""
        response.set_cookie(access.cookie_name(sid), value, path=f"/m/{sid}", httponly=True, samesite="Lax",
                            secure=access.is_https(request), max_age=int((settings.VIEW_ACCESS_DAYS + 30) * 86400))

    # ---------- shared: reading ----------
    async def get_snapshot(request):
        m = meeting(request)
        return web.json_response(snapshot(m, viewer_for(request, m)))

    async def stream(request):
        m = meeting(request)
        v = viewer_for(request, m)
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream; charset=utf-8",
                                           "Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})
        await resp.prepare(request)

        async def send(event, data):
            await resp.write(f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode())

        snap = snapshot(m, v)
        await resp.write(b"retry: 2000\n\n")
        await send("snapshot", snap)
        parts = ("session", "people", "summary", "actions", "presence", "me", "log")
        last = {p: json.dumps(snap[p], sort_keys=True) for p in parts}
        last_lines = {l["id"]: json.dumps(l, sort_keys=True) for l in snap["lines"]}
        next_activity = snap.get("activity", {}).get("next", 0)
        hub.join(m.sid, v.name)
        try:
            while True:
                s = sessions.get(m.sid)
                await hub.wait(m.sid, hub.version(m.sid), s, s.version if s else None, STREAM_HEARTBEAT_S)
                await asyncio.sleep(0.05)  # let a burst of changes land, then send once
                m = Meeting(bridge, m.sid)
                try:
                    fresh = viewer_for(request, m)
                except web.HTTPException as e:
                    await send("revoked", json.loads(e.text))
                    break
                if fresh.name != v.name:
                    hub.leave(m.sid, v.name)
                    hub.join(m.sid, fresh.name)
                v = fresh
                snap = snapshot(m, v)
                sent = False
                for p in parts:
                    text = json.dumps(snap[p], sort_keys=True)
                    if text != last[p]:
                        last[p] = text
                        await send(p, snap[p])
                        sent = True
                now = {l["id"]: json.dumps(l, sort_keys=True) for l in snap["lines"]}
                upsert = [l for l in snap["lines"] if last_lines.get(l["id"]) != now[l["id"]]]
                remove = [i for i in last_lines if i not in now]
                if upsert or remove:
                    await send("lines", {"upsert": upsert, "remove": remove})
                    last_lines, sent = now, True
                if v.role == "organizer":
                    activity = m.activity(next_activity)
                    if activity:
                        next_activity += len(activity)
                        await send("activity", {"items": activity, "next": next_activity})
                        sent = True
                if not sent:
                    await resp.write(b": ping\n\n")
        except (ConnectionError, asyncio.CancelledError, RuntimeError):
            pass
        finally:
            hub.leave(m.sid, v.name)
        return resp

    async def history(request):
        m = meeting(request)
        viewer_for(request, m)
        item = request.query.get("item", "")[:40]
        return web.json_response({"item": item, "changes": editlog.get(m.sid).changes(item_id=item)})

    async def editlog_all(request):
        m = meeting(request)
        organizer(request, m)
        who = request.query.get("who") or None
        log = editlog.get(m.sid)
        return web.json_response({"changes": log.changes(who=who), "people": log.people()})

    async def export_md(request):
        m = meeting(request)
        v = viewer_for(request, m)
        snap = snapshot(m, v)
        text = export.markdown(snap, request.query.get("tz", 0), access.now_ms())
        slug = re.sub(r"[^a-z0-9]+", "-", snap["session"]["title"].lower()).strip("-")[:40] or "meeting"
        return web.Response(text=text, content_type="text/markdown", charset="utf-8",
                            headers={"Content-Disposition": f'attachment; filename="minutes-{slug}.md"'})

    # ---------- shared: writing ----------
    async def edit(request):
        m = meeting(request)
        v = viewer_for(request, m)
        editable(m, v, request)
        body = await body_of(request)
        log = editlog.get(m.sid)
        rows = await guarded(log.update(lambda: state_of(m)[2], v.name, v.role, str(body.get("item", ""))[:40],
                                        body.get("base_version"), body.get("changes")))
        return edited(m, v, rows)

    async def create(request):
        m = meeting(request)
        v = viewer_for(request, m)
        editable(m, v, request)
        body = await body_of(request)
        rows = await guarded(editlog.get(m.sid).create(lambda: state_of(m)[2], v.name, v.role,
                                                       body.get("kind", "action"), body.get("fields") or {}))
        return edited(m, v, rows)

    async def delete(request):
        m = meeting(request)
        v = viewer_for(request, m)
        editable(m, v, request)
        body = await body_of(request)
        rows = await guarded(editlog.get(m.sid).delete(lambda: state_of(m)[2], v.name, v.role,
                                                       str(body.get("item", ""))[:40], body.get("base_version")))
        return edited(m, v, rows)

    async def undo(request):
        m = meeting(request)
        v = viewer_for(request, m)
        editable(m, v, request)
        body = await body_of(request)
        rows = await guarded(editlog.get(m.sid).undo(lambda: state_of(m)[2], v.name, v.role,
                                                     str(body.get("item", ""))[:40], str(body.get("change", ""))[:40]))
        return edited(m, v, rows)

    async def approve(request):
        m = meeting(request)
        v = viewer_for(request, m)
        editable(m, v, request)
        body = await body_of(request)
        rows = await guarded(editlog.get(m.sid).approve_minutes(lambda: state_of(m)[2], v.name, v.role,
                                                                body.get("base_version")))
        return edited(m, v, rows)

    async def presence(request):
        m = meeting(request)
        v = viewer_for(request, m)
        if not v.can_edit or request.headers.get("X-CSRF-Token") != v.csrf():
            raise fail(403, "need_name", "Pick your name first.")
        if access.PRESENCE.hit((m.sid, v.key)):
            return web.json_response({"ok": False})
        item = (await body_of(request, 1000)).get("item")
        hub.set_editing(m.sid, v.name, str(item)[:40] if item else None)
        return web.json_response({"ok": True})

    async def ask(request):
        m = meeting(request)
        v = viewer_for(request, m)
        if request.headers.get("X-CSRF-Token") != v.csrf():
            raise fail(403, "csrf", "Your session is out of date. Reload the page.")
        wait = access.ASK.hit((m.sid, v.key))
        if wait:
            raise fail(429, "rate_limited", "Too many questions at once. Try again in a moment.", retry_after=wait)
        question = " ".join(str((await body_of(request, 4000)).get("question", "")).split())[:300]
        if not question:
            raise fail(400, "invalid", "Type a question.")
        return web.json_response(adapters.ask_source(question, m))

    # ---------- organizer only ----------
    def organizer_write(request):
        if access.is_browser(request) and request.headers.get("X-CSRF-Token") != access.ORGANIZER_TOKEN:
            raise fail(403, "csrf", "Reload the organizer page.")

    async def rotate_code(request):
        m = meeting(request)
        organizer(request, m)
        organizer_write(request)
        if m.declined or not m.exists:
            raise fail(410, "declined", "This meeting can't be shared.")
        body = await body_of(request, 1000) if request.can_read_body else {}
        code = access.create_code(m.sid)
        url, public = view_url(m.sid)
        posted = False
        s = sessions.get(m.sid)
        if body.get("post") and s is not None and m.live:
            await bridge.say(s, f"New access code for the live notes: {url} (code {access.pretty(code)}).")
            posted = True
        hub.touch(m.sid)
        return web.json_response({"code": access.pretty(code), "url": url, "public": public, "posted": posted})

    async def start_demo(request):
        organizer_write(request)
        try:
            speed = float(request.query.get("speed", 1))
            until = float(request.query["until"]) if "until" in request.query else None
        except ValueError:
            speed, until = 1.0, None
        run = demo.start(bridge, speed, until)
        if request.query.get("wait"):
            for _ in range(int(120 / 0.1)):
                if run.sid in access.CODES or run.done:
                    break
                await asyncio.sleep(0.1)
        code = access.CODES.get(run.sid)
        url, public = view_url(run.sid)
        return web.json_response({"id": run.sid, "speed": run.speed, "organizer_url": f"{request.scheme}://{request.host}/live/{run.sid}",
                                  "participant_url": url, "public": public,
                                  "code": access.pretty(code) if code else None})

    async def meetings(request):
        out = []
        folders = sorted((d for d in settings.SESSIONS_DIR.glob("*") if d.is_dir() and BOT_ID.match(d.name)),
                         key=lambda d: -d.stat().st_mtime)
        for d in folders[:60]:
            m = Meeting(bridge, d.name)
            title = editlog.get(d.name).state([], [], m.default_title())
            status, waiting = m.status()
            out.append({"id": d.name, "title": title["meeting"]["title"], "demo": m.demo, "status": status,
                        "waiting": waiting, "platform": m.platform, "started_ms": m.started_ms(),
                        "changed_ms": m.changed_ms(), "people": m.consented(), "lines": len(m.rows()),
                        "shared": access.read_record(d.name) is not None,
                        "approved": title["minutes"].get("approved"), "edits": len(editlog.get(d.name).entries)})
        return web.json_response(out)

    async def ui_config(request):
        base = access.public_base_url()
        return web.json_response({"public_base_url": base, "fallback": settings.LIVE_VIEW_URL,
                                  "view_access_days": settings.VIEW_ACCESS_DAYS})

    async def public_url(request):
        organizer_write(request)
        try:
            url = access.set_public_base_url((await body_of(request, 2000)).get("url"))
        except ValueError as e:
            raise fail(400, "invalid", str(e)) from None
        return web.json_response({"public_base_url": url})

    # ---------- pages ----------
    async def participant_page(request):
        sid = request.match_info["sid"]
        if not BOT_ID.match(sid):
            raise web.HTTPNotFound()
        return page("app.html", "LiveMinutes", "app",
                    {"role": "participant", "sid": sid, "api": f"/m/{sid}/api"}, "is-participant")

    def organizer_page(sid):
        return page("app.html", "LiveMinutes · organizer", "app",
                    {"role": "organizer", "sid": sid, "api": f"/live/{sid}/api", "csrf": access.ORGANIZER_TOKEN},
                    "is-organizer")

    def home_page():
        return page("app.html", "LiveMinutes", "home", {"role": "organizer", "csrf": access.ORGANIZER_TOKEN},
                    "is-home")

    async def favicon(request):
        return web.FileResponse(STATIC / "img" / "logo.svg", headers={"Content-Type": "image/svg+xml"})

    # ---------- the guard ----------
    @web.middleware
    async def guard(request, handler):
        path = request.path
        if access.is_public(request) and not PUBLIC_PATHS.match(path):
            raise web.HTTPNotFound()
        if request.method in WRITES and not access.same_origin(request):
            return web.json_response({"state": "csrf", "error": "Cross-site request refused."}, status=403)
        if request.method == "GET" and "classic" not in request.query and not access.is_public(request):
            if path == "/live":
                return home_page()
            match = ORGANIZER_PAGE.match(path)
            if match and match[1] != "api":
                return organizer_page(match[1])
        response = await handler(request)
        if path.startswith(("/m/", "/static/", "/live/")) and not response.prepared:
            for k, v in SECURITY_HEADERS.items():
                response.headers.setdefault(k, v)
            if path.startswith("/static/"):
                response.headers["Cache-Control"] = "no-cache"
        return response

    app.middlewares.append(guard)
    r = app.router
    r.add_get("/favicon.ico", favicon)
    r.add_static("/static/", STATIC, append_version=False)
    r.add_get("/m/{sid}", participant_page)
    r.add_get("/m/{sid}/", participant_page)
    for prefix in ("/m/{sid}/api", "/live/{sid}/api"):
        r.add_get(prefix + "/snapshot", get_snapshot)
        r.add_get(prefix + "/stream", stream)
        r.add_get(prefix + "/history", history)
        r.add_get(prefix + "/export.md", export_md)
        r.add_post(prefix + "/edit", edit)
        r.add_post(prefix + "/create", create)
        r.add_post(prefix + "/delete", delete)
        r.add_post(prefix + "/undo", undo)
        r.add_post(prefix + "/approve", approve)
        r.add_post(prefix + "/presence", presence)
        r.add_post(prefix + "/ask", ask)
    r.add_get("/m/{sid}/api/me", me)
    r.add_post("/m/{sid}/api/code", enter_code)
    r.add_post("/m/{sid}/api/name", pick_name)
    r.add_post("/m/{sid}/api/forget-name", forget_name)
    r.add_get("/live/{sid}/api/editlog", editlog_all)
    r.add_post("/live/{sid}/api/rotate-code", rotate_code)
    r.add_post("/live/api/demo", start_demo)
    r.add_get("/live/api/meetings", meetings)
    r.add_get("/live/api/ui-config", ui_config)
    r.add_post("/live/api/public-url", public_url)
