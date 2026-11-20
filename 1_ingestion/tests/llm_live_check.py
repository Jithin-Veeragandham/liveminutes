"""Real LLM check: the demo meeting's transcript through the REAL live-notes pipeline (notes.py ->
2_summarization/llm_summarizer.py -> Gemini), into the real meeting page, with real edits and an approval.
Needs GEMINI_API_KEY (and LIVEMINUTES_LLM_PROVIDER=gemini) in the repo's .env. Makes about 10 LLM calls.

    .venv/bin/python 1_ingestion/tests/llm_live_check.py [--shots DIR] [--keep]

Checks (prints PASS / FAIL for each, and every update's latency):
  1. after the first update: summary and action items in the contract format, no "Sample data" label, owners are
     people who said yes (or ""), due dates YYYY-MM-DD (or ""), source lines exist
  2. an edited summary item, an approved action item and a deleted one stay as people left them after the next run
  3. approved minutes stay frozen through the next run
  4. a task asked at the end of one update and accepted in the next one is kept (the split chunk case)
With --shots DIR it also saves screenshots of the page (organizer desktop, participant phone; light and dark).
Nothing is written to 1_ingestion/sessions/: the meetings live in a temporary folder (--keep prints where).
"""

import argparse
import asyncio
import json
import re
import statistics
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import settings  # noqa: E402

settings.SESSIONS_DIR = Path(tempfile.mkdtemp(prefix="lm_llm_check_"))
settings.UI_SECRET = "llm-check"
settings.PUBLIC_BASE_URL = ""

import aiohttp  # noqa: E402
from aiohttp import web  # noqa: E402

import bridge  # noqa: E402
import live_view  # noqa: E402
import ui  # noqa: E402
from session import now_ms  # noqa: E402
from ui import access  # noqa: E402

SCRIPT = json.loads((HERE.parent / "ui" / "fixtures" / "demo_meeting.json").read_text())
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""), flush=True)


class CheckBridge(bridge.Bridge):
    """The real bridge with Attendee faked (no bot): chat messages are just kept."""
    CHAT_GAP_S, CHAT_GAP_PER_CHAR_S, CHAT_GAP_MAX_S = 0.0, 0.0, 0.0

    async def attendee(self, s, action, payload=None):
        return 200


async def consented(b, sid, names):
    s = b.session(sid)
    for n, name in enumerate(names, 1):
        await b.on_join_leave(s, {"participant_uuid": f"u{n}", "participant_name": name, "event_type": "join"})
    await b.on_bot_state(s, {"new_state": "joined_recording"})
    for n, name in enumerate(names, 1):
        await b.on_chat(s, {"text": "yes", "sender_uuid": f"u{n}", "sender_name": name})
    return s


async def update(b, s, rows):
    """Add transcript pieces said in the past, then run one notes update now (one real LLM call)."""
    s.lines += rows
    s.touch()
    b.notes.state(s).next_due_ms = now_ms() - 1
    await b.notes.maybe_update(s)
    h = b.notes.state(s).history[-1]
    print(f"    update {h.get('part')}: {h.get('status')} in {h.get('seconds')} s, {h.get('lines')} lines, "
          f"{h.get('input_tokens')} -> {h.get('output_tokens')} tokens" + (f", error: {h.get('error')}" if h.get("error") else ""),
          flush=True)
    return h


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--shots", help="save screenshots of the page here")
    p.add_argument("--keep", action="store_true", help="print the temporary sessions folder and keep the page up")
    a = p.parse_args()

    async with aiohttp.ClientSession() as http:
        b = CheckBridge(http, consent_required=True)
        if not b.notes.enabled:
            sys.exit(f"live notes are off: {b.notes.reason}")
        if b.notes.provider.name != "gemini":
            sys.exit(f"this check is for Gemini (the provider the bridge image runs), not {b.notes.provider.name}: "
                     "set LIVEMINUTES_LLM_PROVIDER=gemini and GEMINI_API_KEY in the repo's .env")
        print(f"LLM: {b.notes.model}", flush=True)
        app = web.Application()
        live_view.add_routes(app, b)
        ui.setup(app, b)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        port = 8791
        await web.TCPSite(runner, "127.0.0.1", port).start()
        base = f"http://127.0.0.1:{port}"
        await asyncio.sleep(0.2)

        people = SCRIPT["people"]
        sid = "bot_llmcheck"
        s = await consented(b, sid, [people[k] for k in ("maya", "daniel", "priya", "leo")])
        t0 = now_ms() - 600_000                      # the meeting started 10 min ago: every line is settled
        s.t0_ms = t0
        says = [e for e in SCRIPT["events"] if "say" in e]
        rows = [(t0 + int(e["t"] * 1000), t0 + int((e["t"] + e["dur"]) * 1000), people[e["say"]], e["text"]) for e in says]
        thirds = [rows[: len(rows) // 3], rows[len(rows) // 3: 2 * len(rows) // 3], rows[2 * len(rows) // 3:]]
        latencies = []

        async def snapshot(path=f"/live/{sid}/api/snapshot", headers=None):
            async with http.get(base + path, headers=headers or {}) as r:
                return await r.json()

        print("1. first update", flush=True)
        h = await update(b, s, thirds[0])
        latencies.append(h.get("seconds"))
        d = await snapshot()
        names = set(d["people"] and [x["name"] for x in d["people"] if x["decision"] == "yes"])
        line_ids = {x["id"] for x in d["lines"]}
        summ, acts = d["summary"], d["actions"]
        check("summary items, no sample label", summ["items"] and summ["sample"] is False and summ["label"] is None,
              f"{len(summ['items'])} points")
        check("action items, no sample label", acts["sample"] is False and acts["label"] is None, f"{len(acts['items'])} items")
        check("ids look like llm-s / llm-a", all(i["id"].startswith("llm-s") for i in summ["items"])
              and all(i["id"].startswith("llm-a") for i in acts["items"]))
        check("owners are people who said yes, or unassigned", all(i["owner"] in names | {""} for i in acts["items"]),
              ", ".join(sorted({i["owner"] or "-" for i in acts["items"]})))
        check("due dates are YYYY-MM-DD or empty", all(i["due"] == "" or DATE.match(i["due"]) for i in acts["items"]),
              ", ".join(f"{i['due_text']!r}->{i['due'] or '-'}" for i in acts["items"] if i.get("due_text")))
        check("source lines exist", all(set(i["source_line_ids"]) <= line_ids for i in summ["items"] + acts["items"]))
        check("Ask still says sample data", d["ask"]["sample"] is True)

        print("2. people edit, approve and delete; then the next update", flush=True)
        code = access.CODES.get(sid) or access.create_code(sid)
        async with http.post(base + f"/m/{sid}/api/code", json={"code": code}) as r:
            csrf = (await r.json())["csrf"]
        async with http.post(base + f"/m/{sid}/api/name", json={"name": people["maya"]}, headers={"X-CSRF-Token": csrf}) as r:
            csrf = (await r.json())["csrf"]
        hdr = {"X-CSRF-Token": csrf}
        first_summary = summ["items"][0]
        edited_text = "EDITED BY MAYA: " + first_summary["text"]
        async with http.post(base + f"/m/{sid}/api/edit", headers=hdr, json={"item": first_summary["id"],
                             "base_version": first_summary["version"], "changes": {"text": edited_text}}) as r:
            check("edit saved", r.status == 200)
        approved_action = acts["items"][0] if acts["items"] else None
        deleted_action = acts["items"][1] if len(acts["items"]) > 1 else None
        if approved_action:
            async with http.post(base + f"/m/{sid}/api/edit", headers=hdr, json={"item": approved_action["id"],
                                 "base_version": approved_action["version"], "changes": {"status": "approved"}}) as r:
                check("action item approved", r.status == 200)
        if deleted_action:
            async with http.post(base + f"/m/{sid}/api/delete", headers=hdr,
                                 json={"item": deleted_action["id"], "base_version": deleted_action["version"]}) as r:
                check("action item deleted", r.status == 200)
        h = await update(b, s, thirds[1])
        latencies.append(h.get("seconds"))
        d = await snapshot()
        by = {i["id"]: i for i in d["summary"]["items"] + d["actions"]["items"]}
        check("edited summary item kept", by.get(first_summary["id"], {}).get("text") == edited_text)
        if approved_action:
            kept = by.get(approved_action["id"], {})
            check("approved action item kept", kept.get("status") == "approved" and kept.get("text") == approved_action["text"])
        if deleted_action:
            check("deleted action item stays deleted", deleted_action["id"] not in by)
        llm_after = b.notes.state(s).notes
        check("the LLM really ran again", h.get("status") == "ok" and llm_after is not None)

        print("3. approve the minutes; then the next update", flush=True)
        d = await snapshot()
        async with http.post(base + f"/m/{sid}/api/approve", headers=hdr, json={"base_version": d["session"]["minutes"]["version"]}) as r:
            check("minutes approved", r.status == 200)
        frozen = {i["id"]: (i["text"], i.get("owner"), i.get("due")) for i in d["summary"]["items"] + d["actions"]["items"]}
        h = await update(b, s, thirds[2])
        latencies.append(h.get("seconds"))
        d = await snapshot()
        now_items = {i["id"]: (i["text"], i.get("owner"), i.get("due")) for i in d["summary"]["items"] + d["actions"]["items"]}
        check("approved minutes frozen", all(now_items.get(k) == v for k, v in frozen.items()),
              f"{sum(1 for k, v in frozen.items() if now_items.get(k) == v)}/{len(frozen)} items as approved")
        check("new LLM items after approval are flagged", d["session"]["minutes"]["changed_since"] or
              set(now_items) == set(frozen), "changed_since" if d["session"]["minutes"]["changed_since"] else "no new items")

        print("4. a task split across two updates (asked at the end of one, accepted in the next)", flush=True)
        split = await consented(b, "bot_llmsplit", ["Sam Carter", "Priya Raman"])
        t1 = now_ms() - 300_000
        split.t0_ms = t1
        h = await update(b, split, [(t1 + 5_000, t1 + 9_000, "Sam Carter", "Let's look at the budget for the client pitch."),
                                    (t1 + 10_000, t1 + 14_000, "Priya Raman", "The numbers are in the shared folder."),
                                    (t1 + 20_000, t1 + 24_000, "Sam Carter", "Priya, could you send the budget sheet to the client?")])
        latencies.append(h.get("seconds"))
        h = await update(b, split, [(t1 + 31_000, t1 + 34_000, "Priya Raman", "Sure, I'll do it by Friday."),
                                    (t1 + 36_000, t1 + 40_000, "Sam Carter", "Great. Next, the venue for the launch.")])
        latencies.append(h.get("seconds"))
        d = await snapshot(f"/live/bot_llmsplit/api/snapshot")
        budget = [i for i in d["actions"]["items"] if "budget" in i["text"].lower()]
        check("the split task is kept", bool(budget), budget[0]["text"] if budget else "missing")
        if budget:
            check("with Priya as the owner and Friday as the due date",
                  budget[0]["owner"] == "Priya Raman" and budget[0]["due"] != "",
                  f"owner {budget[0]['owner'] or '-'}, due {budget[0]['due'] or budget[0].get('due_text') or '-'}")

        lat = [x for x in latencies if isinstance(x, (int, float))]
        if lat:
            print(f"\nlatency per update (s): {lat}; median {statistics.median(lat):.1f}, max {max(lat):.1f}", flush=True)
        report = {"model": b.notes.model, "latency_s": lat, "checks": [{"name": n, "ok": ok, "detail": det} for n, ok, det in results],
                  "made": time.strftime("%Y-%m-%d %H:%M:%S")}

        if a.shots:
            await screenshots(base, sid, code, a.shots)
            report["shots"] = a.shots
        out = settings.SESSIONS_DIR / "llm_check_report.json"
        out.write_text(json.dumps(report, indent=1))
        failed = [n for n, ok, _ in results if not ok]
        print(f"\n{len(results) - len(failed)}/{len(results)} checks passed" + (f"; FAILED: {failed}" if failed else ""), flush=True)
        print(f"report: {out}", flush=True)
        if a.keep:
            print(f"page: {base}/live/{sid}  (Ctrl+C to stop)", flush=True)
            await asyncio.Event().wait()
        await runner.cleanup()
        sys.exit(1 if failed else 0)


async def screenshots(base, sid, code, folder):
    from playwright.async_api import async_playwright
    Path(folder).mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        for theme in ("light", "dark"):
            ctx = await browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme=theme, reduced_motion="reduce")
            page = await ctx.new_page()
            await page.goto(f"{base}/live/{sid}")
            await page.wait_for_selector(".panel--summary .item")
            await page.wait_for_timeout(800)
            await page.screenshot(path=f"{folder}/real-organizer-{theme}-1440.png")
            await page.get_by_role("tab", name="Activity").click()
            await page.locator(".llm-box summary").first.click()
            await page.wait_for_timeout(300)
            await page.screenshot(path=f"{folder}/real-activity-{theme}-1440.png")
            await ctx.close()
            ctx = await browser.new_context(viewport={"width": 390, "height": 844}, color_scheme=theme, reduced_motion="reduce")
            page = await ctx.new_page()
            await page.goto(f"{base}/m/{sid}")
            await page.wait_for_selector('[aria-label="Character 1 of 6"]')
            await page.keyboard.type(code.replace("-", ""))
            await page.get_by_role("listitem").filter(has_text="Daniel Okafor").click()
            nav = page.get_by_role("navigation", name="Sections")
            await nav.get_by_role("button", name="Summary").click()
            await page.wait_for_timeout(500)
            await page.screenshot(path=f"{folder}/real-participant-summary-{theme}-390.png")
            await nav.get_by_role("button", name=re.compile("^Actions")).click()
            await page.wait_for_timeout(500)
            await page.screenshot(path=f"{folder}/real-participant-actions-{theme}-390.png")
            await ctx.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
