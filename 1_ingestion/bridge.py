"""LiveMinutes bridge: Attendee meeting bot -> consent -> Groq Whisper -> "[mm:ss] Name: text".

Attendee sends us two things:
  wss://<host>:8775/attendee-websocket   live audio (Meet: one stream per person; Teams: one mixed stream)
  http://<host>:8776/webhook             events: bot state, joins/leaves, chat, who is speaking, captions

What happens to them:
  1. Consent (bridge.py + chat.py): when the bot joins it pauses itself and asks in chat. Audio is
     dropped on arrival until EVERYONE present has said yes. A late joiner pauses everyone until they
     answer. Anyone saying no: the bot leaves and the session is deleted. No answer: remind, then leave.
  2. Cutting (audio.py): each stream is cut into 2-10 s pieces at pauses.
  3. Teams only (session.py): each piece of the mixed stream is named from "who is speaking" events,
     while Groq transcribes it (those events arrive ~0.7 s late, about as long as Groq takes).
  4. Upload (bridge.py): pieces go to Groq Whisper, at most 12 per minute (the free tier).
  5. Output (session.py): sessions/<bot_id>/transcript.txt, plus the live view (live_view.py).

Settings: settings.py.   Run:  .venv/bin/python bridge.py [--no-consent]
"""

import asyncio
import base64
import json
import logging
import signal
import ssl
import sys
import time
import weakref
from collections import deque

import aiohttp
from aiohttp import WSCloseCode, WSMsgType, web

import live_view
import settings
from audio import Speaker, wav_bytes
from chat import CONSENT_PROMPT, consent_message, parse_reply, parse_vouch
from session import Person, Session, mmss, now_ms
import ui  # the participants' live view, editing and Demo mode (ui/, docs/UI.md)

MIXED = "mixed"  # stream id for the Teams mixed audio
log = logging.getLogger("bridge")


def spoken_text(groq_json):
    """Text from Groq's verbose_json answer, minus what is probably not speech.
    Returns (text, dropped segments)."""
    kept, dropped = [], []
    for segment in json.loads(groq_json).get("segments") or []:
        text = (segment.get("text") or "").strip()
        no_speech = segment.get("no_speech_prob", 0)
        if no_speech > 0.6 or (text.lower() in settings.HALLUCINATIONS and no_speech > 0.3):
            dropped.append(text)
        elif text:
            kept.append(text)
    return " ".join(" ".join(kept).split()), dropped


class Bridge:
    def __init__(self, http, consent_required):
        self.http = http                      # aiohttp client, for Attendee and Groq
        self.consent_required = consent_required
        self.sessions = {}                    # bot_id -> Session
        self.live_url = settings.LIVE_VIEW_URL
        # upload queue
        self.queue = []                       # (session, piece) waiting for upload
        self.wake = asyncio.Event()           # set when something is queued
        self.sent_at = deque()                # upload times in the last 60 s, for the rate limit
        self.in_flight = set()                # uploads running now
        self.pause_until = 0.0                # Groq said "slow down": no uploads before this time

    def session(self, bot_id):
        if bot_id not in self.sessions:
            self.sessions[bot_id] = Session(bot_id, self.consent_required)
        return self.sessions[bot_id]

    # ================= Talking to Attendee =================

    async def attendee(self, s, action, payload=None):
        """POST /api/v1/bots/<bot_id>/<action>: pause_recording, resume_recording,
        send_chat_message, leave, delete_data. Returns the HTTP status (0 = no answer)."""
        if s.demo:  # Demo mode (ui/demo.py): there is no real bot to call
            return 200
        url = f"{settings.ATTENDEE_URL}/api/v1/bots/{s.bot_id}/{action}"
        headers = {"Authorization": f"Token {settings.ATTENDEE_API_KEY}"}
        try:
            async with self.http.post(url, json=payload or {}, headers=headers,
                                      timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status >= 300:
                    log.warning("Attendee %s -> HTTP %s %s", action, r.status, (await r.text())[:160])
                return r.status
        except Exception as e:
            log.warning("Attendee %s failed: %s", action, e)
            return 0

    async def attendee_request(self, method, path, payload=None):
        """Any Attendee API call, e.g. ("POST", "bots", {...}) or ("GET", "bots/<id>").
        Returns (HTTP status, JSON body or text); status 0 = no answer. Used by the live view."""
        url = f"{settings.ATTENDEE_URL}/api/v1/{path}"
        headers = {"Authorization": f"Token {settings.ATTENDEE_API_KEY}"}
        try:
            async with self.http.request(method, url, json=payload, headers=headers,
                                         timeout=aiohttp.ClientTimeout(total=15)) as r:
                text = await r.text()
                try:
                    return r.status, json.loads(text)
                except ValueError:
                    return r.status, text[:500]
        except Exception as e:
            return 0, str(e) or type(e).__name__

    async def say(self, s, text):
        """Post in the meeting chat."""
        s.sent_texts.add(text.strip())  # so we ignore it when it comes back through the chat webhook
        s.consent_log("bot_chat", text=text)
        await self.attendee(s, "send_chat_message", {"message": text, "to": "everyone"})

    # ================= Webhooks from Attendee =================

    async def webhook(self, request):
        payload = await request.json()
        trigger, data = payload.get("trigger"), payload.get("data", {})
        s = self.session(payload.get("bot_id", "unknown_bot"))
        s.bot_name = (payload.get("bot_metadata") or {}).get("bot_name") or s.bot_name  # set by send_bot.sh
        if not s.declined:
            s.events.write(json.dumps({"recv_ms": now_ms(), **payload}) + "\n")
            s.events.flush()

        if trigger == "bot.state_change":
            log.info("BOT %s -> %s", data.get("old_state"), data.get("new_state"))
            s.state = data.get("new_state") or s.state
            s.note("state", f"bot {data.get('old_state')} → {data.get('new_state')}")
            await self.on_bot_state(s, data)
        elif trigger == "participant_events.join_leave":
            log.info("%s %s", data.get("event_type"), data.get("participant_name"))
            await self.on_join_leave(s, data)
        elif trigger == "chat_messages.update":
            await self.on_chat(s, data)
        elif trigger == "participant_events.speech_start_stop":  # Teams: who is speaking
            uuid, ts, kind = data.get("participant_uuid"), data.get("timestamp_ms") or now_ms(), data.get("event_type")
            s.names.setdefault(uuid, data.get("participant_name"))
            s.talk.setdefault(uuid, []).append((ts, kind))
            if kind == "speech_start":
                self.cut_at_speaker_change(s, uuid, ts)
        elif trigger == "transcript.update" and not s.declined:  # the platform's own captions
            with open(s.dir / "captions.txt", "a") as f:
                f.write(f"[{time.strftime('%H:%M:%S')}] {data.get('speaker_name')}: "
                        f"{(data.get('transcription') or {}).get('transcript')}\n")
        return web.Response(text="ok")

    async def on_bot_state(self, s, data):
        new = data.get("new_state")
        if new == "joined_recording" and s.consent_required and not s.prompted:
            # The bot is in the meeting: pause it and ask everyone present.
            s.prompted = True
            await self.close_gate(s, "bot joined, asking for consent")
            for p in s.present():
                p.asked_ms = p.asked_ms or now_ms()
            names = [p.name for p in s.present()]
            s.consent_log("asked", people=names)
            await self.say(s, consent_message(names, teams=s.is_teams()))
        if new == "joined_recording" and self.live_url and not s.link_posted:
            s.link_posted = True
            # One line of plain text, so it is safe on Teams (typed key by key) and Meet alike.
            await self.say(s, f"Live transcript (organizer's computer only): {self.live_url}/live/{s.bot_id}")
        if new == "ended":
            ui.meeting_ended(s)  # the access code expires VIEW_ACCESS_DAYS from now
            if s.declined:
                status = await self.attendee(s, "delete_data")
                s.consent_log("attendee_data_deleted", http_status=status)
            s.close()

    async def on_join_leave(self, s, data):
        uuid, name, kind = data.get("participant_uuid"), data.get("participant_name"), data.get("event_type")
        s.names[uuid] = name
        if not s.consent_required:
            s.note("people", f"{name} {kind}")
            return
        p = s.people.setdefault(uuid, Person(name))
        p.name = name
        p.present = kind == "join"
        s.consent_log("joined" if p.present else "left", uuid=uuid, name=name)
        await self.evaluate(s)  # a late joiner pauses everyone first...
        if p.present and s.prompted and p.decision != "yes":  # ...then gets asked
            p.asked_ms, p.reminded = now_ms(), False
            await self.say(s, f"Hi {name}. {CONSENT_PROMPT} If {name} is a meeting room, phone line "
                              f'or bot, someone here can reply "{name} yes".')

    # ================= Consent =================

    async def on_chat(self, s, data):
        text, sender_name = data.get("text") or "", data.get("sender_name")
        if not s.consent_required or s.declined:
            return
        if text.strip() in s.sent_texts or sender_name == s.bot_name:
            return  # our own message, echoed back
        sender = s.chat_sender(data.get("sender_uuid"), sender_name)
        decision = parse_reply(text)  # "yes" / "no" / None
        target = sender               # whose answer this is
        if decision is None and sender is not None:  # maybe "<name> yes", for a room, phone line or bot
            vouch = parse_vouch(text, [p.name for p in s.present()])
            if vouch:
                name, decision = vouch
                target = next(p for p in s.present() if p.name == name)
        on_behalf_of = target.name if target and target is not sender else None
        s.consent_log("reply", name=sender_name, sender_uuid=data.get("sender_uuid"), text=text, decision=decision,
                      matched=sender.name if sender else None, on_behalf_of=on_behalf_of)
        if sender is None or decision is None:
            return  # not someone we know, or not a clear answer: ignored

        if on_behalf_of:
            target.vouched_by = sender.name
            s.consent_log("vouched", name=target.name, by=sender.name, decision=decision)
        target.decision = decision
        if decision == "no":
            await self.decline(s, f"{target.name} (per {sender.name})" if on_behalf_of else target.name)
            return
        if on_behalf_of:
            await self.say(s, f"Noted: yes for {target.name}, from {sender.name}.")
        await self.evaluate(s)

    async def evaluate(self, s):
        """Open the gate only while everyone present has said yes; otherwise close it."""
        if not s.consent_required or s.declined or s.leaving:
            return
        present = s.present()
        waiting = [p.name for p in present if p.decision != "yes"]
        if present and not waiting:
            if not s.recording:
                s.recording = True
                s.consent_log("resumed", people=[p.name for p in present])
                await self.attendee(s, "resume_recording")
                await self.say(s, f"Thanks, everyone agreed. Recording {'resumed' if s.t0_ms else 'started'}.")
                message = ui.share_message(s) if self.live_url else None  # once: participants' link + code
                if message:
                    await self.say(s, message)
        elif s.recording:
            await self.close_gate(s, f"waiting for {', '.join(waiting)}" if waiting else "nobody left in the meeting")
            if waiting:
                await self.say(s, f"Paused: waiting for {', '.join(waiting)} to reply yes.")

    async def close_gate(self, s, reason):
        """Stop keeping audio now: drop half-cut and queued pieces, and pause Attendee at the source."""
        if s.recording:
            s.recording = False
            self.drop_audio(s)
        s.consent_log("paused", reason=reason)
        await self.attendee(s, "pause_recording")

    async def decline(self, s, name):
        """Someone said no: stop, leave, delete."""
        was_recording = s.recording
        s.declined = True
        s.recording = False
        self.drop_audio(s)
        s.consent_log("declined", name=name)
        await self.say(s, "Recording stopped.")
        if was_recording:  # Attendee answers 400 when asked to pause an already paused bot
            await self.attendee(s, "pause_recording")
        await self.attendee(s, "leave")
        s.wipe()
        s.consent_log("session_deleted", note="bridge files deleted; Attendee data is deleted once the bot has ended")

    def drop_audio(self, s):
        for speaker in s.speakers.values():
            speaker.discard()
        self.queue = [(q, piece) for q, piece in self.queue if q is not s]

    async def consent_watch(self):
        """Every second: remind people who haven't answered, and leave if they still don't."""
        while True:
            await asyncio.sleep(1)
            for s in list(self.sessions.values()):
                if not s.consent_required or not s.prompted or s.declined or s.leaving:
                    continue
                waiting = [p for p in s.present() if p.decision != "yes" and p.asked_ms]
                if not waiting:
                    continue
                longest_wait_s = (now_ms() - min(p.asked_ms for p in waiting)) / 1000
                if longest_wait_s >= settings.LEAVE_AFTER_S:
                    s.leaving = True
                    s.consent_log("timeout_leave", waiting=[p.name for p in waiting])
                    await self.close_gate(s, "no answer")
                    await self.say(s, "Not everyone replied, so LiveMinutes is leaving.")
                    await self.attendee(s, "leave")
                elif any(not p.reminded and (now_ms() - p.asked_ms) / 1000 >= settings.REMIND_AFTER_S for p in waiting):
                    for p in waiting:
                        p.reminded = True
                    await self.say(s, "Reminder: reply yes to allow recording, or no to stop. "
                                      f"Waiting for {', '.join(p.name for p in waiting)}.")

    # ================= Audio in =================

    async def websocket(self, request):
        ws = web.WebSocketResponse(max_msg_size=16 * 1024 * 1024)
        await ws.prepare(request)
        request.app["websockets"].add(ws)
        log.info("audio websocket connected")
        async for message in ws:
            if message.type == WSMsgType.TEXT:
                try:
                    self.on_audio(json.loads(message.data))
                except Exception:
                    log.exception("bad audio message")
        log.info("audio websocket closed")
        return ws

    def on_audio(self, msg):
        """One audio chunk: {"trigger", "bot_id", "data": {"chunk": base64 PCM, "participant_uuid", "timestamp_ms"}}"""
        trigger = msg.get("trigger")
        if trigger not in ("realtime_audio.per_participant", "realtime_audio.mixed"):
            return
        s = self.session(msg.get("bot_id", "unknown_bot"))
        if not s.recording:
            return  # no consent: dropped on arrival, never buffered
        data = msg.get("data", {})
        uuid = data.get("participant_uuid") if trigger == "realtime_audio.per_participant" else MIXED
        if s.consent_required and uuid != MIXED and (uuid not in s.people or s.people[uuid].decision != "yes"):
            return  # belt and braces: this person has not said yes
        ts = data.get("timestamp_ms") or now_ms()
        if s.t0_ms is None:
            s.t0_ms = ts  # transcript times count from the first audio
        speaker = s.speakers.setdefault(uuid, Speaker(uuid))
        piece = speaker.add(base64.b64decode(data["chunk"]), ts)
        if piece:
            self.piece_done(s, piece)

    def cut_at_speaker_change(self, s, uuid, ts):
        """Teams: `uuid` started talking at ts. If the piece being built already holds someone
        else's speech, end it at ts, so each piece (and transcript line) has one voice.
        The event arrives ~0.5 s late, but the piece is usually still being built by then."""
        speaker = s.speakers.get(MIXED)
        if not s.recording or speaker is None or speaker.piece is None:
            return
        talked = s.talked(speaker.piece.start_ms, ts)
        if any(u != uuid and ms >= settings.MIN_TALK_MS for u, ms in talked.items()):
            piece = speaker.split(ts)
            if piece:
                self.piece_done(s, piece)

    def piece_done(self, s, piece):
        """A finished piece goes straight to the upload queue. Teams pieces are named later
        (label_if_due), while Groq works: the name isn't needed until the line is written."""
        self.enqueue(s, piece)

    def label_if_due(self, s, piece, force=False):
        """Teams: name a mixed piece from the speech events once its wait for late "who is
        speaking" webhooks (LABEL_GRACE_MS after speech end) is over. Returns the label or None."""
        if piece.uuid == MIXED and piece.label is None and (
                force or now_ms() - piece.speech_end_ms >= settings.LABEL_GRACE_MS):
            piece.label = s.who_spoke(piece)
        return piece.label

    def enqueue(self, s, piece):
        if not s.recording:
            return  # the gate closed while this piece was being cut
        piece.ready_ms = now_ms()
        self.queue.append((s, piece))
        self.wake.set()

    async def idle_flusher(self):
        """Every 0.25 s: end pieces whose stream went silent (a stream can simply stop sending)."""
        while True:
            await asyncio.sleep(0.25)
            now = now_ms()
            for s in list(self.sessions.values()):
                if not s.recording:
                    continue
                for speaker in s.speakers.values():
                    if speaker.piece and now - speaker.piece.speech_end_ms > settings.END_OF_TURN_MS + 300:
                        piece = speaker.flush()
                        if piece:
                            self.piece_done(s, piece)

    # ================= Upload to Groq =================

    async def dispatcher(self):
        """Send queued pieces to Groq, at most RPM_LIMIT per minute and MAX_IN_FLIGHT at once."""
        while True:
            await self.wake.wait()
            self.wake.clear()
            while self.queue:
                now = time.time()
                while self.sent_at and now - self.sent_at[0] > 60:
                    self.sent_at.popleft()
                if len(self.sent_at) >= settings.RPM_LIMIT or len(self.in_flight) >= settings.MAX_IN_FLIGHT \
                        or now < self.pause_until:
                    await asyncio.sleep(0.25)
                    continue
                s, piece = self.queue.pop(0)
                self.label_if_due(s, piece)
                # Pieces from the same speaker that piled up while we waited go out as one upload.
                for queued in [q for q in self.queue if q[0] is s and q[1].uuid == piece.uuid]:
                    item = queued[1]
                    if piece.uuid == MIXED and (piece.label is None or self.label_if_due(s, item) != piece.label):
                        break  # Teams: only merge pieces already named after the same person
                    if piece.seconds + item.seconds > settings.MAX_MERGED_S:
                        break
                    piece.merge(item)
                    self.queue.remove(queued)
                self.sent_at.append(time.time())
                task = asyncio.create_task(self.transcribe(s, piece))
                self.in_flight.add(task)
                task.add_done_callback(self.in_flight.discard)

    async def upload(self, wav, prompt):
        """One Groq request. Returns (status, body, headers); status 0 = network error."""
        form = aiohttp.FormData()
        form.add_field("file", wav, filename="piece.wav", content_type="audio/wav")
        form.add_field("model", settings.GROQ_MODEL)
        form.add_field("language", "en")
        form.add_field("response_format", "verbose_json")  # gives no_speech_prob per segment
        form.add_field("temperature", "0")
        if prompt:
            form.add_field("prompt", prompt)  # the speaker's last words, as context
        headers = {"Authorization": f"Bearer {settings.GROQ_API_KEY}",
                   "User-Agent": "liveminutes-groq-bridge/0.1"}  # Groq's Cloudflare blocks some default ones
        try:
            async with self.http.post(settings.GROQ_URL, data=form, headers=headers,
                                      timeout=aiohttp.ClientTimeout(total=30)) as r:
                return r.status, await r.text(), r.headers
        except Exception as e:
            return 0, str(e), {}

    async def transcribe(self, s, piece):
        if s.declined:
            return
        speaker = s.speakers[piece.uuid]
        s.n += 1
        piece.n = s.n
        wav = wav_bytes(bytes(piece.pcm))
        (s.dir / "pieces" / f"{piece.n:04d}.wav").write_bytes(wav)

        for attempt in range(1, 5):  # first try + 3 retries
            sent_ms = now_ms()
            status, body, headers = await self.upload(wav, speaker.prompt)
            if s.declined:
                return  # someone said no during the upload: keep nothing
            if status not in (429, 500, 502, 503, 0) or attempt == 4:
                break
            wait = float(headers.get("retry-after") or 2 * attempt)
            self.pause_until = max(self.pause_until, time.time() + wait)  # holds back all uploads
            log.warning("Groq %s on piece %d, retrying in %.0fs", status or "network error", piece.n, wait)
            await asyncio.sleep(wait)
            self.sent_at.append(time.time())  # a retry counts against the rate limit too
        done_ms = now_ms()

        text, dropped = spoken_text(body) if status == 200 else ("", [])
        if status != 200:
            log.error("Groq %s on piece %d: %s", status, piece.n, body[:200])
            s.note("error", f"Groq {status or 'network error'} on piece {piece.n}: {body[:160]}")

        # Teams: Groq is often back before the late "who is speaking" webhooks; wait out the rest.
        label_wait_ms = 0
        if piece.uuid == MIXED and piece.label is None:
            label_wait_ms = max(0, settings.LABEL_GRACE_MS - (now_ms() - piece.speech_end_ms)) if text else 0
            if label_wait_ms:
                await asyncio.sleep(label_wait_ms / 1000)
                if s.declined:
                    return
            self.label_if_due(s, piece, force=True)

        who = piece.label or s.name(piece.uuid)
        shown_ms = now_ms()
        delay_s = (shown_ms - piece.speech_end_ms) / 1000
        s.piece_log.write(json.dumps({
            "n": piece.n, "speaker": who, "uuid": piece.uuid, "status": status,
            "start_ms": piece.start_ms, "speech_end_ms": piece.speech_end_ms, "ready_ms": piece.ready_ms,
            "sent_ms": sent_ms, "done_ms": done_ms, "audio_s": round(piece.seconds, 2),
            "merged": piece.merged, "groq_ms": done_ms - sent_ms, "label_wait_ms": label_wait_ms,
            "shown_ms": shown_ms, "delay_after_speech_end_s": round(delay_s, 2),
            "left_req_day": headers.get("x-ratelimit-remaining-requests"), "text": text, "dropped": dropped,
            "talk_ms": piece.talk_ms,
        }) + "\n")
        s.piece_log.flush()
        if text:
            speaker.prompt = " ".join((speaker.prompt + " " + text).split()[-40:])  # keep the last 40 words
            s.lines.append((piece.start_ms, piece.speech_end_ms, who, text))
            s.write_transcript()
            s.touch()  # wake live pages waiting for news
            print(f"[{mmss(piece.start_ms - s.t0_ms)}] {who}: {text}    "
                  f"(+{delay_s:.1f}s after speech end, {piece.seconds:.1f}s audio, groq {done_ms - sent_ms}ms)", flush=True)

    # ================= Stopping =================

    async def shutdown(self):
        """Send what is still buffered, wait up to 20 s for uploads, save transcripts."""
        for s in self.sessions.values():
            if not s.recording:
                continue
            for speaker in s.speakers.values():
                piece = speaker.flush()
                if piece:
                    self.piece_done(s, piece)
        deadline = time.time() + 20
        while (self.queue or self.in_flight) and time.time() < deadline:
            await asyncio.sleep(0.2)
        for s in self.sessions.values():
            s.close()


async def main():
    if not settings.GROQ_API_KEY:
        sys.exit("GROQ_API_KEY is missing: put it in the repo's .env (see .env.example)")
    tls = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    tls.load_cert_chain(settings.CERT_DIR / "server.crt", settings.CERT_DIR / "server.key")

    async with aiohttp.ClientSession() as http:
        bridge = Bridge(http, settings.CONSENT_REQUIRED)
        app = web.Application(client_max_size=16 * 1024 * 1024)
        app["websockets"] = weakref.WeakSet()  # open audio connections, closed on shutdown
        app.router.add_get("/attendee-websocket", bridge.websocket)
        app.router.add_post("/webhook", bridge.webhook)
        app.router.add_get("/health", lambda request: web.Response(text="ok"))  # Docker health check
        live_view.add_routes(app, bridge)
        ui.setup(app, bridge)  # /m/<id> for participants, the new organizer pages, Demo mode

        # One app on two ports: audio needs TLS (wss://), webhooks and the live view use plain http.
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, settings.BIND_HOST, settings.WS_PORT, ssl_context=tls).start()
        await web.TCPSite(runner, settings.BIND_HOST, settings.WEBHOOK_PORT).start()
        log.info("listening: audio wss :%d, webhooks + live view http :%d, consent %s",
                 settings.WS_PORT, settings.WEBHOOK_PORT, "ON" if bridge.consent_required else "OFF")

        background = [asyncio.create_task(job()) for job in (bridge.dispatcher, bridge.idle_flusher, bridge.consent_watch)]
        stop = asyncio.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            asyncio.get_running_loop().add_signal_handler(sig, stop.set)
        await stop.wait()  # runs until Ctrl+C or `docker stop`

        for ws in list(app["websockets"]):
            await ws.close(code=WSCloseCode.GOING_AWAY, message=b"bridge stopping")
        await bridge.shutdown()
        for task in background:
            task.cancel()
        await runner.cleanup()
        log.info("stopped; transcripts saved in %s", settings.SESSIONS_DIR)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S", stream=sys.stdout)
    asyncio.run(main())
