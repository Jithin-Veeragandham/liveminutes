"""LiveMinutes feasibility receiver for Attendee (dev tool, from the first test).

Records raw meeting audio to WAV files. It has NO consent step: only use it with
people who agreed to be recorded. The live system is ../../bridge.py.

Two listeners in one process, so audio and participant names can be joined:
  wss://<host>:8765/attendee-websocket   realtime audio (mixed + per participant)
  http://<host>:8766/webhook             bot state, join/leave, speech, captions

Output goes to sessions/<bot_id>/:
  <participant_uuid>.wav    16 kHz mono, one per participant, all on one timeline
  mixed.wav                 mixed meeting audio
  participants.json         uuid -> name map (read by transcribe.py)
  audio_messages.jsonl      one line per audio message
  events.jsonl              every webhook payload

Run (from 1_ingestion/):  .venv/bin/python tools/receiver/receiver.py [--every]
  --every   print every audio message to the console (default: 1 s summary per speaker)
"""

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import math
import os
import re
import signal
import ssl
import sys
import time
import wave
import weakref
from array import array
from pathlib import Path

from aiohttp import WSCloseCode, WSMsgType, web

HERE = Path(__file__).resolve().parent
SESSIONS_DIR = HERE / "sessions"
SYSTEM_DIR = HERE.parent.parent  # 1_ingestion/: shared certs/
CERT_DIR = SYSTEM_DIR / "certs"

# A gap longer than this between chunks is filled with silence so every
# participant WAV stays aligned to the session's wall clock.
GAP_FILL_MS = 300

log = logging.getLogger("receiver")


def load_env(path):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def now_ms():
    return int(time.time() * 1000)


def clock(ms):
    return time.strftime("%H:%M:%S", time.localtime(ms / 1000)) + f".{ms % 1000:03d}"


def level_dbfs(pcm):
    samples = array("h", pcm)
    if not samples:
        return -99.0
    rms = math.sqrt(sum(s * s for s in samples) / len(samples))
    return 20 * math.log10(rms / 32768) if rms > 0 else -99.0


class Track:
    """One WAV file whose sample 0 sits at the session's t0."""

    def __init__(self, path, sample_rate, t0_ms):
        self.sample_rate = sample_rate
        self.wav = wave.open(str(path), "wb")
        self.wav.setnchannels(1)
        self.wav.setsampwidth(2)
        self.wav.setframerate(sample_rate)
        self.end_ms = t0_ms

    def write(self, pcm, sent_ms):
        duration_ms = len(pcm) / 2 / self.sample_rate * 1000
        start_ms = sent_ms - duration_ms
        gap_ms = start_ms - self.end_ms
        if gap_ms > GAP_FILL_MS:
            frames = int(gap_ms * self.sample_rate / 1000)
            self.wav.writeframes(b"\x00\x00" * frames)
            self.end_ms += frames / self.sample_rate * 1000
        self.wav.writeframes(pcm)  # wave patches the header on each write
        self.end_ms += duration_ms
        return duration_ms

    def close(self):
        self.wav.close()


class Session:
    def __init__(self, bot_id):
        self.bot_id = bot_id
        self.dir = SESSIONS_DIR / bot_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.t0_ms = None
        self.participants = {}  # uuid -> {name, is_host, user_uuid}
        self.tracks = {}  # uuid or "mixed" -> Track
        self.files = {}  # uuid or "mixed" -> wav filename
        self.stats = {}  # uuid or "mixed" -> {chunks, ms, delay, level}
        self.audio_log = open(self.dir / "audio_messages.jsonl", "a")
        self.event_log = open(self.dir / "events.jsonl", "a")
        self.manifest_path = self.dir / "participants.json"
        if self.manifest_path.exists():
            saved = json.loads(self.manifest_path.read_text())
            self.participants = saved.get("participants", {})
            self.files = saved.get("wav_files", {})

    def name(self, uuid):
        if uuid == "mixed":
            return "MIXED"
        return self.participants.get(uuid, {}).get("name") or "?unknown?"

    def save_manifest(self):
        data = {
            "bot_id": self.bot_id,
            "t0_ms": self.t0_ms,
            "sample_rate": 16000,
            "participants": self.participants,
            "wav_files": self.files,
        }
        self.manifest_path.write_text(json.dumps(data, indent=2))

    def track(self, key, sample_rate, first_sent_ms):
        if key not in self.tracks:
            if self.t0_ms is None:
                self.t0_ms = first_sent_ms
            # Meet uuids look like "spaces/abc/devices/300"; keep them out of the path
            filename = re.sub(r"[^A-Za-z0-9_.-]+", "_", key) + ".wav"
            path = self.dir / filename
            if path.exists():  # receiver restarted mid-meeting: keep the earlier audio
                old = self.dir / "before_restart"
                old.mkdir(exist_ok=True)
                path.rename(old / f"{path.stem}.{int(time.time())}.wav")
            self.tracks[key] = Track(path, sample_rate, self.t0_ms)
            self.files[key] = filename
            self.stats[key] = {"chunks": 0, "ms": 0.0, "delay": [], "level": -99.0}
            self.save_manifest()
            log.info("NEW AUDIO TRACK  %s  (%s)  -> %s", self.name(key), key, filename)
        return self.tracks[key]

    def close(self):
        for t in self.tracks.values():
            t.close()
        self.save_manifest()
        self.audio_log.close()
        self.event_log.close()


SESSIONS = {}


def session_for(bot_id):
    if bot_id not in SESSIONS:
        SESSIONS[bot_id] = Session(bot_id)
    return SESSIONS[bot_id]


# ---------- websocket: realtime audio ----------


def handle_audio_message(msg, print_every):
    received = now_ms()
    trigger = msg.get("trigger")
    data = msg.get("data", {})
    if trigger not in ("realtime_audio.mixed", "realtime_audio.per_participant"):
        log.info("WS other message: trigger=%s keys=%s", trigger, list(data))
        return

    s = session_for(msg.get("bot_id", "unknown_bot"))
    key = data.get("participant_uuid") or "mixed"
    sent_ms = data.get("timestamp_ms") or received
    rate = data.get("sample_rate", 16000)
    if rate != 16000:
        log.warning("sample_rate is %s, expected 16000", rate)
    pcm = base64.b64decode(data["chunk"])

    duration_ms = s.track(key, rate, sent_ms).write(pcm, sent_ms)
    delay_ms = received - sent_ms
    level = level_dbfs(pcm)

    st = s.stats[key]
    st["chunks"] += 1
    st["ms"] += duration_ms
    st["delay"].append(delay_ms)
    st["level"] = max(st["level"], level)

    s.audio_log.write(
        json.dumps(
            {
                "received_ms": received,
                "timestamp_ms": sent_ms,
                "delay_ms": delay_ms,
                "trigger": trigger,
                "participant_uuid": key,
                "participant_name": s.name(key),
                "bytes": len(pcm),
                "duration_ms": round(duration_ms, 1),
                "level_dbfs": round(level, 1),
            }
        )
        + "\n"
    )
    if print_every:
        log.info(
            "AUDIO %-14s %-20s uuid=%s ts=%s dur=%.0fms delay=%dms level=%.0fdBFS",
            trigger.split(".")[1], s.name(key), key, clock(sent_ms), duration_ms, delay_ms, level,
        )


async def print_summaries():
    """Once a second: who sent audio, how loud, and bot->receiver delay."""
    while True:
        await asyncio.sleep(1)
        for s in SESSIONS.values():
            for key, st in s.stats.items():
                if not st["chunks"]:
                    continue
                d = sorted(st["delay"])
                bar = "#" * max(0, int((st["level"] + 60) / 3))  # rough level meter
                log.info(
                    "AUDIO 1s  %-20s %-22s chunks=%-3d audio=%4.0fms delay p50=%dms max=%dms  peak=%4.0fdBFS %s",
                    s.name(key), key[:22], st["chunks"], st["ms"], d[len(d) // 2], d[-1], st["level"], bar,
                )
                s.stats[key] = {"chunks": 0, "ms": 0.0, "delay": [], "level": -99.0}
            s.audio_log.flush()


async def websocket_handler(request):
    ws = web.WebSocketResponse(max_msg_size=16 * 1024 * 1024)
    await ws.prepare(request)
    request.app["websockets"].add(ws)
    log.info("WS CONNECTED from %s", request.remote)
    count = 0
    async for message in ws:
        if message.type == WSMsgType.TEXT:
            count += 1
            try:
                handle_audio_message(json.loads(message.data), request.app["print_every"])
            except Exception:
                log.exception("bad websocket message")
        elif message.type == WSMsgType.ERROR:
            log.error("WS error: %s", ws.exception())
    log.info("WS DISCONNECTED after %d messages", count)
    return ws


# ---------- webhook: state + participants ----------


def signature_ok(payload, header):
    secret = os.environ.get("ATTENDEE_WEBHOOK_SECRET")
    if not secret or not header:
        return None
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    digest = hmac.new(base64.b64decode(secret), canonical.encode("utf-8"), hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), header)


async def webhook_handler(request):
    payload = await request.json()
    trigger = payload.get("trigger")
    data = payload.get("data", {})
    s = session_for(payload.get("bot_id", "unknown_bot"))
    s.event_log.write(json.dumps({"received_ms": now_ms(), **payload}) + "\n")
    s.event_log.flush()

    sig = signature_ok(payload, request.headers.get("X-Webhook-Signature"))
    sig_note = "" if sig is None else (" [sig ok]" if sig else " [SIG BAD]")

    if trigger == "bot.state_change":
        log.info(
            "BOT STATE  %s -> %s  (event=%s sub=%s)%s",
            data.get("old_state"), data.get("new_state"), data.get("event_type"), data.get("event_sub_type"), sig_note,
        )
    elif trigger and trigger.startswith("participant_events."):
        uuid = data.get("participant_uuid")
        if uuid:
            entry = s.participants.setdefault(uuid, {})
            entry.update(
                name=data.get("participant_name"),
                user_uuid=data.get("participant_user_uuid"),
                is_host=data.get("participant_is_host"),
            )
            s.save_manifest()
        log.info(
            "PARTICIPANT %-17s %-20s uuid=%s host=%s at %s%s",
            data.get("event_type"), data.get("participant_name"), uuid, data.get("participant_is_host"),
            clock(data["timestamp_ms"]) if data.get("timestamp_ms") else "?", sig_note,
        )
    elif trigger == "transcript.update":
        text = (data.get("transcription") or {}).get("transcript")
        log.info("CAPTION  %s: %s%s", data.get("speaker_name"), text, sig_note)
    elif trigger == "bot_logs.update":
        log.info("BOT LOG  [%s] %s: %s", data.get("level"), data.get("entry_type"), data.get("message"))
    else:
        log.info("WEBHOOK  %s %s", trigger, json.dumps(data)[:300])
    return web.Response(text="ok")


# ---------- main ----------


async def main(print_every):
    ws_port = int(os.environ.get("RECEIVER_WS_PORT", 8765))
    hook_port = int(os.environ.get("RECEIVER_WEBHOOK_PORT", 8766))

    tls = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    tls.load_cert_chain(CERT_DIR / "server.crt", CERT_DIR / "server.key")

    app = web.Application(client_max_size=16 * 1024 * 1024)
    app["print_every"] = print_every
    app["websockets"] = weakref.WeakSet()
    app.router.add_get("/attendee-websocket", websocket_handler)
    app.router.add_post("/webhook", webhook_handler)
    app.router.add_get("/health", lambda r: web.Response(text="ok"))

    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    # Colima delivers host.docker.internal traffic to the Mac's loopback,
    # so localhost is enough and the receiver isn't exposed on the LAN.
    bind = os.environ.get("RECEIVER_BIND", "127.0.0.1")
    await web.TCPSite(runner, bind, ws_port, ssl_context=tls).start()
    await web.TCPSite(runner, bind, hook_port).start()
    log.info("websocket  wss://host.docker.internal:%d/attendee-websocket", ws_port)
    log.info("webhook    http://host.docker.internal:%d/webhook", hook_port)
    log.info("saving to  %s", SESSIONS_DIR)

    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)

    summaries = None if print_every else asyncio.create_task(print_summaries())
    try:
        await stop.wait()
    finally:
        if summaries:
            summaries.cancel()
        # Close the bot's socket right away so it notices and reconnects to the
        # next receiver; otherwise cleanup holds it open (unread) for up to 60 s.
        for ws in list(app["websockets"]):
            await ws.close(code=WSCloseCode.GOING_AWAY, message=b"receiver restarting")
        await runner.cleanup()
        for s in SESSIONS.values():
            try:
                s.close()
            except Exception:
                log.exception("could not close session %s", s.bot_id)
        log.info("stopped; WAV files closed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--every", action="store_true", help="print every audio message")
    args = parser.parse_args()
    load_env(SYSTEM_DIR.parent / ".env")  # the repo's .env
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S", stream=sys.stdout)
    asyncio.run(main(args.every))
