"""A bridge web server for the UI's browser tests and screenshots: the real routes (live_view + ui),
the real Bridge consent logic, Demo mode, but no TLS audio port, no Attendee and no Groq.

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
    a = p.parse_args()
    settings.SESSIONS_DIR = Path(a.sessions)
    settings.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    settings.LIVE_VIEW_URL = f"http://127.0.0.1:{a.port}"
    settings.PUBLIC_BASE_URL = a.public
    settings.UI_SECRET = "e2e-test-secret"

    import bridge
    import live_view
    import ui

    async def make():
        b = bridge.Bridge(http=None, consent_required=True)  # demo sessions never call Attendee
        app = web.Application(client_max_size=1024 * 1024)
        app["websockets"] = set()

        async def ok(request):
            return web.Response(text="ok")
        app.router.add_post("/webhook", b.webhook)
        app.router.add_get("/health", ok)
        live_view.add_routes(app, b)
        ui.setup(app, b)
        return app

    web.run_app(make(), host="127.0.0.1", port=a.port, access_log=None, print=lambda *x: print(*x, flush=True))


if __name__ == "__main__":
    main()
