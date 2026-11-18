"""Tests for fetch_data.download(): a stalled connection must time out and be retried, never hang.

A local fake server plays the AMI server's bad days: it accepts the connection and sends nothing, sends part
of the file and stops, or closes early. No internet needed.
Run from the repo root:  .venv/bin/python -m unittest discover -s 5_data/ami -p "test_*.py" -v
"""

import os
import sys
import tempfile
import threading
import time
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_data  # noqa: E402

BODY = bytes(range(256)) * 400  # 100 KB of known bytes


class FakeServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False  # don't wait for the handlers that are stalling on purpose

    def __init__(self, plan):
        """plan: what to do for each request, in order ("stall", "stall_body", "short", "ok"); the last repeats."""
        self.plan, self.requests = list(plan), 0
        super().__init__(("127.0.0.1", 0), Handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        server = self.server
        mode = server.plan[min(server.requests, len(server.plan) - 1)]
        server.requests += 1
        if mode == "stall":            # connection accepted, nothing ever sent
            time.sleep(30)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        if mode == "stall_body":       # headers and a little data, then nothing
            self.wfile.write(BODY[:1000])
            self.wfile.flush()
            time.sleep(30)
        elif mode == "short":          # closes before the end
            self.wfile.write(BODY[: len(BODY) // 2])
        else:
            self.wfile.write(BODY)


class DownloadTimeoutTest(unittest.TestCase):
    def setUp(self):
        self.saved = (getattr(fetch_data, "DOWNLOAD_TIMEOUT_S", None), fetch_data.DOWNLOAD_TRIES, fetch_data.time)
        fetch_data.DOWNLOAD_TIMEOUT_S = 0.5
        self.waits = []
        fetch_data.time = types.SimpleNamespace(sleep=self.waits.append)  # fetch_data doesn't really wait between tries
        self.dir = tempfile.mkdtemp(prefix="lm_fetch_")
        self.path = os.path.join(self.dir, "ami.zip")

    def tearDown(self):
        fetch_data.DOWNLOAD_TIMEOUT_S, fetch_data.DOWNLOAD_TRIES, fetch_data.time = self.saved
        if hasattr(self, "server"):
            self.server.shutdown()
            self.server.server_close()

    def serve(self, *plan):
        self.server = FakeServer(plan)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.server.server_address[1]}/ami.zip"

    def download(self, url):
        t0 = time.monotonic()
        fetch_data.download(url, self.path)
        return time.monotonic() - t0

    def assert_complete(self):
        self.assertEqual(Path(self.path).read_bytes(), BODY)
        self.assertFalse(os.path.exists(self.path + ".part"))

    def test_a_connection_that_sends_nothing_times_out_and_is_retried(self):
        took = self.download(self.serve("stall", "ok"))
        self.assert_complete()
        self.assertEqual(self.server.requests, 2)
        self.assertEqual(self.waits, [2])  # the usual wait before try 2
        self.assertLess(took, 5)           # 0.5 s timeout, not the 30 s stall (or forever)

    def test_a_download_that_stops_half_way_times_out_and_is_retried(self):
        self.download(self.serve("stall_body", "ok"))
        self.assert_complete()
        self.assertEqual(self.server.requests, 2)

    def test_a_download_that_ends_early_is_retried(self):
        self.download(self.serve("short", "ok"))
        self.assert_complete()
        self.assertEqual(self.server.requests, 2)

    def test_gives_up_after_the_last_try_instead_of_hanging(self):
        fetch_data.DOWNLOAD_TRIES = 3
        url = self.serve("stall")
        t0 = time.monotonic()
        with self.assertRaises(SystemExit) as stop:
            fetch_data.download(url, self.path)
        self.assertLess(time.monotonic() - t0, 6)
        self.assertIn("download failed 3 times", str(stop.exception))
        self.assertEqual(self.server.requests, 3)
        self.assertFalse(os.path.exists(self.path) or os.path.exists(self.path + ".part"))


if __name__ == "__main__":
    unittest.main()
