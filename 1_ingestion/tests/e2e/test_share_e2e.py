"""Browser test: a real (not demo) meeting with no public address. The organizer sees a warning before
and after everyone agrees, the bot posts no link, and Share > Make a new code posts it once the tunnel
runs. Attendee is faked by serve.py; the meeting is driven through the real /webhook.

Run from the repo root:  .venv/bin/python -m pytest 1_ingestion/tests -m e2e
"""

import json
import urllib.request

import pytest
from playwright.sync_api import expect

from axe_helper import run_axe

pytestmark = pytest.mark.e2e
WIDE = {"width": 1440, "height": 900}
SID = "bot_e2e_share"
PUBLIC = "https://e2e-share.example.org"


def send(server, path, body):
    req = urllib.request.Request(server.base + path, method="POST", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=30).read()


@pytest.mark.parametrize("theme_name", ["light", "dark"])
def test_no_public_address_warns_then_make_a_new_code_posts_the_link(browser, server, watch, theme_name):
    sid = f"{SID}_{theme_name}"
    send(server, "/live/api/public-url", {"url": ""})

    def bot(trigger, data):  # a webhook from (fake) Attendee
        send(server, "/webhook", {"bot_id": sid, "bot_metadata": {"bot_name": "LiveMinutes - E2E"},
                                  "trigger": trigger, "data": data})
    try:
        for uuid, name in (("p1", "Ann Lee"), ("p2", "Bob Stone")):
            bot("participant_events.join_leave", {"participant_uuid": uuid, "participant_name": name, "event_type": "join"})
        bot("bot.state_change", {"old_state": "joining", "new_state": "joined_recording"})

        ctx = browser.new_context(viewport=WIDE, color_scheme=theme_name, bypass_csp=True, reduced_motion="reduce")
        page = watch.attach(ctx.new_page(), "organizer")
        page.goto(server.base + f"/live/{sid}")
        banner = page.locator(".banner--warning")
        expect(banner).to_contain_text("No public address: when everyone agrees, the participants' link won't be posted")
        problems = run_axe(page, f"{theme_name} organizer, no public address")[0]

        for uuid, name in (("p1", "Ann Lee"), ("p2", "Bob Stone")):
            bot("chat_messages.update", {"sender_uuid": uuid, "sender_name": name, "text": "yes"})
        expect(banner).to_contain_text("weren't posted in the chat: there's no public address")
        chat = json.loads(urllib.request.urlopen(server.base + f"/test/chat/{sid}", timeout=10).read())
        assert chat[-1] == "Thanks, everyone agreed. Recording started."
        assert not [m for m in chat if "/m/" in m]
        problems += run_axe(page, f"{theme_name} organizer, link not posted")[0]

        send(server, "/live/api/public-url", {"url": PUBLIC})   # tunnel.sh starts
        expect(banner).to_contain_text("The tunnel is on now")
        banner.get_by_role("button", name="Share").click()
        dialog = page.get_by_role("dialog", name="Invite people to the live notes")
        expect(dialog).to_be_visible()
        expect(dialog.get_by_label("Post it in the meeting chat")).to_be_checked()
        dialog.get_by_role("button", name="Make a new code").click()
        expect(page.locator(".toast")).to_contain_text("New code posted in the meeting chat")
        chat = json.loads(urllib.request.urlopen(server.base + f"/test/chat/{sid}", timeout=10).read())
        posted = [m for m in chat if "Live notes for everyone:" in m]
        assert len(posted) == 1 and posted[0].startswith(f"New access code. Live notes for everyone: {PUBLIC}/m/{sid} (access code ")
        page.keyboard.press("Escape")
        expect(page.locator(".banner", has_text="posted in the chat")).to_have_count(0)
        ctx.close()
        watch.closing = True
        assert not problems, "\n".join(problems)
        watch.check()
    finally:
        send(server, "/live/api/public-url", {"url": ""})
