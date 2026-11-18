"""API tests for the UI (ui/): access codes, the name cookie, permissions, the edit log, the guard,
XSS escaping, export, adapters and Demo mode. No meeting, no Attendee, no Groq.

Run from the repo root:  .venv/bin/python -m pytest 1_ingestion/tests
"""

import asyncio
import json
import time

import aiohttp
import pytest
from aiohttp import web
from datetime import date

import settings
from tests.test_consent import FakeBridge
from ui import access, adapters, demo, editlog, export, hub
from ui.meeting import Meeting
import bridge as bridge_module
import live_view
import ui

SID = "bot_ui_test"


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SESSIONS_DIR", tmp_path)
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "")
    monkeypatch.setattr(settings, "UI_SECRET", "test-secret")
    for limiter in access.ALL_LIMITERS:
        limiter.reset()
    access.CODES.clear()
    editlog._LOGS.clear()
    demo.RUNS.clear()
    yield


class Bridge(FakeBridge):
    def __init__(self):
        super().__init__()
        self.live_url = "http://localhost:8776"


async def consented_meeting(b, sid=SID, names=("Ann Lee", "Bob Stone")):
    """A meeting where everyone said yes, with two transcript lines. Returns (session, code)."""
    s = b.session(sid)
    for n, name in enumerate(names, 1):
        await b.on_join_leave(s, {"participant_uuid": f"u{n}", "participant_name": name, "event_type": "join"})
    await b.on_bot_state(s, {"new_state": "joined_recording"})
    for n, name in enumerate(names, 1):
        await b.on_chat(s, {"text": "yes", "sender_uuid": f"u{n}", "sender_name": name})
    t0 = 1_700_000_000_000
    s.t0_ms = t0
    s.lines += [(t0 + 1000, t0 + 3000, names[0], "Hello everyone"), (t0 + 9000, t0 + 12000, names[1], "Hi Ann")]
    return s, access.CODES.get(sid)


@pytest.fixture
async def env(aiohttp_client):
    b = Bridge()
    app = web.Application()
    live_view.add_routes(app, b)
    ui.setup(app, b)
    async def ok(request):
        return web.Response(text="ok")
    app.router.add_post("/webhook", ok)  # like bridge.main()
    app.router.add_get("/health", ok)
    client = await aiohttp_client(app)
    s, code = await consented_meeting(b)
    yield b, client, s, code
    s.close()


async def join(client, code, name=None, sid=SID):
    """Enter the code (and pick a name). Returns the CSRF token."""
    r = await client.post(f"/m/{sid}/api/code", json={"code": code})
    assert r.status == 200, await r.text()
    csrf = (await r.json())["csrf"]
    if name:
        r = await client.post(f"/m/{sid}/api/name", json={"name": name}, headers={"X-CSRF-Token": csrf})
        assert r.status == 200, await r.text()
        csrf = (await r.json())["csrf"]
    return csrf


# ================= access codes =================

def test_code_alphabet_and_format():
    for _ in range(200):
        code = access.new_code()
        assert len(code) == 6 and not set(code) & set("0O1IL")
    assert access.pretty("K7QM4X") == "K7Q-M4X"
    assert access.normalize_code(" k7q-m4x ") == "K7QM4X"


async def test_share_message_once_after_consent_and_code_stored_hashed(env):
    b, client, s, code = env
    shares = [m for m in b.chat if "/m/bot_ui_test" in m]
    assert len(shares) == 1 and access.pretty(code) in shares[0] and "\n" not in shares[0]
    assert b.chat.index(shares[0]) > b.chat.index("Thanks, everyone agreed. Recording started.")
    stored = (settings.SESSIONS_DIR / SID / "access.json").read_text()
    assert code not in stored and access.pretty(code) not in stored
    # a late joiner pauses and resumes recording, but no second link is posted
    await b.on_join_leave(s, {"participant_uuid": "u3", "participant_name": "Cy", "event_type": "join"})
    await b.on_chat(s, {"text": "yes", "sender_uuid": "u3", "sender_name": "Cy"})
    assert len([m for m in b.chat if "/m/bot_ui_test" in m]) == 1


async def test_not_shared_before_consent(aiohttp_client):
    b = Bridge()
    app = web.Application()
    ui.setup(app, b)
    client = await aiohttp_client(app)
    s = b.session("bot_waiting")
    await b.on_join_leave(s, {"participant_uuid": "u1", "participant_name": "Ann", "event_type": "join"})
    await b.on_bot_state(s, {"new_state": "joined_recording"})
    d = await (await client.get("/m/bot_waiting/api/me")).json()
    assert d["state"] == "not_shared"
    s.close()


async def test_right_and_wrong_code(env):
    _, client, _, code = env
    assert (await (await client.get(f"/m/{SID}/api/me")).json())["state"] == "need_code"
    wrong = await client.post(f"/m/{SID}/api/code", json={"code": "AAAAAA"})
    assert wrong.status == 401 and (await wrong.json())["state"] == "wrong_code"
    r = await client.post(f"/m/{SID}/api/code", json={"code": access.pretty(code).lower()})
    assert r.status == 200
    cookie = r.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie and f"Path=/m/{SID}" in cookie
    me = await (await client.get(f"/m/{SID}/api/me")).json()
    assert me["state"] == "need_name" and me["people"] == ["Ann Lee", "Bob Stone"]


async def test_code_tries_are_rate_limited(env):
    _, client, _, code = env
    statuses = [(await client.post(f"/m/{SID}/api/code", json={"code": "AAAAAA"})).status for _ in range(7)]
    assert statuses[:6] == [401] * 6 and statuses[6] == 429
    r = await client.post(f"/m/{SID}/api/code", json={"code": code})  # even the right code waits now
    assert r.status == 429 and int(r.headers["Retry-After"]) > 0


async def test_code_expires_after_the_meeting(env):
    b, client, s, code = env
    csrf = await join(client, code, "Ann Lee")
    s.state = "ended"
    access.mark_ended(SID, access.now_ms() - int((settings.VIEW_ACCESS_DAYS + 1) * 86_400_000))
    me = await (await client.get(f"/m/{SID}/api/me")).json()
    assert me["state"] == "expired"
    assert (await client.get(f"/m/{SID}/api/snapshot")).status == 410
    r = await client.post(f"/m/{SID}/api/code", json={"code": code})
    assert r.status == 410
    edit = await client.post(f"/m/{SID}/api/edit", json={"item": "s1", "base_version": 1, "changes": {"text": "x"}},
                             headers={"X-CSRF-Token": csrf})
    assert edit.status == 410


async def test_still_open_within_the_access_window(env):
    b, client, s, code = env
    await join(client, code, "Ann Lee")
    s.state = "ended"
    access.mark_ended(SID, access.now_ms() - 86_400_000)  # ended yesterday
    assert (await client.get(f"/m/{SID}/api/snapshot")).status == 200


async def test_access_stops_when_consent_is_withdrawn(env):
    b, client, s, code = env
    await join(client, code, "Ann Lee")
    assert (await client.get(f"/m/{SID}/api/snapshot")).status == 200
    await b.on_chat(s, {"text": "no", "sender_uuid": "u2", "sender_name": "Bob Stone"})
    assert not (settings.SESSIONS_DIR / SID / "access.json").exists()  # wiped with the session
    me = await (await client.get(f"/m/{SID}/api/me")).json()
    assert me["state"] == "declined"
    assert (await client.get(f"/m/{SID}/api/snapshot")).status == 410


async def test_new_code_signs_everyone_out(env):
    b, client, s, code = env
    await join(client, code, "Ann Lee")
    r = await client.post(f"/live/{SID}/api/rotate-code", json={})
    new = (await r.json())["code"]
    assert new != access.pretty(code)
    assert (await (await client.get(f"/m/{SID}/api/me")).json())["state"] == "need_code"
    assert (await client.post(f"/m/{SID}/api/code", json={"code": code})).status == 401
    assert (await client.post(f"/m/{SID}/api/code", json={"code": new})).status == 200


# ================= the name cookie =================

async def test_pick_name_from_the_consented_list(env):
    _, client, _, code = env
    csrf = await join(client, code)
    bad = await client.post(f"/m/{SID}/api/name", json={"name": "Mallory"}, headers={"X-CSRF-Token": csrf})
    assert bad.status == 400
    no_token = await client.post(f"/m/{SID}/api/name", json={"name": "Ann Lee"})
    assert no_token.status == 403
    ok = await client.post(f"/m/{SID}/api/name", json={"name": "Ann Lee"}, headers={"X-CSRF-Token": csrf})
    assert ok.status == 200
    me = await (await client.get(f"/m/{SID}/api/me")).json()
    assert me["state"] == "ok" and me["name"] == "Ann Lee" and me["can_edit"]


def test_cookie_signature_and_meeting_are_checked():
    value, payload = access.make_cookie(SID, 1, name="Ann Lee")
    assert access.read_cookie(SID, value)["n"] == "Ann Lee"
    assert access.read_cookie("bot_other", value) is None          # another meeting's cookie
    data, sig = value.rsplit(".", 1)
    forged = json.loads(__import__("base64").urlsafe_b64decode(data + "=="))
    forged["n"] = "Bob Stone"
    forged_data = __import__("base64").urlsafe_b64encode(json.dumps(forged).encode()).decode().rstrip("=")
    assert access.read_cookie(SID, f"{forged_data}.{sig}") is None  # name changed: signature fails
    assert access.read_cookie(SID, "garbage") is None


# ================= permissions =================

async def test_no_cookie_means_no_access_and_code_only_means_read_only(env):
    _, client, _, code = env
    assert (await client.get(f"/m/{SID}/api/snapshot")).status == 401
    assert (await client.post(f"/m/{SID}/api/edit", json={"item": "s1"})).status == 401
    csrf = await join(client, code)  # code, but no name yet
    snap = await client.get(f"/m/{SID}/api/snapshot")
    assert snap.status == 200 and (await snap.json())["me"]["can_edit"] is False
    r = await client.post(f"/m/{SID}/api/edit", json={"item": "s1", "base_version": 1, "changes": {"text": "x"}},
                          headers={"X-CSRF-Token": csrf})
    assert r.status == 403 and (await r.json())["state"] == "need_name"


async def test_writes_need_the_csrf_token(env):
    _, client, _, code = env
    await join(client, code, "Ann Lee")
    r = await client.post(f"/m/{SID}/api/edit", json={"item": "s1", "base_version": 1, "changes": {"text": "x"}})
    assert r.status == 403 and (await r.json())["state"] == "csrf"
    r = await client.post(f"/live/{SID}/api/edit", json={"item": "s1", "base_version": 1, "changes": {"text": "x"}},
                          headers={"Origin": str(client.make_url("")).rstrip("/")})
    assert r.status == 403  # organizer page writes need the organizer token too


async def test_tunnel_requests_only_reach_participant_pages(env):
    _, client, _, _ = env
    tunnel = {"Cf-Connecting-Ip": "203.0.113.9", "Host": "abc.trycloudflare.com"}
    for path in ("/live", "/live/api/meetings", f"/live/{SID}", f"/live/{SID}/api/snapshot", "/live/api/sessions",
                 f"/live/{SID}/logs", "/health"):
        assert (await client.get(path, headers=tunnel)).status == 404, path
    assert (await client.post("/webhook", json={}, headers=tunnel)).status == 404
    assert (await client.post("/live/api/demo", headers=tunnel)).status == 404
    page = await client.get(f"/m/{SID}", headers=tunnel)
    assert page.status == 200 and "Content-Security-Policy" in page.headers
    assert (await client.get("/static/css/app.css", headers=tunnel)).status == 200
    assert (await client.get(f"/m/{SID}/api/me", headers=tunnel)).status == 200


async def test_cross_site_writes_are_refused(env):
    _, client, _, code = env
    evil = {"Origin": "https://evil.example"}
    assert (await client.post(f"/m/{SID}/api/code", json={"code": code}, headers=evil)).status == 403
    assert (await client.post("/live/api/demo", headers=evil)).status == 403
    assert (await client.post("/live/api/demo", headers={"Sec-Fetch-Site": "cross-site"})).status == 403
    # a browser on our own origin still needs the organizer token for organizer writes
    own = {"Origin": str(client.make_url("")).rstrip("/"), "Sec-Fetch-Mode": "cors"}
    assert (await client.post("/live/api/public-url", json={"url": ""}, headers=own)).status == 403


# ================= the edit log =================

async def edit(client, csrf, item, base, sid=SID, **changes):
    return await client.post(f"/m/{sid}/api/edit", json={"item": item, "base_version": base, "changes": changes},
                             headers={"X-CSRF-Token": csrf})


async def test_edits_are_appended_with_versions(env):
    _, client, _, code = env
    csrf = await join(client, code, "Ann Lee")
    r = await edit(client, csrf, "s1", 1, text="Agreed to ship on Friday")
    assert r.status == 200
    item = (await r.json())["item"]
    assert item["version"] == 2 and item["edited_by"] == "Ann Lee" and item["text"] == "Agreed to ship on Friday"
    path = settings.SESSIONS_DIR / SID / "edits.jsonl"
    first = path.read_text()
    row = json.loads(first)
    assert {k: row[k] for k in ("who", "item", "field", "after", "version", "op")} == {
        "who": "Ann Lee", "item": "s1", "field": "text", "after": "Agreed to ship on Friday", "version": 2, "op": "update"}
    assert row["before"].startswith("Sample:") and row["ms"] > 0
    await edit(client, csrf, "a1", 1, owner="Bob Stone", due="2026-12-01")
    text = path.read_text()
    assert text.startswith(first)                       # append only: the first line is untouched
    rows = [json.loads(line) for line in text.splitlines()]
    assert len(rows) == 3 and rows[1]["change"] == rows[2]["change"]  # one save, two fields, one change id
    assert (await edit(client, csrf, "s1", 2, text="Agreed to ship on Friday")).status == 200  # no-op
    assert len(path.read_text().splitlines()) == 3


async def test_second_save_on_the_same_version_conflicts(env):
    _, client, _, code = env
    ann = await join(client, code, "Ann Lee")
    assert (await edit(client, ann, "a1", 1, text="Ann's version")).status == 200
    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as other:  # a second browser
        r = await other.post(client.make_url(f"/m/{SID}/api/code"), json={"code": code})
        bob = (await r.json())["csrf"]
        r = await other.post(client.make_url(f"/m/{SID}/api/name"), json={"name": "Bob Stone"},
                             headers={"X-CSRF-Token": bob})
        bob = (await r.json())["csrf"]
        r = await other.post(client.make_url(f"/m/{SID}/api/edit"),
                             json={"item": "a1", "base_version": 1, "changes": {"text": "Bob's version"}},
                             headers={"X-CSRF-Token": bob})
        assert r.status == 409
        d = await r.json()
        assert d["state"] == "conflict" and d["item"]["text"] == "Ann's version" and d["item"]["version"] == 2
        assert d["last_change"]["who"] == "Ann Lee"


async def test_undo_writes_a_new_entry(env):
    _, client, _, code = env
    csrf = await join(client, code, "Ann Lee")
    original = (await (await client.get(f"/m/{SID}/api/snapshot")).json())["summary"]["items"][0]["text"]
    change = (await (await edit(client, csrf, "s1", 1, text="Typo here")).json())["change"]
    r = await client.post(f"/m/{SID}/api/undo", json={"item": "s1", "change": change}, headers={"X-CSRF-Token": csrf})
    item = (await r.json())["item"]
    assert r.status == 200 and item["text"] == original and item["version"] == 3
    rows = [json.loads(x) for x in (settings.SESSIONS_DIR / SID / "edits.jsonl").read_text().splitlines()]
    assert [r["op"] for r in rows] == ["update", "undo"] and rows[1]["undo_of"] == change
    hist = await (await client.get(f"/m/{SID}/api/history?item=s1")).json()
    assert [c["op"] for c in hist["changes"]] == ["undo", "update"]
    # an older change can't be undone once something newer happened
    r = await client.post(f"/m/{SID}/api/undo", json={"item": "s1", "change": change}, headers={"X-CSRF-Token": csrf})
    assert r.status == 409 and (await r.json())["reason"] == "newer"


async def test_add_delete_and_restore_an_action_item(env):
    _, client, _, code = env
    csrf = await join(client, code, "Bob Stone")
    r = await client.post(f"/m/{SID}/api/create", json={"kind": "action", "fields": {"text": "Book the room", "owner": "Ann Lee"}},
                          headers={"X-CSRF-Token": csrf})
    new = (await r.json())["item"]
    assert new["origin"] == "added" and new["created_by"] == "Bob Stone" and new["status"] == "pending"
    r = await client.post(f"/m/{SID}/api/delete", json={"item": new["id"], "base_version": 1}, headers={"X-CSRF-Token": csrf})
    change = (await r.json())["change"]
    ids = [a["id"] for a in (await (await client.get(f"/m/{SID}/api/snapshot")).json())["actions"]["items"]]
    assert new["id"] not in ids
    await client.post(f"/m/{SID}/api/undo", json={"item": new["id"], "change": change}, headers={"X-CSRF-Token": csrf})
    ids = [a["id"] for a in (await (await client.get(f"/m/{SID}/api/snapshot")).json())["actions"]["items"]]
    assert new["id"] in ids


async def test_approve_items_and_minutes(env):
    _, client, _, code = env
    csrf = await join(client, code, "Ann Lee")
    item = (await (await edit(client, csrf, "a1", 1, status="approved")).json())["item"]
    assert item["approved_by"] == "Ann Lee"
    r = await client.post(f"/m/{SID}/api/approve", json={"base_version": 1}, headers={"X-CSRF-Token": csrf})
    minutes = (await r.json())["minutes"]
    assert minutes["approved"]["by"] == "Ann Lee" and minutes["changed_since"] is False
    await asyncio.sleep(0.01)
    await edit(client, csrf, "s2", 1, text="Changed after approval")
    snap = await (await client.get(f"/m/{SID}/api/snapshot")).json()
    assert snap["session"]["minutes"]["changed_since"] is True


@pytest.mark.parametrize("changes,why", [
    ({"due": "next friday"}, "Due date"), ({"status": "maybe"}, "Status"), ({"text": "x" * 301}, "too long"),
    ({"text": "   "}, "empty"), ({"color": "red"}, "can't be edited")])
async def test_bad_edits_are_refused(env, changes, why):
    _, client, _, code = env
    csrf = await join(client, code, "Ann Lee")
    r = await client.post(f"/m/{SID}/api/edit", json={"item": "a1", "base_version": 1, "changes": changes},
                          headers={"X-CSRF-Token": csrf})
    assert r.status == 400 and why in (await r.json())["error"]


async def test_whole_log_for_the_organizer_filterable_by_person(env):
    _, client, _, code = env
    csrf = await join(client, code, "Ann Lee")
    await edit(client, csrf, "s1", 1, text="One")
    await client.post(f"/live/{SID}/api/edit", json={"item": "s2", "base_version": 1, "changes": {"text": "Two"}},
                      headers={"X-CSRF-Token": access.ORGANIZER_TOKEN, "X-LM-Name": "Bob Stone"})
    log = await (await client.get(f"/live/{SID}/api/editlog")).json()
    assert log["people"] == ["Ann Lee", "Bob Stone"] and len(log["changes"]) == 2
    only = await (await client.get(f"/live/{SID}/api/editlog?who=Bob+Stone")).json()
    assert [c["who"] for c in only["changes"]] == ["Bob Stone"] and only["changes"][0]["role"] == "organizer"
    assert (await client.get(f"/m/{SID}/api/editlog")).status in (404, 405)  # organizer only


# ================= XSS, export, contract =================

XSS = '<img src=x onerror="alert(1)"> **bold** | pipe'


async def test_user_text_is_escaped_in_export_and_kept_as_data_in_json(env):
    b, client, s, code = env
    s.lines.append((s.t0_ms + 20_000, s.t0_ms + 22_000, "Ann Lee", XSS))
    csrf = await join(client, code, "Ann Lee")
    await edit(client, csrf, "a1", 1, text=XSS, owner="<b>Bob</b>")
    snap = await client.get(f"/m/{SID}/api/snapshot")
    assert snap.content_type == "application/json"
    assert any(line["text"] == XSS for line in (await snap.json())["lines"])  # raw data; the page uses textContent
    md = await (await client.get(f"/m/{SID}/api/export.md")).text()
    assert "<img" not in md and "<b>" not in md and "&lt;img" in md and "\\| pipe" in md and "\\*\\*bold" in md
    html = await (await client.get(f"/m/{SID}")).text()
    assert XSS not in html and "Ann Lee" not in html  # the page has no meeting data in it; the app reads JSON


async def test_pages_have_a_strict_csp(env):
    _, client, _, _ = env
    for path in (f"/m/{SID}", "/live", f"/live/{SID}"):
        r = await client.get(path)
        csp = r.headers["Content-Security-Policy"]
        assert "script-src 'self' 'nonce-" in csp and "frame-ancestors 'none'" in csp and "unsafe-inline" not in csp
        assert r.headers["X-Content-Type-Options"] == "nosniff"


async def test_export_markdown_minutes(env):
    b, client, s, code = env
    csrf = await join(client, code, "Ann Lee")
    await edit(client, csrf, "a1", 1, owner="Bob Stone", due="2026-12-01")
    await client.post(f"/m/{SID}/api/approve", json={"base_version": 1}, headers={"X-CSRF-Token": csrf})
    r = await client.get(f"/m/{SID}/api/export.md?tz=300")
    md = await r.text()
    assert r.content_type == "text/markdown" and "attachment" in r.headers["Content-Disposition"]
    assert md.startswith("# Untitled meeting")
    for part in ("**Attendees:** Ann Lee, Bob Stone", "## Summary", "## Decisions", "## Action items",
                 f"| Bob Stone | {date(2026, 12, 1).strftime('%a, %b %d').replace(' 0', ' ')} | Pending |",
                 "**Status:** Approved by Ann Lee", "## Edit history",
                 "2 changes by Ann Lee", "sample data"):
        assert part in md, part


def test_adapter_shapes_match_the_contract():
    class M:  # the bits of a Meeting the adapters use
        sid, demo = "bot_x", False

        def line_id_for(self, ms):
            return None
    for source in (adapters.summary_source(M()), adapters.actions_source(M())):
        assert set(source) >= {"items", "sample", "label", "note"}
    for item in adapters.summary_source(M())["items"]:
        assert {"id", "text", "source_line_ids", "version"} <= set(item) and isinstance(item["source_line_ids"], list)
    for item in adapters.actions_source(M())["items"]:
        assert {"id", "text", "owner", "due", "status", "source_line_ids", "version"} <= set(item)
        assert item["status"] in editlog.STATUSES["action"]
    answer = adapters.ask_source("What did we decide about the launch date?")
    assert answer["question"].startswith("What") and answer["source_meetings"]
    assert {"id", "title", "date"} <= set(answer["source_meetings"][0])
    assert adapters.ask_source("zebra?")["source_meetings"] == []


async def test_snapshot_lines_match_the_contract(env):
    _, client, _, code = env
    await join(client, code)
    lines = (await (await client.get(f"/m/{SID}/api/snapshot")).json())["lines"]
    assert [l["speaker"] for l in lines] == ["Ann Lee", "Bob Stone"]
    assert {"id", "speaker", "start", "end", "text"} <= set(lines[0]) and lines[0]["start"] == 1.0
    assert lines[0]["id"] == "t1700000001000"


def test_meeting_read_back_from_disk_after_a_restart(tmp_path):
    b = Bridge()

    async def run():
        s, code = await consented_meeting(b, "bot_disk")
        for n, (start, end, who, text) in enumerate(s.lines, 1):
            s.piece_log.write(json.dumps({"n": n, "speaker": who, "start_ms": start, "speech_end_ms": end,
                                          "text": text, "status": 200}) + "\n")
        s.piece_log.flush()
        ui.meeting_ended(s)
        s.close()
        return [l["id"] for l in Meeting(b, "bot_disk").lines()]
    live_ids = asyncio.run(run())
    m = Meeting(Bridge(), "bot_disk")  # a new bridge: nothing in memory
    assert [l["id"] for l in m.lines()] == live_ids
    assert m.consented() == ["Ann Lee", "Bob Stone"] and m.status()[0] == "ended"


# ================= organizer pages and Demo mode =================

async def test_new_organizer_pages_and_classic_ones(env):
    _, client, _, _ = env
    home = await (await client.get("/live")).text()
    assert 'id="root"' in home and "home.js" in home
    assert "Send bot" in await (await client.get("/live?classic=1")).text()
    page = await (await client.get(f"/live/{SID}")).text()
    assert '"role": "organizer"' in page
    assert "LiveMinutes live" in await (await client.get(f"/live/{SID}?classic=1")).text()
    assert (await client.get(f"/live/{SID}/data")).status == 200  # the old endpoints still answer


async def test_sse_stream_sends_snapshot_then_changes(env):
    _, client, _, code = env
    csrf = await join(client, code, "Ann Lee")
    r = await client.get(f"/m/{SID}/api/stream")
    assert r.headers["Content-Type"].startswith("text/event-stream")
    events = []

    async def read():
        event = None
        async for raw in r.content:
            line = raw.decode().rstrip("\n")
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: ") and event:
                events.append((event, json.loads(line[6:])))
                if event == "summary":
                    return
    reader = asyncio.ensure_future(read())
    await asyncio.sleep(0.3)
    assert events[0][0] == "snapshot"
    t0 = time.monotonic()
    await edit(client, csrf, "s1", 1, text="Live edit")
    await asyncio.wait_for(reader, 3)
    assert events[-1][1]["items"][0]["text"] == "Live edit" and time.monotonic() - t0 < 2
    r.close()


async def test_demo_runs_the_real_consent_flow(aiohttp_client):
    b = Bridge()
    app = web.Application()
    live_view.add_routes(app, b)
    ui.setup(app, b)
    client = await aiohttp_client(app)
    r = await client.post("/live/api/demo?speed=60&wait=1")
    d = await r.json()
    sid = d["id"]
    assert r.status == 200 and sid.startswith("demo_") and len(d["code"]) == 7 and d["participant_url"].endswith(f"/m/{sid}")
    run = demo.RUNS[sid]
    await asyncio.wait_for(run.task, 15)
    s = b.sessions[sid]
    assert "In this meeting:\n• Maya Chen\n• Daniel Okafor\n• Priya Raman" in b.chat[0]
    assert any(m.startswith("Live notes for everyone:") and d["code"] in m for m in b.chat)
    assert "Paused: waiting for Leo Martins to reply yes." in b.chat
    assert "Thanks, everyone agreed. Recording resumed." in b.chat
    assert run.dropped == 1                                   # said while Leo hadn't answered yet: not kept
    m = Meeting(b, sid)
    assert m.status()[0] == "ended" and len(m.lines()) > 25
    summary = adapters.summary_source(m)["items"]
    s4 = next(i for i in summary if i["id"] == "s4")
    assert s4["type"] == "decision" and s4["version"] == 2 and "December 9" in s4["text"] and s4["source_line_ids"]
    actions = adapters.actions_source(m)["items"]
    assert [a["owner"] for a in actions] == ["Daniel Okafor", "Priya Raman"] and all(a["due"] for a in actions)
    assert all(lid in {l["id"] for l in m.lines()} for a in actions for lid in a["source_line_ids"])
    assert access.read_record(sid)["ended_ms"]               # the access window started
    assert s.demo
    hub.forget(sid)


def test_real_bridge_never_calls_attendee_for_demo_sessions():
    real = bridge_module.Bridge(http=None, consent_required=True)  # http=None: any real call would crash
    s = real.session("demo_check")
    s.demo = True
    try:
        assert asyncio.run(real.attendee(s, "send_chat_message", {"message": "hi"})) == 200
    finally:
        s.close()


# ================= live updates through proxies, and what a tunnel can reach =================
# Cloudflare quick tunnels don't pass Server-Sent Events on, so the page renders from GET /snapshot
# and falls back to the long poll (/poll). These check the pieces of that.

async def test_poll_answers_at_once_then_waits_and_sends_only_new_lines(env):
    b, client, s, code = env
    csrf = await join(client, code, "Ann Lee")
    first = await (await client.get(f"/m/{SID}/api/poll")).json()
    assert [e[0] for e in first["events"]] == ["snapshot"] and first["v"]
    last_id = first["events"][0][1]["lines"][-1]["id"]
    waiting = asyncio.ensure_future(client.get(f"/m/{SID}/api/poll?v={first['v']}&after={last_id}"))
    await asyncio.sleep(0.4)
    assert not waiting.done()                                      # nothing new: the request is held
    t0 = time.monotonic()
    s.lines.append((s.t0_ms + 30_000, s.t0_ms + 32_000, "Bob Stone", "A brand new line"))
    s.touch()
    d = await (await waiting).json()
    assert time.monotonic() - t0 < 2 and d["v"] != first["v"]
    lines = dict(d["events"])["lines"]["upsert"]
    assert [l["text"] for l in lines] == ["Hi Ann", "A brand new line"]  # the last line again (it may grow) + the new one
    assert {"session", "people", "summary", "actions", "presence", "me"} <= dict(d["events"]).keys()
    # an edit wakes a waiting poll too
    waiting = asyncio.ensure_future(client.get(f"/m/{SID}/api/poll?v={d['v']}&after={lines[-1]['id']}"))
    await asyncio.sleep(0.3)
    await edit(client, csrf, "s1", 1, text="Edited while someone polls")
    d2 = dict((await (await waiting).json())["events"])
    assert d2["summary"]["items"][0]["text"] == "Edited while someone polls"


async def test_poll_resyncs_and_stops_when_access_ends(env):
    b, client, s, code = env
    await join(client, code, "Ann Lee")
    first = await (await client.get(f"/m/{SID}/api/poll")).json()
    d = await (await client.get(f"/m/{SID}/api/poll?v=stale&after=t_unknown")).json()
    assert [e[0] for e in d["events"]] == ["snapshot"]            # an unknown line id: full snapshot
    await b.on_chat(s, {"text": "no", "sender_uuid": "u2", "sender_name": "Bob Stone"})
    r = await client.get(f"/m/{SID}/api/poll?v={first['v']}")
    assert r.status == 410 and (await r.json())["state"] == "declined"
    assert (await client.get("/m/bot_nope/api/poll")).status == 404


async def test_pollers_count_as_viewing(env):
    _, client, _, code = env
    await join(client, code, "Bob Stone")
    await client.get(f"/m/{SID}/api/poll")
    snap = await (await client.get(f"/live/{SID}/api/snapshot")).json()
    assert "Bob Stone" in snap["presence"]["online"]


async def test_stream_is_proxy_friendly_and_pings(env, monkeypatch):
    from ui import routes
    monkeypatch.setattr(routes, "STREAM_HEARTBEAT_S", 0.3)
    _, client, _, code = env
    await join(client, code, "Ann Lee")
    r = await client.get(f"/m/{SID}/api/stream")
    assert r.headers["Cache-Control"] == "no-cache, no-transform" and r.headers["X-Accel-Buffering"] == "no"
    assert r.headers["Content-Encoding"] == "identity" and r.headers["Content-Type"].startswith("text/event-stream")
    seen = b""
    async def read():
        nonlocal seen
        async for chunk in r.content.iter_any():
            seen += chunk
            if b"event: ping" in seen:
                return
    await asyncio.wait_for(read(), 5)
    assert seen.startswith(b":" + b" " * 2048) and b"event: snapshot" in seen
    r.close()


async def test_bare_tunnel_address_shows_a_friendly_page(env):
    _, client, _, _ = env
    tunnel = {"Cf-Connecting-Ip": "203.0.113.9", "Host": "abc.trycloudflare.com"}
    r = await client.get("/", headers=tunnel)
    text = await r.text()
    assert r.status == 200 and "Open the link from your meeting chat" in text and "Content-Security-Policy" in r.headers
    local = await client.get("/", allow_redirects=False)
    assert local.status == 302 and local.headers["Location"] == "/live"  # on this computer: the organizer home


async def test_every_route_but_the_participant_ones_is_blocked_through_a_tunnel(aiohttp_client):
    """Builds the app like bridge.main() and tries EVERY registered route through a tunnel."""
    b = Bridge()
    app = web.Application()

    async def ok(request):
        return web.Response(text="ok")
    app.router.add_get("/attendee-websocket", ok)   # bridge.main(): the audio websocket
    app.router.add_post("/webhook", ok)
    app.router.add_get("/health", ok)
    live_view.add_routes(app, b)
    ui.setup(app, b)
    client = await aiohttp_client(app)
    s, code = await consented_meeting(b)
    tunnel = {"Cf-Connecting-Ip": "203.0.113.9", "Host": "abc.trycloudflare.com"}
    reached, blocked = [], []
    for route in app.router.routes():
        info = route.resource.get_info() if route.resource else {}
        path = info.get("path") or info.get("formatter")
        if path is None:                       # the static files
            path = info.get("prefix", "/static") + "/css/app.css"
        path = path.replace("{sid}", SID).replace("{bot_id}", SID)
        if route.method not in ("GET", "POST") or path in ("/", ):
            continue
        r = await client.request(route.method, path, headers=tunnel, json={} if route.method == "POST" else None)
        (blocked if r.status == 404 else reached).append(f"{route.method} {path}")
    assert reached and blocked
    for r in reached:
        assert r.split(" ", 1)[1].startswith(("/m/", "/static/", "/favicon.ico")), f"reachable through a tunnel: {r}"
    for must in ("GET /live", "GET /live/api/sessions", "POST /live/api/send", "GET /live/api/meetings",
                 "POST /live/api/demo", "POST /live/api/public-url", f"GET /live/{SID}/logs", f"GET /live/{SID}/data",
                 f"GET /live/{SID}/api/editlog", f"GET /live/{SID}/api/snapshot", f"POST /live/{SID}/api/edit",
                 f"POST /live/{SID}/api/rotate-code", "POST /webhook", "GET /health", "GET /attendee-websocket"):
        assert must in blocked, must
    s.close()
