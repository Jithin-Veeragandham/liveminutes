"""Through a REAL Cloudflare quick tunnel (skipped without cloudflared, or with LM_NO_TUNNEL=1).

Quick tunnels don't pass Server-Sent Events on, which once left the participant page on "Loading…".
This starts a test server, a quick tunnel to it, a demo meeting, and two participants who join through
the tunnel's https address: lines must arrive within 10 s, and one participant's edit must show up for
the other.  Run:  .venv/bin/python -m pytest 1_ingestion/tests -m e2e -k tunnel
"""

import os
import re
import shutil
import subprocess
import time
import urllib.request

import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.e2e,
              pytest.mark.skipif(not shutil.which("cloudflared") or os.environ.get("LM_NO_TUNNEL"),
                                 reason="needs cloudflared (brew install cloudflared); LM_NO_TUNNEL=1 skips it")]


@pytest.fixture(scope="module")
def tunnel(server):
    proc = subprocess.Popen(["cloudflared", "tunnel", "--no-autoupdate", "--url", server.base],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    url, deadline = None, time.time() + 60
    while time.time() < deadline and url is None:
        line = proc.stdout.readline()
        m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line or "")
        url = m[0] if m else None
    if url is None:
        proc.kill()
        pytest.skip("cloudflared started but gave no tunnel address (no internet?)")
    req = urllib.request.Request(server.base + "/live/api/public-url", method="POST", data=f'{{"url": "{url}"}}'.encode(),
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10)
    for _ in range(90):  # a new quick tunnel takes a few seconds to answer
        try:
            if urllib.request.urlopen(url + "/", timeout=10).status == 200:
                break
        except OSError:
            time.sleep(1)
    yield url
    proc.terminate()


def join(browser, url, demo, name, watch, label):
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    page = watch.attach(ctx.new_page(), label)
    page.goto(f"{url}/m/{demo['id']}", timeout=60_000)
    expect(page.get_by_role("textbox", name="Character 1 of 6")).to_be_focused(timeout=30_000)
    page.keyboard.type(demo["code"].replace("-", ""))
    page.get_by_role("listitem").filter(has_text=name).click(timeout=30_000)
    expect(page.locator(".meeting-title")).to_have_text("Orbit 2.0 launch sync", timeout=15_000)
    return ctx, page


def test_participants_through_a_real_quick_tunnel(browser, server, tunnel, watch):
    demo = server.demo(speed=6)
    assert demo["participant_url"].startswith(tunnel)          # the bot's link uses the tunnel address
    assert urllib.request.urlopen(tunnel + "/", timeout=20).read().find(b"Open the link from your meeting chat") > 0
    for path in ("/live", "/live/api/meetings", "/live/api/sessions", f"/live/{demo['id']}"):
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(tunnel + path, timeout=20)
        assert e.value.code == 404, path
    actx, ann = join(browser, tunnel, demo, "Maya Chen", watch, "maya-via-tunnel")
    bctx, bob = join(browser, tunnel, demo, "Daniel Okafor", watch, "daniel-via-tunnel")
    # live lines arrive within 10 s, for both
    for page in (ann, bob):
        page.get_by_role("navigation", name="Sections").get_by_role("button", name="Transcript").click()
        before = page.locator(".line").count()
        expect(page.locator(".line").nth(before)).to_be_visible(timeout=10_000)
    print("connection state through the tunnel:", ann.locator(".conn").text_content() or "live (stream)")
    # Maya edits the first summary point; Daniel sees it
    nav = ann.get_by_role("navigation", name="Sections")
    nav.get_by_role("button", name="Summary").click()
    item = ann.locator(".panel--summary .item").first
    expect(item).to_be_visible(timeout=20_000)
    item.click()
    item.get_by_role("button", name="Edit (E)").click()
    ann.locator(".panel--summary textarea").fill("Edited through the Cloudflare tunnel")
    ann.get_by_role("button", name="Save").click()
    bob.get_by_role("navigation", name="Sections").get_by_role("button", name="Summary").click()
    expect(bob.locator(".panel--summary")).to_contain_text("Edited through the Cloudflare tunnel", timeout=10_000)
    actx.close()
    bctx.close()
    watch.closing = True
    watch.check()
