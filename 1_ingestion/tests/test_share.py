"""The participants' link and access code in the meeting chat, and yes on behalf of someone else.

From a real Teams test (2026-11-19): two LiveMinutes bots in one call, one yes for the other bot, and
the link was never shown. Causes: the link went out 80 ms after "Thanks, everyone agreed" and Teams
lost it; no tunnel address at that moment; and the other bot matched "LiveMinutes" to our
"LiveMinutes - Aatmaj". No meeting, no Groq, Attendee mocked.

Run from the repo root:  .venv/bin/python -m pytest 1_ingestion/tests/test_share.py
"""

import asyncio
import json
import os
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from aiohttp import web

import bridge as bridge_module
import live_view
import settings
import ui
from chat import parse_vouch
from tests.test_consent import FakeBridge
from ui import access, demo, editlog
from ui.meeting import read_meta

SID = "bot_share_test"
PUBLIC = "https://minus-example-tunnel.trycloudflare.com"
THANKS = "Thanks, everyone agreed. Recording started."
OUR_BOT = "LiveMinutes - Aatmaj"


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


def public(url=PUBLIC):
    access.set_public_base_url(url)


class Bridge(FakeBridge):
    """Our bot, named like in the Teams test. `during_resume` runs while resume_recording is in flight."""

    def __init__(self, bot_name=OUR_BOT):
        super().__init__()
        self.live_url = "http://localhost:8776"
        self.bot_name = bot_name
        self.during_resume = None

    def session(self, bot_id):
        s = super().session(bot_id)
        s.bot_name = self.bot_name  # the webhook sets it from bot_metadata
        return s

    async def attendee(self, s, action, payload=None):
        self.calls.append(action)
        if action == "resume_recording" and self.during_resume:
            self.during_resume(s)
        return 200

    async def say(self, s, text):
        await super().say(s, text)
        return 200


async def join(b, s, uuid, name):
    await b.on_join_leave(s, {"participant_uuid": uuid, "participant_name": name, "event_type": "join"})


async def chat(b, s, uuid, name, text):
    await b.on_chat(s, {"text": text, "sender_uuid": uuid, "sender_name": name})


def links(b):
    return [m for m in b.chat if "Live notes for everyone:" in m]


async def two_people_agree(b, sid=SID):
    s = b.session(sid)
    await join(b, s, "8:orgid:ann", "Ann Lee")
    await join(b, s, "8:orgid:bob", "Bob Stone")
    await b.on_bot_state(s, {"new_state": "joined_recording"})
    await chat(b, s, "8:orgid:ann", "Ann Lee", "yes")
    await chat(b, s, "8:orgid:bob", "Bob Stone", "y")
    return s


# ================= when the link is posted =================

async def test_started_link_and_code_in_the_same_message_once():
    public()
    b = Bridge()
    s = await two_people_agree(b)
    code = access.pretty(access.CODES[SID])
    assert s.recording
    assert links(b) == [f"{THANKS} Live notes for everyone: {PUBLIC}/m/{SID} (access code {code}). "
                        f"Open it, enter the code and pick your name. It stops working 7 days after the meeting."]
    assert "\n" not in links(b)[0]                                    # Teams would press Enter
    assert not [m for m in b.chat if m == THANKS]                     # no separate "Thanks" message
    assert read_meta(SID)["chat_link"] == "posted"
    s.close()


async def test_audio_arriving_during_resume_still_says_started_and_posts_the_link():
    """In the Teams test, audio came in while resume_recording was in flight, set t0_ms, and the bot
    said "Recording resumed" at the first consent."""
    public()
    b = Bridge()
    b.during_resume = lambda s: setattr(s, "t0_ms", 1_795_127_558_159)
    s = await two_people_agree(b)
    assert len(links(b)) == 1 and links(b)[0].startswith(THANKS + " Live notes for everyone: ")
    s.close()


async def test_resumed_after_a_late_joiner_posts_no_second_link():
    public()
    b = Bridge()
    s = await two_people_agree(b)
    await join(b, s, "8:orgid:cy", "Cy Park")
    assert not s.recording and "Paused: waiting for Cy Park to reply yes." in b.chat
    await chat(b, s, "8:orgid:cy", "Cy Park", "yes")
    assert s.recording and b.chat[-1] == "Thanks, everyone agreed. Recording resumed."
    assert len(links(b)) == 1
    s.close()


async def test_once_per_meeting_even_after_a_bridge_restart():
    public()
    b = Bridge()
    s = await two_people_agree(b)
    s.close()
    b2 = Bridge()  # restarted: a new Session object, same meeting folder
    s2 = await two_people_agree(b2)
    assert b2.chat[-1] == "Thanks, everyone agreed. Recording resumed." and not links(b2)
    s2.close()


async def test_yes_on_behalf_completes_consent_and_posts_the_link_once():
    public()
    b = Bridge()
    s = b.session(SID)
    await join(b, s, "8:orgid:ann", "Ann Lee")
    await join(b, s, "8:teamsvisitor:room", "Conference Room 4B")
    await b.on_bot_state(s, {"new_state": "joined_recording"})
    await chat(b, s, "8:orgid:ann", "Ann Lee", "yes")
    assert not s.recording
    await chat(b, s, "8:orgid:ann", "Ann Lee", "Conference Room 4B yes")
    assert s.recording and s.people["8:teamsvisitor:room"].vouched_by == "Ann Lee"
    assert b.chat[-2:] == ["Noted: yes for Conference Room 4B, from Ann Lee.", links(b)[0]]
    assert len(links(b)) == 1
    s.close()


# ================= two LiveMinutes bots in one meeting =================

async def test_second_liveminutes_bot_in_the_meeting():
    """Our bot is "LiveMinutes - Aatmaj" (Attendee leaves it out of the participants); a teammate's bot
    "LiveMinutes" is a participant like a meeting room."""
    public()
    b = Bridge(bot_name=OUR_BOT)
    s = b.session(SID)
    for uuid, name in (("8:teamsvisitor:other", "LiveMinutes"), ("8:orgid:ash", "Gupta, Ashmit"),
                       ("8:orgid:aat", "Aatmaj Amol Salunke")):
        await join(b, s, uuid, name)
    await b.on_bot_state(s, {"new_state": "joined_recording"})
    assert OUR_BOT not in b.chat[0]  # our bot isn't asked to agree
    await chat(b, s, "8:orgid:ash", "Gupta, Ashmit", "y")
    await chat(b, s, "8:orgid:aat", "Aatmaj Amol Salunke", "yes")
    await chat(b, s, "8:orgid:aat", "Aatmaj Amol Salunke", "LiveMinutes yes")
    noted = [m for m in b.chat if m.startswith("Noted:")]
    assert noted == ["Noted: yes for LiveMinutes, from Aatmaj Amol Salunke."]   # one message, one participant
    assert s.recording and len(links(b)) == 1
    # The other bot's own chat lines change nothing here.
    before = list(b.chat)
    await chat(b, s, "8:teamsvisitor:other", "LiveMinutes", "Noted: yes for LiveMinutes - Aatmaj, from Aatmaj Amol Salunke.")
    await chat(b, s, "8:teamsvisitor:other", "LiveMinutes", "Thanks, everyone agreed. Recording started.")
    assert b.chat == before and s.recording
    s.close()


async def test_the_other_bot_ignores_a_yes_that_names_itself():
    """The same message as seen by the teammate's bot "LiveMinutes": "LiveMinutes yes" is about itself,
    so it must not count for our "LiveMinutes - Aatmaj" (that made the second "Noted" line)."""
    b = Bridge(bot_name="LiveMinutes")
    s = b.session(SID)
    await join(b, s, "8:teamsvisitor:ours", OUR_BOT)
    await join(b, s, "8:orgid:aat", "Aatmaj Amol Salunke")
    await b.on_bot_state(s, {"new_state": "joined_recording"})
    await chat(b, s, "8:orgid:aat", "Aatmaj Amol Salunke", "yes")
    await chat(b, s, "8:orgid:aat", "Aatmaj Amol Salunke", "LiveMinutes yes")
    assert not [m for m in b.chat if m.startswith("Noted:")]
    assert s.people["8:teamsvisitor:ours"].decision is None and not s.recording
    await chat(b, s, "8:orgid:aat", "Aatmaj Amol Salunke", "LiveMinutes - Aatmaj yes")   # its full name works
    assert "Noted: yes for LiveMinutes - Aatmaj, from Aatmaj Amol Salunke." in b.chat and s.recording
    s.close()


def test_similar_names_exact_first_and_one_participant_only():
    names = ["LiveMinutes", OUR_BOT, "Aatmaj Amol Salunke", "Gupta, Ashmit", "Conference Room 4B", "Room 12"]
    # exact wins over a longer name that contains it
    assert parse_vouch("LiveMinutes yes", names, own_name="LiveMinutes - Jithin") == ("LiveMinutes", "yes")
    assert parse_vouch("LiveMinutes yes", names) == ("LiveMinutes", "yes")
    assert parse_vouch("LiveMinutes - Aatmaj yes", names) == (OUR_BOT, "yes")
    assert parse_vouch("yes for liveminutes aatmaj", names) == (OUR_BOT, "yes")
    # our own name never reaches anyone else
    assert parse_vouch("LiveMinutes yes", [OUR_BOT, "Aatmaj Amol Salunke"], own_name="LiveMinutes") is None
    assert parse_vouch("Aatmaj yes", ["Aatmaj Amol Salunke", "Room 12"], own_name=OUR_BOT) is None  # could be us
    assert parse_vouch("Aatmaj yes", ["Aatmaj Amol Salunke", "Room 12"]) == ("Aatmaj Amol Salunke", "yes")
    # partial names: only when exactly one participant fits
    assert parse_vouch("Ashmit yes", names, own_name=OUR_BOT) == ("Gupta, Ashmit", "yes")
    assert parse_vouch("Room 4B yes", names, own_name=OUR_BOT) == ("Conference Room 4B", "yes")
    assert parse_vouch("Room 12 yes", names, own_name=OUR_BOT) == ("Room 12", "yes")       # exact, not 4B
    assert parse_vouch("Room yes", names, own_name=OUR_BOT) is None                        # two fit
    assert parse_vouch("Aatmaj yes", names) is None                                        # two fit
    assert parse_vouch("LiveMinutes yes", ["LiveMinutes", "LiveMinutes"]) is None          # two exact: ignored
    assert parse_vouch("LiveMinutes no", names, own_name="LiveMinutes - Jithin") == ("LiveMinutes", "no")


# ================= no public address; Make a new code =================

@pytest.fixture
async def app(aiohttp_client):
    b = Bridge()
    application = web.Application()
    live_view.add_routes(application, b)
    ui.setup(application, b)
    return b, await aiohttp_client(application)


async def test_no_public_url_posts_nothing_and_warns_the_organizer(app):
    b, client = app
    s = await two_people_agree(b)
    assert s.recording and b.chat[-1] == THANKS and not links(b)
    assert not [m for m in b.chat if "localhost" in m and "/m/" in m]
    snap = await (await client.get(f"/live/{SID}/api/snapshot")).json()
    share = snap["session"]["share"]
    assert share["chat"] == "no_public_url" and share["public"] is False
    assert share["code"] == access.pretty(access.CODES[SID])  # a code exists: the organizer can still give it out
    s.close()


async def test_make_a_new_code_is_the_only_way_to_post_again(app):
    b, client = app
    s = await two_people_agree(b)
    first_code = access.CODES[SID]
    # no public address yet: nothing is posted, and the answer says why
    r = await (await client.post(f"/live/{SID}/api/rotate-code", json={"post": True})).json()
    assert r["posted"] is False and r["not_posted"] == "no_public_url" and not links(b)
    # the tunnel starts (tunnel.sh), then Make a new code with "Post it in the meeting chat"
    assert (await client.post("/live/api/public-url", json={"url": PUBLIC})).status == 200
    r = await (await client.post(f"/live/{SID}/api/rotate-code", json={"post": True})).json()
    assert r["posted"] is True and r["public"] is True and r["url"] == f"{PUBLIC}/m/{SID}"
    assert links(b) == [b.chat[-1]] and b.chat[-1].startswith(f"New access code. Live notes for everyone: {PUBLIC}/m/{SID} "
                                                              f"(access code {r['code']}).")
    snap = await (await client.get(f"/live/{SID}/api/snapshot")).json()
    assert snap["session"]["share"]["chat"] == "posted"
    assert (await client.post(f"/m/{SID}/api/code", json={"code": first_code})).status == 401
    assert (await client.post(f"/m/{SID}/api/code", json={"code": r["code"]})).status == 200
    # without "post", or once the meeting is over, the bot says nothing
    await (await client.post(f"/live/{SID}/api/rotate-code", json={})).json()
    s.state = "ended"  # as the webhook does before on_bot_state
    await b.on_bot_state(s, {"new_state": "ended"})
    r = await (await client.post(f"/live/{SID}/api/rotate-code", json={"post": True})).json()
    assert r["posted"] is False and r["not_posted"] == "ended" and len(links(b)) == 1


async def test_make_a_new_code_reports_when_attendee_refuses_the_message(app):
    b, client = app
    s = await two_people_agree(b)
    public()

    async def refused(s, text):
        return 400
    b.say = refused
    r = await (await client.post(f"/live/{SID}/api/rotate-code", json={"post": True})).json()
    assert r["posted"] is False and r["not_posted"] == "chat_failed"
    s.close()


async def test_link_refused_at_first_consent_shows_a_warning():
    public()
    b = Bridge()
    sent = []

    async def refused(s, text):
        sent.append(text)
        return 400
    b.say = refused
    s = await two_people_agree(b)
    assert any("Live notes for everyone:" in m for m in sent) and read_meta(SID)["chat_link"] == "failed"
    s.close()


async def test_code_made_before_consent_is_the_one_posted(app):
    b, client = app
    public()
    s = b.session(SID)
    await join(b, s, "8:orgid:ann", "Ann Lee")
    await b.on_bot_state(s, {"new_state": "joined_recording"})
    r = await (await client.post(f"/live/{SID}/api/rotate-code", json={})).json()  # organizer, early
    await chat(b, s, "8:orgid:ann", "Ann Lee", "yes")
    assert len(links(b)) == 1 and f"(access code {r['code']})" in links(b)[0]
    s.close()


# ================= the tunnel address =================

async def test_tunnel_exit_clears_the_address_only_if_it_is_still_its_own(app):
    _, client = app
    new = "https://newer-tunnel.trycloudflare.com"
    await client.post("/live/api/public-url", json={"url": new})
    r = await (await client.post("/live/api/public-url", json={"url": "", "only_if": PUBLIC})).json()
    assert r == {"public_base_url": new, "changed": False} and access.public_base_url() == new
    r = await (await client.post("/live/api/public-url", json={"url": "", "only_if": new})).json()
    assert r["changed"] is True and access.public_base_url() == ""


@pytest.mark.skipif(not shutil.which("bash") or not Path("/usr/bin/curl").exists(), reason="needs bash and curl")
def test_tunnel_sh_sets_its_address_then_clears_only_its_own(tmp_path):
    """tunnel.sh against a fake bridge and a fake cloudflared that stops after a moment."""
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "cloudflared").write_text("#!/bin/sh\necho 'INF |  https://fake-tunnel-abc.trycloudflare.com  |'\nsleep 1\n")
    # curl: the real one, except the tunnel address itself "answers" without the network
    (bin_dir / "curl").write_text('#!/bin/sh\nfor a in "$@"; do case "$a" in https://*.trycloudflare.com*) exit 0;; esac; done\n'
                                  'exec /usr/bin/curl "$@"\n')
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "BRIDGE_URL": f"http://127.0.0.1:{server.server_port}"}
    script = Path(__file__).resolve().parent.parent / "tunnel.sh"
    done = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, timeout=30)
    server.shutdown()
    assert done.returncode == 0, done.stdout + done.stderr
    tunnel = "https://fake-tunnel-abc.trycloudflare.com"
    assert calls == [{"url": tunnel}, {"url": "", "only_if": tunnel}]


# ================= the bot's chat messages =================

async def test_bot_messages_go_out_one_at_a_time_with_a_gap():
    sent = []

    class Timed(bridge_module.Bridge):
        CHAT_GAP_S, CHAT_GAP_PER_CHAR_S, CHAT_GAP_MAX_S = 0.15, 0.0, 0.15

        async def attendee(self, s, action, payload=None):
            sent.append((time.monotonic(), payload["message"]))
            await asyncio.sleep(0.01)
            return 200

    b = Timed(http=None, consent_required=True)
    s = b.session(SID)
    statuses = await asyncio.gather(b.say(s, "one"), b.say(s, "two"), b.say(s, "three"))
    assert statuses == [200, 200, 200] and [m for _, m in sent] == ["one", "two", "three"]
    gaps = [later - earlier for (earlier, _), (later, _) in zip(sent, sent[1:])]
    assert all(g >= 0.14 for g in gaps), gaps
    s.close()


def test_chat_gap_grows_with_length_but_stays_short():
    b = bridge_module.Bridge
    gap = lambda n: min(b.CHAT_GAP_MAX_S, b.CHAT_GAP_S + b.CHAT_GAP_PER_CHAR_S * n)  # noqa: E731
    assert gap(40) >= 1.0 and gap(230) == 3.0
    assert b.CHAT_GAP_MAX_S * 2 < 10  # two messages in one webhook stay under Attendee's 10 s timeout
