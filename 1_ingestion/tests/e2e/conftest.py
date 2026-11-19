"""Fixtures for the browser tests: a real bridge web server (serve.py) on a free port with a temporary
sessions folder, a Chromium browser, and pages that record every console error, page error and failed
request (the tests fail on any of them)."""

import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
AXE = HERE.parent / "vendor" / "axe" / "axe.min.js"

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, tmp):
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.sessions = tmp / "sessions"
        self.proc = subprocess.Popen([sys.executable, str(HERE / "serve.py"), "--port", str(self.port), "--sessions", str(self.sessions)],
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for _ in range(100):
            try:
                urllib.request.urlopen(self.base + "/health", timeout=1)
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("test server did not start")

    def post(self, path):
        return json.load(urllib.request.urlopen(urllib.request.Request(self.base + path, method="POST"), timeout=150))

    def get(self, path):
        return json.load(urllib.request.urlopen(self.base + path, timeout=10))

    def demo(self, speed=8):
        return self.post(f"/live/api/demo?speed={speed}&wait=1")

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    s = Server(tmp_path_factory.mktemp("e2e"))
    yield s
    s.stop()


@pytest.fixture(scope="module")
def browser():
    if sync_playwright is None:
        pytest.skip("playwright is not installed (requirements.txt section 4)")
    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:  # pragma: no cover
            pytest.skip(f"Chromium missing: .venv/bin/python -m playwright install chromium ({e})")
        yield b
        b.close()


class Watch:
    """Problems seen on pages: console errors, uncaught exceptions, failed requests and HTTP 5xx."""

    def __init__(self):
        self.problems = []
        self.closing = False
        self.allowed = []  # substrings of problems a test expects (e.g. a 401 for a wrong code)

    def attach(self, page, label):
        page.on("console", lambda m: m.type == "error" and not self._ok(m.text) and self.problems.append(f"[{label}] console.error: {m.text}"))
        page.on("pageerror", lambda e: self.problems.append(f"[{label}] page error: {e}"))
        page.on("requestfailed", lambda r: self._failed(label, r))
        page.on("response", lambda r: r.status >= 500 and self.problems.append(f"[{label}] HTTP {r.status} {r.url}"))
        return page

    def _ok(self, text):
        return any(a in text for a in self.allowed)

    def _failed(self, label, r):
        failure = r.failure or ""
        # the live stream is cut when a page closes or navigates: not a problem
        if "/api/stream" in r.url and ("ERR_ABORTED" in failure or self.closing):
            return
        if "ERR_ABORTED" in failure and r.resource_type in ("document",):
            return
        self.problems.append(f"[{label}] request failed: {r.url} {failure}")

    def check(self):
        assert not self.problems, "\n".join(self.problems)


@pytest.fixture
def watch():
    w = Watch()
    yield w
    w.closing = True
