"""Demo mode: a scripted, realistic 5-minute product meeting (ui/fixtures/demo_meeting.json) that runs
through the same pipeline as a real one, at real speed (or faster, for tests).

  - People join and the "bot joins" through the real Bridge.on_join_leave / on_bot_state, so the real
    consent message goes out, the real gate stays closed until everyone has said yes in "chat"
    (Bridge.on_chat), and a late joiner really pauses everyone.
  - Transcript lines go into Session.lines and pieces.jsonl like Groq's would, but only while the gate
    is open: a line said while recording is paused is dropped, exactly like audio would be.
  - Summary and action-item updates come from the script and are served by ui/adapters.py (labelled Demo).
  - There is no Attendee bot: Bridge.attendee() answers 200 for demo sessions without calling anything,
    and the bot's chat messages (consent request, link + code) show in the organizer's Activity tab.

Start: the "Start demo" button on http://localhost:8776/live, or
       curl -s -X POST http://localhost:8776/live/api/demo        (add ?speed=10 to run 10x faster,
       ?until=200 to stop the script at 3:20 and keep the meeting live there)
"""

import asyncio
import json
import secrets
from datetime import date, timedelta
from pathlib import Path

import settings
from ui import hub
from ui.access import now_ms
from ui.meeting import read_json, write_meta

SCRIPT = Path(__file__).parent / "fixtures" / "demo_meeting.json"
TRANSCRIBE_DELAY_S = 1.3   # a real line shows up about this long after the speaker stops
RUNS = {}                  # meeting id -> DemoRun, while the bridge runs


def load_script():
    return json.loads(SCRIPT.read_text())


def next_weekday(start, weekday):
    """The next date with this weekday (Monday = 0), never today."""
    return start + timedelta(days=(weekday - start.weekday()) % 7 or 7)


class DemoRun:
    def __init__(self, speed=1.0, until=None):
        self.sid = "demo_" + secrets.token_hex(4)
        self.speed = min(60.0, max(0.25, float(speed)))
        self.until = until  # stop the script at this second and keep the meeting live (screenshots, pausing a demo)
        self.summary, self.actions = {}, {}   # id -> item, in arrival order
        self.keys = {}                        # script line key -> start_ms of its piece
        self.dropped = 0
        self.done = False
        self.task = None

    def save(self):
        folder = settings.SESSIONS_DIR / self.sid
        if folder.is_dir():
            (folder / "demo.json").write_text(json.dumps({
                "summary": list(self.summary.values()), "actions": list(self.actions.values()),
                "keys": self.keys, "dropped": self.dropped, "done": self.done, "speed": self.speed}))

    def upsert(self, table, item):
        old = table.get(item["id"])
        item["version"] = (old["version"] + 1) if old else 1
        table[item["id"]] = item

    def open(self, bridge):
        """Make the session now, so its pages work as soon as start() returns."""
        script = load_script()
        s = bridge.session(self.sid)
        s.demo = True
        s.bot_name = "LiveMinutes"
        s.state = "joining"
        write_meta(self.sid, demo=True, title=script["title"], platform=script.get("platform", "meet"))
        s.note("state", "Demo meeting started (scripted, no real bot)")
        return script

    async def run(self, bridge, script):
        people = script["people"]
        s = bridge.sessions[self.sid]

        timeline = []
        for ev in script["events"]:
            fire = ev["t"] + (ev.get("dur", 0) + TRANSCRIBE_DELAY_S if "say" in ev else 0)
            timeline.append((fire, len(timeline), ev))
        timeline.sort()

        loop = asyncio.get_running_loop()
        t_start = loop.time()
        today = date.today()
        for fire, _, ev in timeline:
            if self.until is not None and fire > self.until:
                s.note("state", f"Demo held at {int(self.until // 60)}:{int(self.until % 60):02d} (until=)")
                return  # the meeting stays live where it is
            await asyncio.sleep(max(0.0, t_start + fire / self.speed - loop.time()))
            if s.declined:
                break
            if "join" in ev or "leave" in ev:
                key = ev.get("join") or ev.get("leave")
                await bridge.on_join_leave(s, {"participant_uuid": f"demo-{key}", "participant_name": people[key],
                                               "event_type": "join" if "join" in ev else "leave"})
            elif "bot" in ev:
                old, s.state = s.state, ev["bot"]
                s.note("state", f"bot {old} → {ev['bot']}")
                await bridge.on_bot_state(s, {"old_state": old, "new_state": ev["bot"]})
            elif "chat" in ev:
                await bridge.on_chat(s, {"text": ev["text"], "sender_uuid": f"demo-{ev['chat']}",
                                         "sender_name": people[ev["chat"]]})
            elif "say" in ev:
                self.say(s, people[ev["say"]], ev)
            elif "summary" in ev:
                item = ev["summary"]
                self.upsert(self.summary, {"id": item["id"], "type": item.get("type", "point"), "text": item["text"],
                                           "source": item.get("source", [])})
                self.save()
                hub.touch(self.sid)
            elif "action" in ev:
                item = ev["action"]
                due = next_weekday(today, item["due_weekday"]).isoformat() if "due_weekday" in item else ""
                self.upsert(self.actions, {"id": item["id"], "text": item["text"], "owner": people.get(item.get("owner"), ""),
                                           "due": due, "status": "pending", "source": item.get("source", [])})
                self.save()
                hub.touch(self.sid)
            elif ev.get("end"):
                old, s.state = s.state, "ended"
                s.note("state", f"bot {old} → ended (demo finished)")
                self.done = True
                self.save()
                await bridge.on_bot_state(s, {"old_state": old, "new_state": "ended"})
                hub.touch(self.sid)
        self.done = True
        self.save()

    def say(self, s, name, ev):
        """One transcript line, as if Groq had just returned it. Dropped while recording is paused."""
        if not s.recording:
            self.dropped += 1
            s.note("consent", f"Demo: a line by {name} was not kept (recording paused)")
            return
        end = now_ms() - int(TRANSCRIBE_DELAY_S * 1000 / self.speed)
        start = end - int(ev.get("dur", 3) * 1000 / self.speed)
        if s.t0_ms is None:
            s.t0_ms = start
            write_meta(self.sid, t0_ms=start)
        s.lines.append((start, end, name, ev["text"]))
        s.n += 1
        s.piece_log.write(json.dumps({"n": s.n, "speaker": name, "uuid": f"demo-{ev['say']}", "status": 200,
                                      "start_ms": start, "speech_end_ms": end, "done_ms": now_ms(), "groq_ms": 0,
                                      "audio_s": ev.get("dur"), "delay_after_speech_end_s": TRANSCRIBE_DELAY_S,
                                      "text": ev["text"], "demo": True}) + "\n")
        s.piece_log.flush()
        s.write_transcript()
        self.keys[ev["key"]] = start
        s.touch()


def start(bridge, speed=1.0, until=None):
    run = DemoRun(speed, until)
    RUNS[run.sid] = run
    script = run.open(bridge)
    run.task = asyncio.get_running_loop().create_task(run.run(bridge, script))
    return run


def state(sid):
    """The demo's summary and action items so far (live, or from demo.json after a restart)."""
    run = RUNS.get(sid)
    if run is not None:
        return {"summary": list(run.summary.values()), "actions": list(run.actions.values()), "keys": run.keys,
                "done": run.done}
    return read_json(settings.SESSIONS_DIR / sid / "demo.json", {"summary": [], "actions": [], "keys": {}, "done": True})
