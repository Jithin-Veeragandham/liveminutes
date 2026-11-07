"""Offline test for bridge.py, no meeting needed: a mock Attendee API (records every call the
bridge makes) plus a fake bot that sends webhooks and audio.

1. Start the bridge against the mock API, with short consent timers:
     ATTENDEE_URL=http://127.0.0.1:8099 CONSENT_REMIND_S=4 CONSENT_LEAVE_S=8 .venv/bin/python bridge.py
2. In another terminal (from 1_ingestion/):
     .venv/bin/python tests/consent_sim.py <scenario> <speech.wav>

Scenarios:
  yes_late  Meet: both say yes, then a late joiner pauses everyone until they say yes
  no        Meet: one person says no -> "Recording stopped.", leave, session deleted
  timeout   Meet: one person never answers -> reminder, then the bot leaves
  teams     Teams mixed audio: labels Alice, Bob, "Multiple speakers", "Unknown speaker"

<speech.wav> is any 16 kHz mono 16-bit WAV with at least 24 s of talking. Scenarios with speech
make a few real Groq calls.
"""

import asyncio
import base64
import json
import ssl
import sys
import time
import wave
from pathlib import Path

import aiohttp
from aiohttp import web

SCEN, WAV = sys.argv[1], sys.argv[2]
CA = Path(__file__).resolve().parent.parent / "certs" / "ca.crt"
BOT = f"bot_sim_{SCEN}"
HOOK = "http://127.0.0.1:8776/webhook"
WS = "wss://127.0.0.1:8775/attendee-websocket"
calls = []


async def api(request):
    body = await request.json() if request.can_read_body else {}
    action = request.match_info["action"]
    text = body.get("message")
    if action == "send_chat_message" and "message" not in body:
        text = "MISSING 'message' FIELD (Attendee would answer 400)"
    calls.append((round(time.time() - T0, 1), action, text))
    return web.json_response({})


def now():
    return int(time.time() * 1000)


async def hook(http, trigger, data):
    await http.post(HOOK, json={"bot_id": BOT, "bot_metadata": {"bot_name": "LiveMinutes"}, "trigger": trigger, "data": data})


async def join(http, uuid, name, kind="join"):
    await hook(http, "participant_events.join_leave", {"participant_uuid": uuid, "participant_name": name, "event_type": kind, "timestamp_ms": now()})


async def chat(http, uuid, name, text):
    await hook(http, "chat_messages.update", {"sender_uuid": uuid, "sender_name": name, "text": text, "timestamp_ms": now()})


async def state(http, old, new):
    await hook(http, "bot.state_change", {"old_state": old, "new_state": new})


async def speech_event(http, uuid, name, kind, ts=None):
    await hook(http, "participant_events.speech_start_stop", {"participant_uuid": uuid, "participant_name": name, "event_type": kind, "timestamp_ms": ts or now()})


async def speak(ws, uuid, pcm, seconds, quiet=None, mixed=False):
    """Stream `seconds` of pcm in 10 ms chunks; uuids in `quiet` get silence at the same time."""
    trigger = "realtime_audio.mixed" if mixed else "realtime_audio.per_participant"
    for i in range(int(seconds * 100)):
        chunk = pcm[(i * 320) % (len(pcm) - 320):][:320]
        data = {"chunk": base64.b64encode(chunk).decode(), "sample_rate": 16000, "timestamp_ms": now()}
        if not mixed:
            data["participant_uuid"] = uuid
        await ws.send_str(json.dumps({"bot_id": BOT, "trigger": trigger, "data": data}))
        for q in quiet or []:
            silent = {**data, "participant_uuid": q, "chunk": base64.b64encode(b"\0" * 320).decode()}
            await ws.send_str(json.dumps({"bot_id": BOT, "trigger": trigger, "data": silent}))
        await asyncio.sleep(0.01)


async def main():
    global T0
    T0 = time.time()
    app = web.Application()
    app.router.add_post("/api/v1/bots/{bot}/{action}", api)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 8099).start()

    w = wave.open(WAV)
    pcm = w.readframes(w.getnframes())
    speech_a, speech_b = pcm[16000 * 2 * 3:16000 * 2 * 9], pcm[16000 * 2 * 18:16000 * 2 * 24]  # 6 s each
    silence = b"\0" * 3200
    A, B, C = "spaces/x/devices/1", "spaces/x/devices/2", "spaces/x/devices/3"

    async with aiohttp.ClientSession() as http:
        async with http.ws_connect(WS, ssl=ssl.create_default_context(cafile=str(CA))) as ws:
            await join(http, A, "Alice")
            await join(http, B, "Bob")
            await state(http, "joining", "joined_recording")
            if SCEN == "yes_late":
                await speak(ws, A, speech_a, 3, quiet=[B])          # before consent: must be dropped
                await chat(http, A, "Alice", "Yes!")
                await speak(ws, A, speech_a, 2, quiet=[B])          # only Alice agreed: still dropped
                await chat(http, B, "Bob", "yes please")
                await state(http, "joined_recording_paused", "joined_recording")
                await speak(ws, A, speech_a, 6, quiet=[B])
                await speak(ws, A, silence, 2, quiet=[B])
                await join(http, C, "Carol")                        # late joiner: pause everyone
                await asyncio.sleep(0.5)
                await speak(ws, B, speech_b, 3, quiet=[A, C])       # must be dropped
                await chat(http, C, "Carol", "no problem, go ahead")  # unclear: ignored
                await chat(http, C, "Carol", "YES")
                await speak(ws, B, speech_b, 6, quiet=[A, C])
                await speak(ws, B, silence, 2, quiet=[A, C])
                await asyncio.sleep(4)
            elif SCEN == "no":
                await chat(http, A, "Alice", "yes")
                await chat(http, B, "Bob", "No")
                await speak(ws, A, speech_a, 3, quiet=[B])          # after the no: dropped
                await asyncio.sleep(1)
                await state(http, "leaving", "ended")              # bridge then asks Attendee to delete data
                await asyncio.sleep(1)
            elif SCEN == "timeout":
                await chat(http, A, "Alice", "yes")
                await asyncio.sleep(12)                             # with CONSENT_REMIND_S=4, CONSENT_LEAVE_S=8
            elif SCEN == "teams":
                await chat(http, A, "Alice", "yes")
                await chat(http, B, "Bob", "yes")
                await state(http, "joined_recording_paused", "joined_recording")
                await speech_event(http, A, "Alice", "speech_start")
                await speak(ws, None, speech_a, 5, mixed=True)
                await speech_event(http, A, "Alice", "speech_stop")
                await speak(ws, None, silence, 1.5, mixed=True)
                await speech_event(http, B, "Bob", "speech_start")
                await speak(ws, None, speech_b, 5, mixed=True)
                await speech_event(http, B, "Bob", "speech_stop")
                await speak(ws, None, silence, 1.5, mixed=True)
                both = now()                                        # both talk at once
                await speech_event(http, A, "Alice", "speech_start", both)
                await speech_event(http, B, "Bob", "speech_start", both)
                await speak(ws, None, speech_a, 4, mixed=True)
                await speech_event(http, A, "Alice", "speech_stop")
                await speech_event(http, B, "Bob", "speech_stop")
                await speak(ws, None, silence, 2, mixed=True)
                await speak(ws, None, speech_b, 3, mixed=True)      # nobody flagged as speaking
                await speak(ws, None, silence, 2, mixed=True)
                await asyncio.sleep(4)
            else:
                sys.exit(f"unknown scenario {SCEN}")

    print("Calls the bridge made to the (mock) Attendee API:")
    for c in calls:
        print("  ", c)
    print(f"Bridge output: sessions/{BOT}/ (transcript.txt, consent.jsonl)")
    await runner.cleanup()


asyncio.run(main())
