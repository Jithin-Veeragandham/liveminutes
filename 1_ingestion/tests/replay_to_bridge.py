"""Replay a WAV into bridge.py as two fake Meet participants, in real time (no meeting needed).
First half = 'Speaker A', then a 1.5 s gap, second half = 'Speaker B'. The quiet participant
gets silence chunks, like Meet sends. Makes a few real Groq calls.

1. Start the bridge without consent (this tests cutting + transcription only):
     .venv/bin/python bridge.py --no-consent
2. From 1_ingestion/:
     .venv/bin/python tests/replay_to_bridge.py <speech.wav>

<speech.wav> is any 16 kHz mono 16-bit WAV, e.g. a mixed.wav made by tools/receiver.
Output: sessions/bot_replayTest/transcript.txt
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

WAV = sys.argv[1]
CA = Path(__file__).resolve().parent.parent / "certs" / "ca.crt"
BOT = "bot_replayTest"
A, B = "spaces/test/devices/1", "spaces/test/devices/2"


def msg(uuid, pcm):
    return json.dumps({"bot_id": BOT, "trigger": "realtime_audio.per_participant",
                       "data": {"participant_uuid": uuid, "chunk": base64.b64encode(pcm).decode(),
                                "sample_rate": 16000, "timestamp_ms": int(time.time() * 1000)}})


async def main():
    w = wave.open(WAV)
    pcm = w.readframes(w.getnframes())
    half = (len(pcm) // 2) // 320 * 320
    plan = [(A, pcm[:half]), (B, b"\x00" * 48000), (B, pcm[half:])]
    async with aiohttp.ClientSession() as http:
        for uuid, name in [(A, "Speaker A"), (B, "Speaker B")]:
            await http.post("http://localhost:8776/webhook", json={
                "bot_id": BOT, "trigger": "participant_events.join_leave",
                "data": {"participant_uuid": uuid, "participant_name": name, "event_type": "join"}})
        async with http.ws_connect("wss://localhost:8775/attendee-websocket", ssl=ssl.create_default_context(cafile=str(CA))) as ws:
            start, sent = time.time(), 0
            for uuid, audio in plan:
                other = B if uuid == A else A
                for i in range(0, len(audio), 320):  # 10 ms chunks
                    await ws.send_str(msg(uuid, audio[i:i + 320]))
                    await ws.send_str(msg(other, b"\x00" * 320))
                    sent += 1
                    await asyncio.sleep(max(0, start + sent * 0.01 - time.time()))
            await asyncio.sleep(4)
    print(f"replayed {sent * 0.01:.1f}s -> see sessions/{BOT}/transcript.txt")


asyncio.run(main())
