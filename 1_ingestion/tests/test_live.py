"""Offline tests for the live view (/live/<bot_id>).

Run (from 1_ingestion/):  python -m unittest tests.test_live
"""

import asyncio
import json
import sys
import time
import unittest
from array import array
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.test_consent import FakeBridge  # noqa: E402  (also points settings at a temp sessions dir)
import audio  # noqa: E402
import bridge  # noqa: E402
import chat  # noqa: E402
import live_view  # noqa: E402
import settings  # noqa: E402


class Request:
    def __init__(self, bot_id):
        self.match_info = {"bot_id": bot_id}


class LiveViewTest(unittest.TestCase):
    def setUp(self):
        self.b = FakeBridge()
        self.b.live_url = "http://localhost:8776"
        self.s = self.b.session("bot_live")

    def tearDown(self):
        self.s.close()

    def test_link_posted_once_after_consent_prompt(self):
        asyncio.run(self.b.on_join_leave(self.s, {"participant_uuid": "u1", "participant_name": "Ann", "event_type": "join"}))
        for _ in range(2):
            asyncio.run(self.b.on_bot_state(self.s, {"new_state": "joined_recording"}))
        links = [m for m in self.b.chat if "/live/bot_live" in m]
        self.assertEqual(links, ["Live transcript (organizer's computer only): http://localhost:8776/live/bot_live"])
        self.assertIn("In this meeting", self.b.chat[0])  # consent prompt comes first
        self.assertNotIn("\n", links[0])  # safe to type in Teams

    def test_data_has_people_transcript_and_new_activity_only(self):
        asyncio.run(self.b.on_join_leave(self.s, {"participant_uuid": "u1", "participant_name": "Ann", "event_type": "join"}))
        asyncio.run(self.b.on_bot_state(self.s, {"new_state": "joined_recording"}))
        asyncio.run(self.b.on_chat(self.s, {"text": "yes", "sender_uuid": "u1", "sender_name": "Ann"}))
        self.s.t0_ms = 0
        self.s.lines += [(1000, 2000, "Ann", "hello"), (2500, 3000, "Ann", "there")]
        d = live_view.live_data(self.s, 0)
        self.assertTrue(d["recording"])
        self.assertEqual(d["people"][0]["decision"], "yes")
        self.assertEqual(d["transcript"], [{"t": "00:01", "who": "Ann", "text": "hello there"}])
        kinds = [e["kind"] for e in d["activity"]]
        self.assertIn("chat", kinds)
        self.assertIn("bot", kinds)
        self.assertTrue(any("→ yes" in e["text"] for e in d["activity"]))
        self.assertEqual(live_view.live_data(self.s, d["next"])["activity"], [])  # nothing new since
        json.dumps(d)  # must be sendable as JSON

    def test_teams_html_is_shown_as_plain_text(self):
        self.assertEqual(chat.plain("<b>Hi</b><br>• R&amp;D"), "Hi\n• R&D")

    def test_finished_session_read_from_disk(self):
        d = settings.SESSIONS_DIR / "bot_old"
        d.mkdir(parents=True, exist_ok=True)
        (d / "transcript.txt").write_text("[00:05] Ann: hi all\n")
        (d / "consent.jsonl").write_text(json.dumps({"time": "2026-11-06T10:00:01-0500", "event": "resumed"}) + "\n")
        out = live_view.saved_data("bot_old", 0)
        self.assertFalse(out["live"])
        self.assertEqual(out["transcript"], [{"t": "00:05", "who": "Ann", "text": "hi all"}])
        self.assertEqual(out["activity"][0]["time"], "10:00:01")

    def test_bad_bot_id_is_rejected(self):
        with self.assertRaises(web.HTTPNotFound):
            live_view.checked_bot_id(Request("../etc"))


class SendBotUnitTest(unittest.TestCase):
    def test_meeting_links(self):
        ok = live_view.meeting_link
        self.assertEqual(ok("meet.google.com/abc-defg-hij"), ("https://meet.google.com/abc-defg-hij", None))
        self.assertIsNone(ok(" https://teams.microsoft.com/l/meetup-join/19%3ameeting_x ")[1])
        self.assertIsNone(ok("https://teams.live.com/meet/9312345")[1])
        for bad in ("", "http://meet.google.com/abc-defg-hij", "https://evil.com/meet.google.com",
                    "https://meet.google.com/", "https://meet.google.com.evil.com/abc", "https://meet.google.com/a b"):
            self.assertIsNone(ok(bad)[0], bad)

    def test_bot_name(self):
        self.assertEqual(live_view.bot_name("  Notes   bot "), "Notes bot")
        self.assertEqual(live_view.bot_name(""), "LiveMinutes")
        self.assertEqual(len(live_view.bot_name("x" * 200)), 60)

    def test_payload_matches_send_bot_sh(self):
        meet = live_view.bot_payload("https://meet.google.com/abc-defg-hij", "LM", "liveminutes-bridge")
        self.assertEqual(meet["websocket_settings"],
                         {"per_participant_audio": {"url": "wss://liveminutes-bridge:8775/attendee-websocket"}})
        self.assertFalse(meet["recording_settings"]["record_participant_speech_start_stop_events"])
        self.assertEqual(meet["webhooks"][0]["url"], "http://liveminutes-bridge:8776/webhook")
        self.assertEqual(meet["metadata"], {"bot_name": "LM"})
        teams = live_view.bot_payload("https://teams.microsoft.com/l/meetup-join/x", "LM", "h")
        self.assertIn("audio", teams["websocket_settings"])
        self.assertTrue(teams["recording_settings"]["record_participant_speech_start_stop_events"])

    def test_logs_merge_three_sources_in_time_order(self):
        b = FakeBridge()
        s = b.session("bot_logs")
        try:
            s.activity.append({"time": "", "ms": 2_000, "kind": "state", "text": "bot joining → joined_recording"})
            s.piece_log.write(json.dumps({"n": 1, "speaker": "Ann", "status": 200, "done_ms": 3_000, "audio_s": 2.5,
                                          "groq_ms": 400, "delay_after_speech_end_s": 1.9, "text": "hello"}) + "\n")
            s.piece_log.write(json.dumps({"n": 2, "speaker": "Ann", "status": 429, "done_ms": 4_000}) + "\n{half")
            s.piece_log.flush()
            attendee = {"state": "joined_recording", "recording_state": "in_progress", "events": [
                {"type": "join_requested", "created_at": "1970-01-01T00:00:01Z"},
                {"type": "could_not_join", "sub_type": "meeting_not_found", "created_at": "1970-01-01T00:00:05+00:00"}]}
            out = live_view.bot_logs("bot_logs", s, 200, attendee)
            self.assertEqual([e["source"] for e in out["log"]], ["attendee", "bridge", "groq", "groq", "attendee"])
            self.assertEqual(out["log"][-1]["text"], "could not join (meeting not found)")
            self.assertEqual(out["log"][-1]["kind"], "error")
            self.assertEqual(out["log"][3]["kind"], "error")  # the 429 upload
            self.assertIn('"hello"', out["log"][2]["text"])
            self.assertEqual(out["attendee"]["state"], "joined_recording")
            self.assertEqual(out["bridge"]["session"], "live")
            down = live_view.bot_logs("bot_logs", s, 0, "Cannot connect")
            self.assertIn("not reachable", down["attendee"]["error"])
            json.dumps(out)
        finally:
            s.close()


class TeamsUploadFirstTest(unittest.TestCase):
    """Teams pieces go to Groq at once; the speaker is named after the late webhooks are in."""

    def setUp(self):
        self.grace = settings.LABEL_GRACE_MS
        settings.LABEL_GRACE_MS = 400

    def tearDown(self):
        settings.LABEL_GRACE_MS = self.grace

    def test_upload_starts_before_label_and_label_still_right(self):
        b = FakeBridge()
        s = b.session("bot_teams_fast")
        sent_at = []

        async def fake_upload(wav, prompt):
            sent_at.append(bridge.now_ms())
            return 200, json.dumps({"segments": [{"text": "hello there", "no_speech_prob": 0.01}]}), {}
        b.upload = fake_upload

        async def run():
            end = bridge.now_ms()
            piece = audio.Piece(bridge.MIXED, end - 2000)
            piece.pcm += bytes(16000 * 2 * 2)
            piece.speech_end_ms = end
            s.t0_ms = end - 5000
            s.speakers[bridge.MIXED] = audio.Speaker(bridge.MIXED)
            s.names["u1"] = "Ann"
            s.talk["u1"] = [(end - 2000, "speech_start")]  # her stop webhook is still on its way
            b.label_if_due(s, piece)
            self.assertIsNone(piece.label)  # too early to name it
            task = asyncio.create_task(b.transcribe(s, piece))
            await asyncio.sleep(0.15)
            s.talk["u1"].append((end + 50, "speech_stop"))  # arrives late, like Teams
            await task
            return end

        end = asyncio.run(run())
        try:
            self.assertLess(sent_at[0] - end, 100)  # uploaded right away, not after the grace
            self.assertEqual(s.lines[-1][2:], ("Ann", "hello there"))
            row = live_view.read_jsonl(s.dir / "pieces.jsonl")[-1]
            self.assertGreater(row["label_wait_ms"], 0)
            self.assertGreaterEqual(row["shown_ms"] - end, settings.LABEL_GRACE_MS - 20)
        finally:
            s.close()

    def test_backlog_merges_only_pieces_named_after_the_same_person(self):
        b = FakeBridge()
        s = b.session("bot_teams_merge")
        sent = []

        async def fake_transcribe(sess, piece):
            sent.append((piece.label, piece.merged))
        b.transcribe = fake_transcribe
        now = bridge.now_ms()
        s.names.update(u1="Ann", u2="Bob")
        s.talk.update(u1=[(now - 9000, "speech_start"), (now - 5000, "speech_stop")],
                      u2=[(now - 5000, "speech_start"), (now - 3000, "speech_stop")])

        def piece(start, end):
            p = audio.Piece(bridge.MIXED, start)
            p.pcm += bytes(16000 * 2)
            p.speech_end_ms = end
            return p

        async def run():
            # Ann, Ann (both past the grace), Bob, then a piece too new to name yet
            for p in (piece(now - 9000, now - 7000), piece(now - 7000, now - 5000),
                      piece(now - 5000, now - 3000), piece(now - 100, now - 50)):
                b.queue.append((s, p))
            b.wake.set()
            task = asyncio.create_task(b.dispatcher())
            await asyncio.sleep(0.2)
            task.cancel()

        try:
            asyncio.run(run())
            self.assertEqual(sent, [("Ann", 2), ("Bob", 1), (None, 1)])  # the last one is named after Groq
        finally:
            s.close()


class GroqLimitsTest(unittest.TestCase):
    def test_per_minute_and_per_hour_budgets(self):
        b = FakeBridge()
        t = 1_000_000.0
        for i in range(settings.RPM_LIMIT):
            self.assertTrue(b.can_send(t), i)
            b.count_upload(2.0, t)  # short pieces are billed as 10 s
        self.assertFalse(b.can_send(t + 1))  # 19 in this minute: wait
        self.assertTrue(b.can_send(t + 61))  # next minute: fine again
        # the hour budget: 7,000 s at >= 10 s each is 700 uploads, spread so the minute limit never bites
        b = FakeBridge()
        for i in range(settings.AUDIO_S_PER_HOUR // settings.BILLED_MIN_S):
            b.count_upload(1.0, t + i * 4)
        later = t + 700 * 4
        self.assertFalse(b.can_send(later))           # hour used up
        self.assertTrue(b.can_send(t + 3600 + 61))    # oldest uploads have left the window


class LongPollTest(AioHTTPTestCase):
    async def get_application(self):
        self.bridge = FakeBridge()
        self.s = self.bridge.session("bot_poll")
        app = web.Application()
        live_view.add_routes(app, self.bridge)
        return app

    async def asyncTearDown(self):
        self.s.close()
        await super().asyncTearDown()

    async def test_answers_as_soon_as_something_changes(self):
        v = (await (await self.client.get("/live/bot_poll/data")).json())["version"]
        t0 = time.monotonic()
        waiting = asyncio.ensure_future(self.client.get(f"/live/bot_poll/data?v={v}"))
        await asyncio.sleep(0.3)
        self.assertFalse(waiting.done())  # nothing new yet: the request is held open
        self.s.note("people", "Ann joined")
        d = await (await waiting).json()
        self.assertGreater(d["version"], v)
        self.assertEqual(d["activity"][-1]["text"], "Ann joined")
        self.assertLess(time.monotonic() - t0, 2)

    async def test_gives_up_after_timeout(self):
        old, live_view.LONG_POLL_S = live_view.LONG_POLL_S, 0.2
        try:
            v = self.s.version
            d = await (await self.client.get(f"/live/bot_poll/data?v={v}")).json()
            self.assertEqual(d["version"], v)
        finally:
            live_view.LONG_POLL_S = old


class FakeAttendeeBridge(FakeBridge):
    def __init__(self):
        super().__init__()
        self.requests = []

    async def attendee_request(self, method, path, payload=None):
        self.requests.append((method, path, payload))
        if method == "POST":
            return 201, {"id": "bot_new123", "state": "joining"}
        return 200, {"state": "joining", "events": [{"type": "join_requested", "created_at": "2026-11-07T10:00:00Z"}]}


class SendBotHttpTest(AioHTTPTestCase):
    async def get_application(self):
        self.bridge = FakeAttendeeBridge()
        app = web.Application()
        live_view.add_routes(app, self.bridge)
        settings.ATTENDEE_API_KEY = "test-key"
        return app

    async def test_send_creates_bot(self):
        r = await self.client.post("/live/api/send", json={"link": "meet.google.com/abc-defg-hij", "name": "LM"})
        self.assertEqual(r.status, 200)
        self.assertEqual((await r.json())["bot_id"], "bot_new123")
        method, path, payload = self.bridge.requests[-1]
        self.assertEqual((method, path, payload["meeting_url"]), ("POST", "bots", "https://meet.google.com/abc-defg-hij"))

    async def test_send_rejects_bad_link(self):
        r = await self.client.post("/live/api/send", json={"link": "https://example.com/x"})
        self.assertEqual(r.status, 400)
        self.assertEqual(self.bridge.requests, [])

    async def test_send_refuses_cross_site_requests(self):
        form = await self.client.post("/live/api/send", data={"link": "https://meet.google.com/abc-defg-hij"})
        other_site = await self.client.post("/live/api/send", json={"link": "https://meet.google.com/abc-defg-hij"},
                                            headers={"Origin": "https://evil.example"})
        rebinding = await self.client.post("/live/api/send", json={"link": "https://meet.google.com/abc-defg-hij"},
                                           headers={"Host": "evil.example:8776", "Origin": "http://evil.example:8776"})
        self.assertEqual([form.status, other_site.status, rebinding.status], [403, 403, 403])
        self.assertEqual(self.bridge.requests, [])

    async def test_logs_and_home(self):
        r = await self.client.get("/live/bot_new123/logs")
        d = await r.json()
        self.assertEqual(d["attendee"]["state"], "joining")
        self.assertEqual(d["log"][0]["source"], "attendee")
        self.assertEqual((await self.client.get("/live/..%2Fetc/logs")).status, 404)
        self.assertIn("Send bot", await (await self.client.get("/live")).text())
        self.assertIsInstance(await (await self.client.get("/live/api/sessions")).json(), list)


def tone(ms):
    """Loud 16 kHz audio (counts as speech) lasting ms."""
    return array("h", [8000, -8000] * int(settings.RATE * ms / 2000)).tobytes()


class SpeakerChangeCutTest(unittest.TestCase):
    """Teams: a piece is cut where a different person starts talking, not only at pauses."""

    def feed(self, speaker, start_ms, ms):
        """Feed `ms` of speech in 20 ms chunks starting at wall clock start_ms; return finished pieces."""
        out = []
        for t in range(start_ms, start_ms + ms, 20):
            piece = speaker.add(tone(20), t + 20)
            if piece:
                out.append(piece)
        return out

    def test_split_keeps_all_audio_on_the_right_side(self):
        sp = audio.Speaker(bridge.MIXED)
        self.feed(sp, 10_000, 3000)                    # 3 s of talk, no pause
        whole = len(sp.piece.pcm)
        first = sp.split(11_500)                        # someone else started at 1.5 s
        self.assertAlmostEqual(first.seconds, 1.5, delta=0.05)
        self.assertEqual(first.speech_end_ms, 11_500)
        self.assertEqual(sp.piece.start_ms, 11_500)     # the rest keeps building
        self.assertEqual(len(first.pcm) + len(sp.piece.pcm), whole)
        self.assertIsNone(sp.split(11_550))             # too little speech before the cut: no cut

    def test_speech_start_of_someone_else_cuts_the_piece(self):
        b = FakeBridge()
        s = b.session("bot_teams_cut")
        try:
            s.recording = True
            s.names.update(u1="Ann", u2="Bob")
            sp = s.speakers[bridge.MIXED] = audio.Speaker(bridge.MIXED)
            s.talk["u1"] = [(10_000, "speech_start"), (11_600, "speech_stop")]
            self.feed(sp, 10_000, 2500)                 # Ann, then Bob straight after with no pause
            b.cut_at_speaker_change(s, "u2", 11_600)    # Bob's (late) speech start
            self.assertEqual(len(b.queue), 1)
            first = b.queue[0][1]
            self.assertEqual(s.who_spoke(first), "Ann")
            s.talk["u2"] = [(11_600, "speech_start"), (12_500, "speech_stop")]
            self.assertEqual(s.who_spoke(sp.piece), "Bob")  # the rest is Bob's alone
            b.cut_at_speaker_change(s, "u2", 12_400)    # Bob again: nobody else in his piece, no cut
            self.assertEqual(len(b.queue), 1)
        finally:
            s.close()

    def test_one_word_piece_is_named(self):
        """A piece shorter than MIN_TALK_MS ("And...", 357 ms) is named after whoever said it."""
        b = FakeBridge()
        s = b.session("bot_teams_short")
        try:
            s.names.update(u1="Ann", u2="Bob")
            word = audio.Piece(bridge.MIXED, 20_000)
            word.speech_end_ms = 20_357
            s.talk["u1"] = [(19_884, "speech_start"), (20_384, "speech_stop")]
            self.assertEqual(s.who_spoke(word), "Ann")
            long = audio.Piece(bridge.MIXED, 30_000)    # a long piece still ignores a 300 ms blip
            long.speech_end_ms = 36_000
            s.talk["u1"] += [(30_000, "speech_start"), (36_000, "speech_stop")]
            s.talk["u2"] = [(33_000, "speech_start"), (33_300, "speech_stop")]
            self.assertEqual(s.who_spoke(long), "Ann")
        finally:
            s.close()


if __name__ == "__main__":
    unittest.main()
