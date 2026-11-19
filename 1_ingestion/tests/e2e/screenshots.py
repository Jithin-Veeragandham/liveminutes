"""Screenshots of every main screen, light and dark, at 1440 / 1024 / 768 / 390 px wide, plus the empty,
loading, error, dialog and print states. Also makes contact sheets (a grid per group) for reviewing.

  .venv/bin/python 1_ingestion/tests/e2e/screenshots.py OUT_DIR [--only light|dark]

Runs its own test server (serve.py) with a temporary sessions folder: no real meeting data.
"""

import argparse
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
WIDTHS = [(1440, 900), (1024, 768), (768, 1024), (390, 844)]


def start_server(port, sessions):
    proc = subprocess.Popen([sys.executable, str(HERE / "serve.py"), "--port", str(port), "--sessions", str(sessions)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)
            return proc
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("server did not start")


def post(base, path):
    return json.load(urllib.request.urlopen(urllib.request.Request(base + path, method="POST"), timeout=120))


def settle(page, ms=700):
    page.wait_for_timeout(ms)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--only", choices=["light", "dark"])
    ap.add_argument("--port", type=int, default=8797)
    ap.add_argument("--speed", type=float, default=30, help="demo speed (1 = real time: realistic timestamps, about 5 min)")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    base = f"http://127.0.0.1:{a.port}"
    proc = start_server(a.port, Path(tempfile.mkdtemp(prefix="lm-shots-")))
    shots = []
    try:
        rich = post(base, f"/live/api/demo?speed={a.speed}&until=262&wait=1")  # live, after both action items
        early = post(base, "/live/api/demo?speed=1&until=4")                    # before anyone has said yes
        time.sleep(max(10, 262 / a.speed + 3))
        with sync_playwright() as p:
            browser = p.chromium.launch()
            themes = [a.only] if a.only else ["light", "dark"]
            # some edits by hand, so "Edited by", history and the edit log have content
            seed = browser.new_context()
            org = seed.new_page()
            org.goto(rich["organizer_url"])
            org.wait_for_selector(".action-card")
            org.evaluate("""async () => {
                const cfg = JSON.parse(document.getElementById('lm-config').textContent);
                const call = (path, body, who) => fetch(cfg.api + path, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': cfg.csrf, 'X-LM-Name': who }, body: JSON.stringify(body) }).then(r => r.json());
                const snap = await fetch(cfg.api + '/snapshot').then(r => r.json());
                const a1 = snap.actions.items.find(i => i.id === 'a1');
                await call('/edit', { item: 'a1', base_version: a1.version, changes: { text: 'Ship the offline-sync fix with the three status states to beta, so testers get the weekend on it' } }, 'Maya Chen');
                const s2 = snap.summary.items.find(i => i.id === 's2');
                await call('/edit', { item: 's2', base_version: s2.version, changes: { status: 'approved' } }, 'Daniel Okafor');
                await call('/create', { kind: 'action', fields: { text: 'Tell support about the December 9 launch date', owner: 'Maya Chen', due: '' } }, 'Maya Chen');
            }""")
            seed.close()

            for theme in themes:
                tdir = out / theme
                tdir.mkdir(exist_ok=True)

                def ctx_for(w, hgt, storage=None, **kw):
                    return browser.new_context(viewport={"width": w, "height": hgt}, color_scheme=theme, device_scale_factor=1,
                                               storage_state=storage, reduced_motion="reduce", **kw)

                def snap(page, name, full=False):
                    path = tdir / f"{name}.png"
                    page.screenshot(path=str(path), full_page=full)
                    shots.append((theme, name, path))

                # participant: join once, reuse the cookie
                c = ctx_for(1440, 900)
                pg = c.new_page()
                pg.goto(base + f"/m/{rich['id']}")
                pg.wait_for_selector("input[aria-label='Character 1 of 6']:focus")
                settle(pg, 300)
                snap(pg, "join-code-1440")
                pg.keyboard.type(rich["code"].replace("-", ""))
                pg.wait_for_selector("text=Who are you?")
                settle(pg, 300)
                snap(pg, "join-name-1440")
                pg.get_by_role("listitem").filter(has_text="Priya Raman").click()
                pg.wait_for_selector(".action-card")
                storage = c.storage_state()
                c.close()

                for w, hgt in WIDTHS:
                    c = ctx_for(w, hgt, storage)
                    pg = c.new_page()
                    pg.goto(base + f"/m/{rich['id']}")
                    pg.wait_for_selector(".line")
                    settle(pg, 900)
                    snap(pg, f"participant-{w}")
                    if w == 390:
                        nav = pg.get_by_role("navigation", name="Sections")
                        nav.get_by_role("button", name="Summary").click(); settle(pg); snap(pg, "participant-390-summary")
                        nav.get_by_role("button", name="Actions").click(); settle(pg); snap(pg, "participant-390-actions")
                        nav.get_by_role("button", name="Ask").click(); settle(pg); snap(pg, "participant-390-ask")
                        nav.get_by_role("button", name="More").click(); settle(pg); snap(pg, "participant-390-people")
                        nav.get_by_role("button", name="Transcript").click(); settle(pg)
                        pg.get_by_role("button", name="Share").click(); settle(pg); snap(pg, "dialog-share-390")
                        pg.keyboard.press("Escape")
                    if w == 1440:
                        pg.get_by_role("button", name="Share").click(); settle(pg); snap(pg, "dialog-share-1440")
                        pg.keyboard.press("Escape"); settle(pg, 300)
                        card = pg.locator(".action-card").first
                        card.hover(); settle(pg, 300); snap(pg, "hover-toolbar-1440")
                        card.get_by_role("button", name="Edit (E)").click(); settle(pg); snap(pg, "editing-1440")
                        pg.keyboard.press("Escape")
                        pg.wait_for_selector(".edit-form", state="detached"); settle(pg, 300)
                        card.hover(); card.get_by_role("button", name="History").click(force=True); settle(pg); snap(pg, "drawer-history-1440")
                        pg.keyboard.press("Escape"); settle(pg, 300)
                        pg.keyboard.press("Meta+k"); settle(pg); snap(pg, "dialog-palette-1440")
                        pg.keyboard.press("Escape"); settle(pg, 300)
                        pg.keyboard.press("?"); settle(pg); snap(pg, "dialog-shortcuts-1440")
                        pg.keyboard.press("Escape"); settle(pg, 300)
                        pg.get_by_role("textbox", name="Your question").fill("What did we decide about the launch date?")
                        pg.keyboard.press("Enter"); settle(pg, 1400); snap(pg, "ask-answer-1440")
                        pg.locator("body").click(position={"x": 5, "y": 400})
                        pg.keyboard.press("/"); pg.keyboard.type("sync"); settle(pg); snap(pg, "search-1440")
                        pg.evaluate("() => { window.print = () => {}; }")
                        pg.keyboard.press("Escape")
                        pg.keyboard.press("Meta+k"); pg.keyboard.type("print"); pg.keyboard.press("Enter"); settle(pg)
                        pg.emulate_media(media="print"); settle(pg)
                        snap(pg, "print-1440", full=True)
                        pg.emulate_media(media="screen")
                        c.set_offline(True); settle(pg, 2500); snap(pg, "offline-1440"); c.set_offline(False)
                    c.close()

                    # organizer
                    c = ctx_for(w, hgt)
                    pg = c.new_page()
                    pg.goto(rich["organizer_url"])
                    pg.wait_for_selector(".line")
                    settle(pg, 900)
                    snap(pg, f"organizer-{w}")
                    if w in (1440, 1024):
                        pg.get_by_role("tab", name="Edit log").click(); settle(pg); snap(pg, f"organizer-editlog-{w}")
                        pg.get_by_role("tab", name="Activity").click(); settle(pg); snap(pg, f"organizer-activity-{w}")
                    if w == 1440:
                        pg.get_by_role("button", name="Share").click(); settle(pg); snap(pg, "dialog-share-organizer-1440")
                        pg.keyboard.press("Escape")
                        pg.get_by_role("button", name="Approve minutes").click(); settle(pg); snap(pg, "dialog-approve-1440")
                        pg.keyboard.press("Escape")
                    # home
                    pg.goto(base + "/live")
                    pg.wait_for_selector(".meeting-row")
                    settle(pg)
                    snap(pg, f"home-{w}")
                    if w == 1440:
                        pg.get_by_role("button", name="Send bot to a meeting").click(); settle(pg); snap(pg, "dialog-sendbot-1440")
                        pg.keyboard.press("Escape")
                    # early / empty states
                    pg.goto(early["organizer_url"])
                    pg.wait_for_selector(".panel--transcript .empty")
                    settle(pg)
                    snap(pg, f"empty-organizer-{w}")
                    c.close()

                    if w in (1440, 390):
                        c = ctx_for(w, hgt)
                        pg = c.new_page()
                        pg.goto(base + "/m/bot_does_not_exist"); pg.wait_for_selector("h1"); settle(pg); snap(pg, f"error-notfound-{w}")
                        pg.goto(base + f"/m/{early['id']}"); pg.wait_for_selector("h1"); settle(pg); snap(pg, f"error-notshared-{w}")
                        c.close()
                        # loading: hold the stream so the skeleton stays up
                        c = ctx_for(w, hgt)
                        pg = c.new_page()
                        pg.route("**/api/stream", lambda route: None)
                        pg.goto(rich["organizer_url"]); settle(pg, 900); snap(pg, f"loading-{w}")
                        c.close()
            browser.close()
    finally:
        proc.terminate()
    sheets(out, shots)
    print(f"{len(shots)} screenshots in {out}")


def sheets(out, shots):
    """One labelled grid per theme and group, scaled down, for reviewing many screenshots at once."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return
    groups = {"participant": "participant-", "organizer": "organizer-", "home": "home-", "states": ("empty-", "error-", "loading-", "offline-"),
              "dialogs": ("dialog-", "drawer-", "editing-", "hover-", "ask-", "search-", "join-")}
    for theme in ("light", "dark"):
        for gname, prefix in groups.items():
            imgs = [(n, pth) for t, n, pth in shots if t == theme and n.startswith(prefix)]
            if not imgs:
                continue
            tile_w = 560
            tiles = []
            for name, pth in imgs:
                im = Image.open(pth).convert("RGB")
                scale = tile_w / max(im.width, 1) if im.width > tile_w else min(1.0, 420 / max(im.width, 1)) if im.width < 500 else 1
                im = im.resize((int(im.width * scale), int(im.height * scale)))
                if im.height > 760:
                    im = im.crop((0, 0, im.width, 760))
                tiles.append((name, im))
            cols = 3
            rows = (len(tiles) + cols - 1) // cols
            cell_w, cell_h = tile_w + 20, max(t.height for _, t in tiles) + 44
            sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), (40, 40, 46))
            d = ImageDraw.Draw(sheet)
            for i, (name, im) in enumerate(tiles):
                x, y = (i % cols) * cell_w + 10, (i // cols) * cell_h + 30
                d.text((x, y - 22), name, fill=(235, 235, 240))
                sheet.paste(im, (x, y))
            sheet.save(out / f"sheet-{theme}-{gname}.png")


if __name__ == "__main__":
    main()
