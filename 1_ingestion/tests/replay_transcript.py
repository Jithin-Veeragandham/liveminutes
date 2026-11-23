"""Demo the live page with live notes, no meeting needed: replays a recorded transcript.txt line by line,
at the pace it was spoken, into the real live view + notes code (real LLM calls), on its own port.

    cd 1_ingestion
    ../.venv/bin/python tests/replay_transcript.py sessions/<bot_id>/transcript.txt [--speed 2] [--port 8777]
    (Windows: ..\\.venv\\Scripts\\python tests\\replay_transcript.py sessions\\<bot_id>\\transcript.txt)

Then open http://localhost:8777/live/bot_replayDemo (the meeting page: transcript, live summary and action items
with the countdown to the next LLM update; the Activity tab lists every update with its latency). --speed 2 plays the meeting twice as fast (the notes still
update every NOTES_INTERVAL_S of real time, so each update covers more of the meeting). Nothing is written to
sessions/: the demo session lives in a temporary folder. Ctrl+C to stop.
"""

import argparse
import asyncio
import re
import sys
import tempfile
from pathlib import Path

from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import settings  # noqa: E402

settings.SESSIONS_DIR = Path(tempfile.mkdtemp(prefix="lm_replay_"))   # keep the demo out of sessions/
import live_view  # noqa: E402
import notes  # noqa: E402
import ui  # noqa: E402
from session import Person, Session, now_ms  # noqa: E402

BOT = "bot_replayDemo"
LINE = re.compile(r"^\[(\d+):(\d{2})\] (.*?): (.*)$")


class DemoBridge:
    """Just what live_view needs from the real Bridge."""

    def __init__(self):
        self.sessions = {}
        self.notes = notes.LiveNotes()
        self.live_url = ""

    async def say(self, s, text):
        return 200

    async def attendee_request(self, method, path, payload=None):
        return 0, "replay demo: no Attendee"


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("transcript")
    p.add_argument("--speed", type=float, default=1.0)
    p.add_argument("--port", type=int, default=8777)
    a = p.parse_args()
    lines = []
    for raw in Path(a.transcript).read_text(encoding="utf-8").splitlines():
        m = LINE.match(raw)
        if m:
            lines.append((int(m[1]) * 60 + int(m[2]), m[3], m[4]))
    if not lines:
        sys.exit("no '[mm:ss] Name: text' lines")

    bridge = DemoBridge()
    if not bridge.notes.enabled:
        sys.exit(f"live notes are off: {bridge.notes.reason}")
    app = web.Application()
    live_view.add_routes(app, bridge)
    ui.setup(app, bridge)   # the meeting page
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", a.port).start()
    url = f"http://localhost:{a.port}/live/{BOT}"
    print(f"{len(lines)} lines, {lines[-1][0] // 60} min at speed {a.speed}; notes: {bridge.notes.model}, "
          f"every {settings.NOTES_INTERVAL_S} s\nopen {url}", flush=True)

    s = Session(BOT, consent_required=False)
    s.bot_name, s.state = f"Replay of {Path(a.transcript).parent.name}", "joined_recording"
    for n, name in enumerate(dict.fromkeys(who for _, who, _ in lines)):   # the speakers, as if they said yes
        s.people[f"replay-{n}"] = Person(name)
        s.people[f"replay-{n}"].present, s.people[f"replay-{n}"].decision = True, "yes"
    s.recording = True
    bridge.sessions[BOT] = s
    watcher = asyncio.create_task(bridge.notes.watch(bridge.sessions))
    s.t0_ms = now_ms()
    for start_s, who, text in lines:
        at = s.t0_ms + start_s * 1000 / a.speed       # demo clock: the meeting compressed by --speed
        await asyncio.sleep(max(0, (at - now_ms()) / 1000))
        s.lines.append((int(at), int(at) + 2000, who, text))
        s.touch()
    print("transcript done; final notes update", flush=True)
    s.state = "ended"
    s.touch()
    await bridge.notes.finish(s)
    s.touch()
    print(f"done. The page stays up: {url}  (Ctrl+C to stop)", flush=True)
    watcher.cancel()
    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
