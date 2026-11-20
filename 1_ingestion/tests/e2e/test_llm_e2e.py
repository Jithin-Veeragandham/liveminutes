"""Browser test: the LLM's live notes on a real (not demo) meeting page, from a scripted LLM (serve.py --fake-llm,
updates every 3 s). Real items with no "Sample data" label, the status line with its countdown, the organizer's
list of updates with their latency, "Summary update delayed" when the LLM fails, both themes, desktop and phone,
and axe. Screenshots go to E2E_SHOTS (if set), for reading by eye.

Run from the repo root:  .venv/bin/python -m pytest 1_ingestion/tests -m e2e -k llm
"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import expect

from axe_helper import run_axe
from conftest import HERE, free_port

pytestmark = pytest.mark.e2e
WIDE = {"width": 1440, "height": 900}
PHONE = {"width": 390, "height": 844}
SHOTS = os.environ.get("E2E_SHOTS")


class LLMServer:
    def __init__(self, tmp):
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.proc = subprocess.Popen([sys.executable, str(HERE / "serve.py"), "--port", str(self.port),
                                      "--sessions", str(tmp / "sessions"), "--fake-llm", "--notes-interval", "3"],
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for _ in range(100):
            try:
                urllib.request.urlopen(self.base + "/health", timeout=1)
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("test server did not start")

    def send(self, path, body):
        req = urllib.request.Request(self.base + path, method="POST", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=30).read() or b"null")

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


@pytest.fixture(scope="module")
def llm_server(tmp_path_factory):
    s = LLMServer(tmp_path_factory.mktemp("e2e-llm"))
    yield s
    s.stop()


def meeting(server, sid):
    def hook(trigger, data):
        req = urllib.request.Request(server.base + "/webhook", method="POST", headers={"Content-Type": "application/json"},
                                     data=json.dumps({"bot_id": sid, "bot_metadata": {"bot_name": "LiveMinutes"},
                                                      "trigger": trigger, "data": data}).encode())
        urllib.request.urlopen(req, timeout=30).read()
    for uuid, name in (("p1", "Ann Lee"), ("p2", "Bob Stone")):
        hook("participant_events.join_leave", {"participant_uuid": uuid, "participant_name": name, "event_type": "join"})
    hook("bot.state_change", {"old_state": "joining", "new_state": "joined_recording"})
    for uuid, name in (("p1", "Ann Lee"), ("p2", "Bob Stone")):
        hook("chat_messages.update", {"sender_uuid": uuid, "sender_name": name, "text": "yes"})
    server.send("/test/lines", {"sid": sid, "lines": [
        ["Ann Lee", "Okay, let's review the beta. Bob, can you send the release notes to the testers tomorrow?"],
        ["Bob Stone", "Sure, I'll send them tomorrow morning."],
        ["Ann Lee", "Great. So we agree we ship the beta on Friday."],
        ["Bob Stone", "One thing: the launch budget is tight."]]})


def shot(page, name):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"), full_page=False)


@pytest.mark.parametrize("theme_name", ["light", "dark"])
def test_llm_notes_on_the_meeting_page(browser, llm_server, watch, theme_name):
    sid = f"bot_e2e_llm_{theme_name}"
    llm_server.send("/test/llm", {"fail": None, "delay": 0})
    meeting(llm_server, sid)
    ctx = browser.new_context(viewport=WIDE, color_scheme=theme_name, bypass_csp=True, reduced_motion="reduce")
    page = watch.attach(ctx.new_page(), "organizer")
    page.goto(llm_server.base + f"/live/{sid}")
    summary = page.locator(".panel--summary")
    expect(summary.locator(".item").first).to_contain_text("Ann Lee opened the beta review", timeout=20_000)
    expect(summary.locator(".panel-head .chip--warning")).to_be_hidden()          # no "Sample data" label
    expect(page.locator(".panel--actions .panel-head .chip--warning")).to_be_hidden()
    expect(summary.locator(".item--decision")).to_contain_text("agreed to ship the beta")
    card = page.locator(".action-card").filter(has_text="Send the beta release notes")
    expect(card).to_contain_text("Bob Stone")                                       # "Bob" matched to the person
    status = summary.locator(".llm-status")
    expect(status).to_contain_text("Updated")
    expect(status).to_contain_text("next in")
    llm_server.send("/test/lines", {"sid": sid, "lines": [["Ann Lee", "I'll draft a short launch email for the customers."]]})
    expect(page.locator(".action-card").filter(has_text="Draft the launch email")).to_be_visible(timeout=15_000)
    problems = run_axe(page, f"{theme_name} organizer with live notes")[0]
    shot(page, f"llm-organizer-{theme_name}-1440")
    page.get_by_role("tab", name="Activity").click()
    box = page.locator(".llm-box")
    expect(box).to_be_visible()
    box.locator("summary").first.click()
    expect(box.locator(".llm-call--ok").first).to_contain_text("Update")
    expect(box.locator(".llm-lat").first).to_have_text(re.compile(r"^\d+\.\d s$"))
    problems += run_axe(page, f"{theme_name} organizer activity with live notes")[0]
    shot(page, f"llm-activity-{theme_name}-1440")
    # the LLM fails: the page keeps the last notes and says the update is delayed
    llm_server.send("/test/llm", {"fail": "Gemini HTTP 429: quota exceeded"})
    llm_server.send("/test/lines", {"sid": sid, "lines": [["Ann Lee", "Let's also check the venue for the launch."]]})
    page.get_by_role("tab", name="Activity").click()
    expect(status).to_contain_text("Summary update delayed", timeout=15_000)
    expect(summary.locator(".item").first).to_contain_text("Ann Lee opened the beta review")
    shot(page, f"llm-delayed-{theme_name}-1440")
    problems += run_axe(page, f"{theme_name} organizer, update delayed")[0]
    llm_server.send("/test/llm", {"fail": None})
    expect(status).to_contain_text("Updated", timeout=15_000)
    # a participant on a phone
    ctx.close()
    pctx = browser.new_context(viewport=PHONE, color_scheme=theme_name, bypass_csp=True, reduced_motion="reduce")
    phone = watch.attach(pctx.new_page(), "phone")
    snap = json.loads(urllib.request.urlopen(llm_server.base + f"/live/{sid}/api/snapshot", timeout=10).read())
    code = snap["session"]["share"]["code"]
    phone.goto(llm_server.base + f"/m/{sid}")
    expect(phone.get_by_role("textbox", name="Character 1 of 6")).to_be_focused()
    phone.keyboard.type(code.replace("-", ""))
    phone.get_by_role("listitem").filter(has_text="Bob Stone").click()
    tabbar = phone.get_by_role("navigation", name="Sections")
    tabbar.get_by_role("button", name="Summary").click()
    expect(phone.locator(".panel--summary .llm-status")).to_contain_text("Updated", timeout=15_000)
    shot(phone, f"llm-participant-summary-{theme_name}-390")
    problems += run_axe(phone, f"{theme_name} participant phone with live notes")[0]
    tabbar.get_by_role("button", name=re.compile("^Actions")).click()
    expect(phone.locator(".panel--actions .llm-status--compact")).to_be_visible()
    shot(phone, f"llm-participant-actions-{theme_name}-390")
    pctx.close()
    watch.closing = True
    assert not problems, "\n".join(problems)
    watch.check()
