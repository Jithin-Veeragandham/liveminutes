"""Browser tests (Playwright + Chromium) for the participants' live view and the organizer view.

Run from the repo root:
  .venv/bin/python -m playwright install chromium            (once)
  .venv/bin/python -m pytest 1_ingestion/tests -m e2e

Every test fails on any console error, uncaught page error, failed request or HTTP 5xx. A few HTTP
4xx answers are part of the flows on purpose (a wrong code is 401, a conflicting save is 409); only
the tests that cause them allow them.
"""

import re
import time

import pytest
from playwright.sync_api import expect

from axe_helper import run_axe

pytestmark = pytest.mark.e2e
WIDE = {"width": 1440, "height": 900}


def participant(browser, server, watch, demo, name, label, viewport=WIDE):
    """A participant's browser: open the link, type the code, pick a name."""
    ctx = browser.new_context(viewport=viewport)
    page = watch.attach(ctx.new_page(), label)
    page.goto(server.base + f"/m/{demo['id']}")
    expect(page.get_by_role("textbox", name="Character 1 of 6")).to_be_focused()
    page.keyboard.type(demo["code"].replace("-", "").lower())
    expect(page.get_by_role("heading", name="Who are you?")).to_be_visible()
    page.get_by_role("listitem").filter(has_text=name).click()
    expect(page.locator(".meeting-title")).to_have_text("Orbit 2.0 launch sync")
    return ctx, page


def organizer(browser, server, watch, demo, viewport=WIDE):
    ctx = browser.new_context(viewport=viewport)
    page = watch.attach(ctx.new_page(), "organizer")
    page.goto(demo["organizer_url"])
    expect(page.locator(".meeting-title")).to_have_text("Orbit 2.0 launch sync")
    return ctx, page


def card(page, text):
    return page.locator(".action-card").filter(has_text=text).first


def test_two_participants_live_edit_conflict_undo_approve_export_print(browser, server, watch, tmp_path):
    watch.allowed = ["status of 409"]  # the conflicting save is refused with 409 on purpose
    demo = server.demo(speed=8)
    assert re.fullmatch(r"[A-Z2-9]{3}-[A-Z2-9]{3}", demo["code"]) and not set(demo["code"]) & set("01OIL")
    octx, org = organizer(browser, server, watch, demo)
    mctx, maya = participant(browser, server, watch, demo, "Maya Chen", "maya")
    dctx, dan = participant(browser, server, watch, demo, "Daniel Okafor", "daniel")

    # live lines arrive in every browser, through the real consent gate
    for page in (org, maya, dan):
        expect(page.locator(".line").nth(4)).to_be_visible(timeout=20_000)
    expect(maya.locator(".me-btn")).to_contain_text("Maya")

    # the late joiner pauses everyone, then recording resumes
    expect(org.locator(".state-chip")).to_contain_text("waiting for Leo", timeout=25_000)
    expect(maya.locator(".paused-bar")).to_be_visible()
    expect(org.locator(".live-pill")).to_be_visible(timeout=15_000)

    # the first action item appears for everyone, with source chips that jump to the transcript
    item = "Ship the offline-sync fix"
    for page in (maya, dan, org):
        expect(card(page, item)).to_be_visible(timeout=25_000)
    card(maya, item).locator(".src-chip").first.click()
    expect(maya.locator('.line[aria-current="true"]')).to_be_visible()

    # both open the same item; Maya saves first; Daniel's save gets the conflict dialog
    for page in (maya, dan):
        c = card(page, item)
        c.hover()
        c.get_by_role("button", name="Edit (E)").click()
        expect(page.locator(".action-card textarea")).to_be_visible()
    expect(dan.locator(".action-card .form-presence")).to_contain_text("Maya is editing this too", timeout=10_000)
    expect(card(org, item).locator(".editing-hint")).to_contain_text("is editing")
    maya.locator(".action-card textarea").fill("Ship the offline-sync fix to beta by Friday")
    maya.get_by_role("button", name="Save").click()
    expect(maya.locator(".toast").filter(has_text="Saved")).to_be_visible()
    dan.locator(".action-card textarea").fill("Daniel's own wording for the sync fix")
    dan.get_by_role("button", name="Save").click()
    dialog = dan.get_by_role("dialog", name="Someone changed this while you were editing")
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text("Their version · Maya Chen")
    expect(dialog).to_contain_text("Daniel's own wording")
    dialog.get_by_role("button", name="Keep theirs").click()
    expect(card(dan, "to beta by Friday")).to_be_visible()

    # Undo (a new log entry) brings the old text back, live for everyone
    card(maya, "to beta by Friday").hover()
    card(maya, "to beta by Friday").get_by_role("button", name="History").click()
    drawer = maya.get_by_role("dialog", name=re.compile("History"))
    expect(drawer).to_contain_text("Maya Chen edited this")
    drawer.get_by_role("button", name="Undo this change").click()
    for page in (maya, dan, org):
        expect(card(page, "with the three sync status states")).to_be_visible()

    # approve the minutes as Maya; everyone sees it
    maya.get_by_role("button", name="Approve minutes").click()
    maya.get_by_role("dialog", name="Approve the minutes?").get_by_role("button", name="Approve minutes").click()
    for page in (maya, dan, org):
        expect(page.locator(".minutes-bar")).to_contain_text("Minutes approved", timeout=10_000)
        expect(page.locator(".minutes-bar")).to_contain_text("Maya Chen")

    # the organizer's edit log shows everyone's changes and filters by person
    org.get_by_role("tab", name="Edit log").click()
    log = org.locator(".panel--editlog")
    expect(log.locator(".log-entry")).to_have_count(3, timeout=10_000)
    log.get_by_role("button", name="Everyone").click()
    org.get_by_role("menuitemradio", name="Maya Chen").click()
    expect(log.locator(".log-entry")).to_have_count(3)
    expect(log).to_contain_text("approved the minutes")

    # export Markdown
    with org.expect_download() as dl:
        org.locator(".minutes-bar").get_by_role("button", name="Export").click()
    text = open(dl.value.path(), encoding="utf-8").read()
    for part in ("# Orbit 2.0 launch sync", "**Attendees:** Maya Chen, Daniel Okafor, Priya Raman, Leo Martins",
                 "## Decisions", "Launch moves from December 2 to Tuesday, December 9", "| Daniel Okafor |", "Approved by Maya Chen", "## Edit history", "Demo meeting"):
        assert part in text, part

    # print preview looks like minutes (and makes a real PDF)
    org.evaluate("() => { window.print = () => {}; }")
    org.keyboard.press("Control+k" if not org.evaluate("navigator.platform").startswith("Mac") else "Meta+k")
    org.get_by_role("combobox", name="Search commands").fill("print")
    org.keyboard.press("Enter")
    org.emulate_media(media="print")
    minutes = org.locator("#print-minutes")
    expect(minutes).to_be_visible()
    expect(minutes.locator(".pm-title")).to_have_text("Orbit 2.0 launch sync")
    expect(minutes).to_contain_text("Approved by Maya Chen")
    expect(minutes.locator(".pm-table tbody tr")).to_have_count(2, timeout=10_000)
    expect(org.locator(".app")).to_be_hidden()
    pdf = org.pdf(path=str(tmp_path / "minutes.pdf"))
    assert pdf[:4] == b"%PDF" and len(pdf) > 10_000
    org.emulate_media(media="screen")

    for c in (mctx, dctx, octx):
        c.close()
    watch.closing = True
    watch.check()


def test_wrong_code_rate_limit_and_error_screens(browser, server, watch):
    watch.allowed = ["status of 401", "status of 429"]
    demo = server.demo(speed=20)
    ctx = browser.new_context(viewport=WIDE)
    page = watch.attach(ctx.new_page(), "guess")
    page.goto(server.base + f"/m/{demo['id']}")
    expect(page.get_by_role("textbox", name="Character 1 of 6")).to_be_focused()
    page.keyboard.type("AAAAAA")
    expect(page.locator(".form-msg")).to_contain_text("isn't right")
    for _ in range(6):
        page.keyboard.type("BBBBBB")
        time.sleep(0.15)
    expect(page.locator(".form-msg")).to_contain_text("Too many tries")
    page.goto(server.base + "/m/bot_does_not_exist")
    expect(page.get_by_role("heading", name="Meeting not found")).to_be_visible()
    ctx.close()
    watch.closing = True
    watch.check()


def test_keyboard_palette_theme_and_reconnect(browser, server, watch):
    demo = server.demo(speed=10)
    ctx, page = participant(browser, server, watch, demo, "Priya Raman", "priya")
    expect(page.locator(".line").nth(3)).to_be_visible(timeout=20_000)
    page.locator("body").click(position={"x": 5, "y": 300})
    page.keyboard.press("/")
    expect(page.get_by_role("searchbox", name="Search the transcript")).to_be_focused()
    page.keyboard.type("beta")
    expect(page.locator(".line mark").first).to_be_visible()
    page.keyboard.press("Escape")
    page.keyboard.press("j")
    page.keyboard.press("j")
    expect(page.locator('.line[aria-current="true"]')).to_have_count(1)
    page.keyboard.press("?")
    expect(page.get_by_role("dialog", name="Keyboard shortcuts")).to_be_visible()
    page.keyboard.press("Escape")
    page.keyboard.press("Meta+k" if page.evaluate("navigator.platform").startswith("Mac") else "Control+k")
    expect(page.get_by_role("combobox", name="Search commands")).to_be_focused()
    page.keyboard.type("dark")
    page.keyboard.press("Enter")
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    # the choice survives a reload, and the theme is set before the first paint (no flash)
    page.reload()
    assert page.evaluate("document.documentElement.getAttribute('data-theme')") == "dark"
    expect(page.locator(".meeting-title")).to_be_visible()
    # summary: select with j, edit with e
    expect(page.locator(".panel--summary .item").first).to_be_visible(timeout=20_000)
    page.locator(".panel--summary .item").first.click()
    page.keyboard.press("e")
    expect(page.locator(".panel--summary textarea")).to_be_focused()
    page.keyboard.press("Escape")
    expect(page.locator(".panel--summary textarea")).to_have_count(0)
    # connection lost and back
    ctx.set_offline(True)
    expect(page.locator(".banner").filter(has_text=re.compile("offline|Reconnecting"))).to_be_visible(timeout=10_000)
    ctx.set_offline(False)
    expect(page.locator(".toast").filter(has_text="Back online")).to_be_visible(timeout=20_000)
    page.evaluate("localStorage.setItem('lm-theme', 'system')")
    ctx.close()
    watch.closing = True
    watch.problems = [p for p in watch.problems if "ERR_INTERNET_DISCONNECTED" not in p]  # caused by going offline
    watch.check()


@pytest.mark.parametrize("width,height", [(1440, 900), (1024, 768), (768, 1024), (390, 844)])
def test_responsive_layouts(browser, server, watch, width, height):
    demo = server.demo(speed=10)
    ctx, page = participant(browser, server, watch, demo, "Priya Raman", "priya", viewport={"width": width, "height": height})
    expect(page.locator(".line").first).to_be_visible(timeout=20_000)
    if width >= 1200:
        expect(page.locator(".panel--summary")).to_be_visible()
        expect(page.locator(".panel--ask")).to_be_visible()
    elif width >= 768:
        expect(page.locator(".panel--summary")).to_be_visible()
        page.get_by_role("tab", name="Ask").click()
        expect(page.locator(".panel--ask")).to_be_visible()
    else:
        tabbar = page.get_by_role("navigation", name="Sections")
        expect(tabbar).to_be_visible()
        expect(page.locator(".panel--summary")).to_be_hidden()
        tabbar.get_by_role("button", name="Summary").click()
        expect(page.locator(".panel--summary")).to_be_visible()
        expect(page.locator(".panel--transcript")).to_be_hidden()
        tabbar.get_by_role("button", name=re.compile("^Actions")).click()
        expect(page.locator(".panel--actions")).to_be_visible()
    overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert overflow <= 0, f"horizontal scroll at {width}px"
    ctx.close()
    watch.closing = True
    watch.check()


@pytest.mark.parametrize("theme_name", ["light", "dark"])
def test_accessibility_axe(browser, server, watch, theme_name):
    watch.allowed = ["status of 401"]
    demo = server.demo(speed=12)
    problems = []
    # bypass_csp only lets the test inject axe-core; the page itself is unchanged
    # reduced motion: axe measures colors on a still page, not half-way through a fade-in
    ctx = browser.new_context(viewport=WIDE, color_scheme=theme_name, bypass_csp=True, reduced_motion="reduce")
    page = watch.attach(ctx.new_page(), "axe")
    page.goto(server.base + f"/m/{demo['id']}")
    expect(page.get_by_role("textbox", name="Character 1 of 6")).to_be_focused()
    problems += run_axe(page, f"{theme_name} code screen")[0]
    page.keyboard.type(demo["code"].replace("-", ""))
    expect(page.get_by_role("heading", name="Who are you?")).to_be_visible()
    problems += run_axe(page, f"{theme_name} name screen")[0]
    page.get_by_role("listitem").filter(has_text="Maya Chen").click()
    expect(page.locator(".action-card").first).to_be_visible(timeout=30_000)
    problems += run_axe(page, f"{theme_name} meeting (participant)")[0]
    page.get_by_role("button", name="Share").click()
    expect(page.get_by_role("dialog", name="Invite people to the live notes")).to_be_visible()
    problems += run_axe(page, f"{theme_name} share dialog")[0]
    page.keyboard.press("Escape")
    page.get_by_role("tab", name="People").click()
    problems += run_axe(page, f"{theme_name} people tab")[0]
    org = watch.attach(ctx.new_page(), "axe-org")
    org.goto(demo["organizer_url"])
    expect(org.locator(".line").first).to_be_visible(timeout=20_000)
    problems += run_axe(org, f"{theme_name} meeting (organizer)")[0]
    org.get_by_role("tab", name="Edit log").click()
    problems += run_axe(org, f"{theme_name} edit log")[0]
    org.goto(server.base + "/live")
    expect(org.get_by_role("heading", name="Meetings")).to_be_visible()
    expect(org.locator(".meeting-row").first).to_be_visible()
    problems += run_axe(org, f"{theme_name} organizer home")[0]
    org.goto(server.base + "/m/bot_nope")
    expect(org.get_by_role("heading", name="Meeting not found")).to_be_visible()
    problems += run_axe(org, f"{theme_name} error screen")[0]
    ctx.close()
    watch.closing = True
    assert not problems, "\n".join(problems)
    watch.check()


def test_page_renders_and_falls_back_to_polling_when_the_stream_never_arrives(browser, server, watch):
    """Like a Cloudflare quick tunnel: the event stream never delivers. The page must still show the
    meeting (from GET /snapshot), switch to the long poll, stay live, and say so."""
    demo = server.demo(speed=8)
    ctx = browser.new_context(viewport=WIDE)
    page = watch.attach(ctx.new_page(), "blocked")
    page.route("**/api/stream", lambda route: None)  # never answered
    page.goto(server.base + f"/m/{demo['id']}")
    expect(page.get_by_role("textbox", name="Character 1 of 6")).to_be_focused()
    page.keyboard.type(demo["code"].replace("-", ""))
    page.get_by_role("listitem").filter(has_text="Priya Raman").click()
    expect(page.locator(".meeting-title")).to_have_text("Orbit 2.0 launch sync", timeout=3000)  # from the GET
    expect(page.locator(".conn")).to_contain_text("Live · polling", timeout=8000)
    before = page.locator(".line").count()
    expect(page.locator(".line").nth(before)).to_be_visible(timeout=10_000)   # new lines keep arriving
    octx, org = organizer(browser, server, watch, demo)
    expect(org.locator(".panel--summary .item").first).to_be_visible(timeout=20_000)
    org.locator(".panel--summary .item").first.click()
    org.keyboard.press("e")
    org.locator(".panel--summary textarea").fill("Edited by the organizer while Priya polls")
    org.keyboard.press("Meta+Enter" if org.evaluate("navigator.platform").startswith("Mac") else "Control+Enter")
    expect(page.locator(".panel--summary")).to_contain_text("Edited by the organizer while Priya polls", timeout=5000)
    octx.close()
    ctx.close()
    watch.closing = True
    watch.check()
