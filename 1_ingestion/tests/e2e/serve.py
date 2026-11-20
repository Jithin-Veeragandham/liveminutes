"""A bridge web server for the UI's browser tests and screenshots: the real routes (live_view + ui),
the real Bridge consent logic, Demo mode, but no TLS audio port, no Groq and a fake Attendee.

  .venv/bin/python 1_ingestion/tests/e2e/serve.py --port 8790 --sessions /tmp/lm-e2e
Then open http://127.0.0.1:8790/live
"""

import argparse
import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

from aiohttp import web  # noqa: E402

import settings  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8790)
    p.add_argument("--sessions", required=True)
    p.add_argument("--public", default="", help="PUBLIC_BASE_URL to pretend we have (e.g. https://demo.example)")
    p.add_argument("--fake-llm", action="store_true", help="live notes from a scripted LLM (no key, no network)")
    p.add_argument("--notes-interval", type=int, default=3, help="with --fake-llm: seconds between notes updates")
    a = p.parse_args()
    settings.SESSIONS_DIR = Path(a.sessions)
    settings.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    settings.LIVE_VIEW_URL = f"http://127.0.0.1:{a.port}"
    settings.PUBLIC_BASE_URL = a.public
    settings.UI_SECRET = "e2e-test-secret"

    import bridge
    import live_view
    import notes
    import ui
    from session import now_ms

    class FakeLLM:
        """The summarizer's JSON shape, scripted: the first answer, then a longer one. POST /test/llm
        {"fail": "message"} makes calls fail until {"fail": null}; {"delay": seconds} slows them down."""
        name = "fake"
        fail, delay, calls = None, 0, 0
        usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "served_by": ["fake-llm"]}

        def generate_json(self, system, user, schema, max_tokens):
            import time
            time.sleep(self.delay)
            if self.fail:
                raise RuntimeError(self.fail)
            self.calls += 1
            self.usage["input_tokens"] += 900
            self.usage["output_tokens"] += 140
            summary = ("Ann Lee opened the beta review with Bob Stone. The team agreed to ship the beta to the "
                       "testers on Friday. Bob Stone raised that the budget for the launch is tight.")
            actions = [{"task": "Send the beta release notes to the testers", "owner": "Bob", "due": "tomorrow"},
                       {"task": "Book the review room for next week", "owner": "Ann Lee", "due": "next meeting"}]
            if self.calls > 1:
                summary += " They also planned a short launch email for the existing customers."
                actions.append({"task": "Draft the launch email for existing customers", "owner": "Ann Lee", "due": "Friday"})
            return {"summary": summary, "action_items": actions}

    class FakeAttendeeBridge(bridge.Bridge):
        """Attendee is faked for meetings driven through /webhook (demo meetings never call it): every
        call succeeds, and the bot's chat messages are kept for GET /test/chat/<id>."""
        CHAT_GAP_S, CHAT_GAP_PER_CHAR_S, CHAT_GAP_MAX_S = 0.1, 0.0, 0.1  # the real gaps are tested offline
        chat = {}

        async def attendee(self, s, action, payload=None):
            if action == "send_chat_message":
                self.chat.setdefault(s.bot_id, []).append(payload["message"])
            return 200

    async def make():
        b = FakeAttendeeBridge(http=None, consent_required=True)
        app = web.Application(client_max_size=1024 * 1024)
        app["websockets"] = set()
        fake = FakeLLM()
        if a.fake_llm:
            settings.NOTES_INTERVAL_S = a.notes_interval
            settings.NOTES_SETTLE_MS = 0
            b.notes = notes.LiveNotes(provider=fake)

            async def start_notes(app):
                app["notes_task"] = asyncio.get_running_loop().create_task(b.notes.watch(b.sessions))
            app.on_startup.append(start_notes)

        async def lines(request):
            """{"sid", "lines": [[speaker, text], ...]}: transcript lines, as if just transcribed."""
            body = await request.json()
            s = b.sessions[body["sid"]]
            now = now_ms()
            s.t0_ms = s.t0_ms or now - 60_000
            for n, (who, text) in enumerate(body["lines"]):
                start = now - 5_000 + n * 400
                s.lines.append((start, start + 300, who, text))
            s.touch()
            return web.json_response({"lines": len(s.lines)})

        async def llm(request):
            body = await request.json()
            fake.fail = body.get("fail", fake.fail)
            fake.delay = body.get("delay", fake.delay)
            return web.json_response({"fail": fake.fail, "delay": fake.delay, "calls": fake.calls})

        async def ok(request):
            return web.Response(text="ok")

        async def chat(request):
            return web.json_response(b.chat.get(request.match_info["sid"], []))
        app.router.add_post("/webhook", b.webhook)
        app.router.add_get("/health", ok)
        app.router.add_get("/test/chat/{sid}", chat)
        app.router.add_post("/test/lines", lines)
        app.router.add_post("/test/llm", llm)
        live_view.add_routes(app, b)
        ui.setup(app, b)
        return app

    web.run_app(make(), host="127.0.0.1", port=a.port, access_log=None, print=lambda *x: print(*x, flush=True))


if __name__ == "__main__":
    main()
