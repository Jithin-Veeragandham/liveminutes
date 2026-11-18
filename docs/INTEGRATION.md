# Integration guide: plugging the LLM summary, action items and knowledge base into the UI

For whoever merges the three parts of LiveMinutes:
- the UI with editing and Demo mode (`1_ingestion/ui/`, PR #9);
- the LLM summary and action items (Jithin);
- the knowledge base over past meetings (Ashmit).

It assumes no other context. The JSON shapes are also in [UI_CONTRACT.md](UI_CONTRACT.md); the UI itself is described in [UI.md](UI.md).

**The short version:** you only replace functions in [`1_ingestion/ui/adapters.py`](../1_ingestion/ui/adapters.py), and add your own modules. Then you run one command that tells you whether your code fits:

```bash
.venv/bin/python -m pytest 1_ingestion/tests/test_contract.py -v
```

---

## 1. How data flows (one screen)

```
 Meet / Teams call
   │  Attendee bot (Docker) streams audio + webhooks
   ▼
 bridge.py  (1_ingestion/, Docker container "liveminutes-bridge", port 8776)
   ├─ consent gate: nothing is kept until everyone says yes in chat; anyone's "no" deletes the meeting
   ├─ audio → Groq Whisper → transcript lines → Session.lines + sessions/<meeting>/pieces.jsonl, transcript.txt
   │
   ├─ ui/  (same process)
   │    snapshot(meeting) is built for every page load and every live update:
   │      transcript lines ......... ui/meeting.py  Meeting.lines()          (from the bridge)
   │      summary items ............ adapters.summary_source(meeting)        ← JITHIN (LLM)
   │      action items ............. adapters.actions_source(meeting)        ← JITHIN (LLM)
   │      people's edits ........... ui/editlog.py  sessions/<meeting>/edits.jsonl, replayed ON TOP
   │    "Approve minutes" ......... approved record stored in the edit log
   │                                 → adapters.minutes_approved(meeting_id, record) ← ASHMIT (index it)
   │    Ask panel ................. adapters.ask_source(question, meeting)  ← ASHMIT (answer from the index)
   │    adapters.background(bridge): optional loop started with the bridge   ← JITHIN (run the LLM here)
   │
   ▼
 Browsers: organizer http://localhost:8776/live/<meeting>; participants /m/<meeting> (link + code, via a tunnel)
           live updates by Server-Sent Events, or a long poll where those don't get through
```

Key facts:
- **Everything below runs inside the bridge process**, in the `liveminutes-bridge` Docker image. Your code must be in that image (see section 6).
- **One meeting = one session folder:** `1_ingestion/sessions/<meeting_id>/`, where `meeting_id` is the Attendee bot id like `bot_ab12…`, or `demo_…` in Demo mode. It holds:
  - `pieces.jsonl` and `transcript.txt`: the transcript;
  - `consent.jsonl`: the consent record;
  - `edits.jsonl`: people's edits, append only;
  - `meta.json`: the UI's notes about the meeting;
  - your own files, if you like (e.g. `llm_notes.json`).
- **Edits are never sent back to your code.** They're replayed over your output, and they win (section 3).

## 2. The adapters (`1_ingestion/ui/adapters.py`)

| Function | Sync/async | Called when | How often | May be slow? |
|---|---|---|---|---|
| `summary_source(meeting)` | **sync only** | building every snapshot: page load, every live update for every viewer (any new line, chat message, edit, presence change, plus a 15 s heartbeat), every long-poll answer, edits, export, approve | **many times a second** in a busy meeting | **No.** Under 50 ms; the contract test enforces it. Return a cached result. |
| `actions_source(meeting)` | **sync only** | same as above | same | **No** |
| `ask_source(question, meeting)` | sync (run in a thread) **or** `async def` | once per question in the Ask panel | per question | Yes (an LLM call is fine) |
| `ask_info()` | sync | every snapshot | many times a second | No (return constants) |
| `minutes_approved(meeting_id, record)` | sync (thread) or async | after "Approve minutes" (also "Approve again") | per approval | Yes; it runs in the background and never delays the page |
| `minutes_unapproved(meeting_id)` | sync (thread) or async | after an approval is undone, and when the meeting is deleted (someone said no) | rare | Yes |
| `background(bridge)` (optional) | **`async def`** | once, when the bridge starts; cancelled when it stops | runs forever | It's your loop |

**Inputs.** `meeting` is a [`ui.meeting.Meeting`](../1_ingestion/ui/meeting.py). Use it read-only:

| Attribute / method | What |
|---|---|
| `meeting.sid` | meeting id (= session folder name) |
| `meeting.demo` | `True` for Demo meetings; **keep the demo branch** (section 6) |
| `meeting.live` | the bot is in the call right now |
| `meeting.lines()` | transcript: `[{"id": "t1795043210257", "speaker": "Maya Chen", "start": 0.0, "end": 6.2, "text": "...", "multi": false, "unknown": false}]`. `start`/`end` are seconds from the meeting start. Ids are stable (cite them in `source_line_ids`). |
| `meeting.people()` | `[{"id", "name", "present", "decision": "yes"\|"no"\|None, "vouched_by", "asked"}]` |
| `meeting.consented()` | names of people who said yes. **Owners must be one of these, or `""`.** |
| `meeting.platform` | `"meet"` or `"teams"` |
| `meeting.started_ms()`, `meeting.ended_ms()` | wall clock in ms (or `None`) |
| `meeting.status()` | `("recording"\|"paused"\|"joining"\|"ended"\|"declined", waiting_names)` |
| `meeting.folder` | the session folder (a `Path`): read or write your own files here |
| `meeting.rows()` | raw transcribed pieces `[(start_ms, end_ms, speaker, text)]` |
| `meeting.line_id_for(start_ms)` | the line id that contains a piece |

In `background(bridge)` you also have `bridge.sessions`, the meetings running now (`{meeting_id: Session}`). Useful Session fields:
- `.lines` (grows as people talk);
- `.version` (goes up on every change);
- `.recording`;
- `.state` (`"ended"` when the bot left);
- `.declined`;
- `.demo`.

Build `Meeting(bridge, meeting_id)` from these.

### 2.1 `summary_source(meeting)` → summary items

```json
{
  "items": [
    {"id": "s4", "type": "decision", "version": 2,
     "text": "Launch moves from December 2 to Tuesday, December 9. The email campaign and blog post shift by a week; final screenshots are due December 4.",
     "source_line_ids": ["t1795043212691", "t1795043212958"]}
  ],
  "sample": false,
  "label": null,
  "note": ""
}
```

| Field | Rule |
|---|---|
| `id` | A string of 1–40 characters (letters, digits, `_ . : -`). **Stable** (section 3). Not `meeting`, `minutes`, `__new`, and not `u` + 8 hex digits: that pattern is for items people add. |
| `text` | Up to 600 characters |
| `type` | `"point"` (default) or `"decision"`. Decisions get their own section in the minutes. |
| `version` | An integer starting at 1. **Raise it when that item's text changes.** |
| `source_line_ids` | Ids from `meeting.lines()`; `[]` is allowed |
| `sample`, `label`, `note` | Real output: `false`, `null`, `""`. `sample: true` shows the label chip and the note banner (used for sample and Demo data). |

### 2.2 `actions_source(meeting)` → action items

```json
{
  "items": [
    {"id": "a1", "text": "Ship the offline-sync fix, with the three sync status states, in the beta build",
     "owner": "Daniel Okafor", "due": "2026-11-20", "status": "pending", "version": 1,
     "source_line_ids": ["t1795043213358", "t1795043213590"]}
  ],
  "sample": false, "label": null, "note": ""
}
```

| Field | Rule |
|---|---|
| `id`, `version`, `source_line_ids` | As above. **Summary ids and action ids must not overlap.** |
| `text` | Up to 300 characters |
| `owner` | One of `meeting.consented()`, or `""` |
| `due` | `YYYY-MM-DD`, or `""` |
| `status` | `"pending"` for new items. `"approved"` and `"done"` are set by people. |

### 2.3 `ask_source(question, meeting)` and `ask_info()`

`ask_source` returns:

```json
{"question": "What did we decide about the launch date?",
 "answer": "On Nov 18 the team moved the launch from December 2 to December 9 because of a sync bug.",
 "source_meetings": [{"id": "bot_ab12cd", "title": "Orbit 2.0 launch sync", "date": "2026-11-18", "snippet": "Launch moves to December 9."}],
 "sample": false}
```

- `source_meetings[].id` is a meeting id; `date` is `YYYY-MM-DD`; `snippet` is optional.
- `meeting` is the meeting the question was asked from (you may ignore it).
- Answer **only from approved minutes** (section 4). Before citing a meeting, check that `ui.approved_minutes(meeting_id)` is still not `None`: someone may have said no since, which deletes the meeting.

`ask_info()` returns `{"sample": false, "note": "", "suggestions": ["What did we decide about …?", …]}`. While `sample` is `true`, the Ask panel shows "Preview · sample data".

### 2.4 Pushing a live update when the summary changes

`summary_source` is only *read* when a page needs it. After your background job has new items, write them where `summary_source` reads them (memory and/or a file in `meeting.folder`), then call:

```python
import ui
ui.notify(meeting_id)   # every open page of that meeting updates within about a second (stream or long poll)
```

### 2.5 Recommended pattern for the LLM summary and action items (Jithin)

The LLM runs in `background()`, never inside `summary_source`. Results go into a per-meeting cache that's also kept on disk, so a bridge restart keeps them.

```python
# 1_ingestion/ui/adapters.py  (keep everything that's there for Demo mode)
import asyncio, json
import ui
from ui.meeting import Meeting
import llm_notes                      # YOUR module, inside 1_ingestion/ (so it's in the Docker image)

_notes = {}                           # meeting id -> {"summary": [...], "actions": [...]}

def _load(meeting):
    if meeting.sid not in _notes:
        path = meeting.folder / "llm_notes.json"
        _notes[meeting.sid] = json.loads(path.read_text()) if path.exists() else {"summary": [], "actions": []}
    return _notes[meeting.sid]

def summary_source(meeting):
    if meeting.demo:                  # unchanged: Demo mode
        d = demo.state(meeting.sid)
        return {"items": _resolve(meeting, d.get("keys", {}), d.get("summary", [])), "sample": True, "label": "Demo",
                "note": "Demo meeting: the summary is part of the script and updates as the meeting goes."}
    return {"items": _load(meeting)["summary"], "sample": False, "label": None, "note": ""}

def actions_source(meeting):          # same shape, with _load(meeting)["actions"]
    ...

async def background(bridge):
    seen = {}                         # meeting id -> Session.version already summarized
    while True:
        await asyncio.sleep(20)       # how often to re-summarize during a meeting
        for sid, s in list(bridge.sessions.items()):
            if s.demo or s.declined or not s.lines or seen.get(sid) == s.version:
                continue
            m = Meeting(bridge, sid)
            previous = _load(m)
            notes = await llm_notes.update(m.lines(), m.consented(), previous)   # your LLM call (async, or run_in_executor)
            if not m.folder.is_dir():  # deleted meanwhile (someone said no): keep nothing
                continue
            (m.folder / "llm_notes.json").write_text(json.dumps(notes))
            _notes[sid] = notes
            seen[sid] = s.version
            ui.notify(sid)
```

- **Final pass:** when `s.state == "ended"`, run one last update.
- **Cache size:** keep `_notes` small, e.g. drop meetings that ended long ago.

### 2.6 Recommended pattern for the knowledge base (Ashmit)

```python
def minutes_approved(meeting_id, record):     # runs in a thread, after "Approve minutes"
    if record["demo"] or record["sample"]:    # never index Demo meetings or sample data
        return
    kb.upsert(meeting_id, record)             # REPLACE any earlier version of this meeting

def minutes_unapproved(meeting_id):           # approval undone, or meeting deleted (someone said no)
    kb.delete(meeting_id)

async def background(bridge):                 # optional: rebuild the index at start-up
    for record in ui.all_approved_minutes():
        if not (record["demo"] or record["sample"]):
            kb.upsert(record["meeting_id"], record)
    ...                                       # (and Jithin's loop, if both need one: run both from here)
```

**One `background()` function:** there is only one. If both of you need a loop, start both from it, e.g. with `asyncio.gather(summary_loop(bridge), kb_backfill())`.

## 3. Rules that protect edits

People edit, approve and delete summary items and action items in the UI. A later LLM run must never undo that.

1. **IDs must stay stable across regenerations.** When the LLM rewrites the summary, an item that is "the same point" keeps its id; raise `version` if its text changed.
   - **How to keep them stable:** pass the previous items (with ids) into the prompt and ask the model to return them; or match new items to old ones yourself (e.g. by `source_line_ids` or text similarity).
   - **Why it matters:** an id that drifts makes an edited item look new (the person's version stays, but the LLM's rewrite appears beside it as a duplicate).
2. **New LLM items get new IDs, never a reused one.** Use a counter that only grows (`s1, s2, s3…`, persisted in your notes file) or random ids (`"llm-" + uuid4().hex[:8]`). Never give an old id to a different point. Ids must not look like `u` + 8 hex digits, which is how people's added items are named.
3. **Once a person has touched an item, the LLM can't change it.** That means edited, approved, marked done, or deleted. This is enforced by the code, not by convention ([`ui/editlog.py`](../1_ingestion/ui/editlog.py)):
   - **The freeze:** the first edit-log line for a generated item stores `"base"`, the whole item as generated at that moment. When the page is built, that item is rebuilt from `base` plus the log, and **whatever `summary_source`/`actions_source` now returns for that id is ignored**, every field of it.
   - **Dropped items:** if your adapter stops returning an item someone edited, the item still shows, as the person left it.
   - **What the LLM still controls:** items nobody touched follow your adapter (that's how the live summary updates). People's own added items exist only in the log.
4. **Approved minutes are frozen too.** "Approve minutes" stores the whole approved record in its log line:
   - **While the approval stands,** untouched generated items are shown as they were approved, not as your adapter returns them now.
   - **"Changed since":** if anything differs afterwards (a person's edit, or a new item your adapter adds), the minutes show "changed after approval" until someone approves again.
   - **Undoing the approval** returns untouched items to following the adapter.

Tests for all of this: `test_an_llm_rerun_never_overwrites_what_a_person_edited_or_approved` and `test_approving_freezes_the_record_until_undone` in [`1_ingestion/tests/test_ui.py`](../1_ingestion/tests/test_ui.py).

## 4. Approved records only (for the knowledge base)

The knowledge base must index **only approved minutes**, never live drafts or Demo meetings. The UI fires these hooks from [`1_ingestion/ui/routes.py`](../1_ingestion/ui/routes.py) (`minutes_changed`, `fire_hook`) and [`ui/__init__.py`](../1_ingestion/ui/__init__.py) (`meeting_deleted`):

| Event | Hook called | Notes |
|---|---|---|
| Someone presses **Approve minutes** (or **Approve again** after changes) | `minutes_approved(meeting_id, record)` | Upsert: replace any earlier version of that meeting |
| The approval is undone (Undo in the toast or history) | `minutes_unapproved(meeting_id)`, or `minutes_approved` again if an earlier approval is back in effect | |
| Someone says **no** in the meeting: the bot leaves and the session folder is wiped | `minutes_unapproved(meeting_id)` | Remove everything about the meeting (privacy) |

How the hooks run:
- In the background, after the change is saved.
- Sync functions run in a thread; `async def` hooks are awaited.
- An exception is logged and never breaks the page.
- There's no retry, so at start-up rebuild from `ui.all_approved_minutes()`. `ui.approved_minutes(meeting_id)` gives the record in effect now, or `None`.

**The `record` (exactly what's passed):**

```json
{
  "meeting_id": "bot_ab12cd34",
  "title": "Orbit 2.0 launch sync",
  "platform": "meet",
  "demo": false,
  "sample": false,
  "started_ms": 1795043210257,
  "ended_ms": 1795043514942,
  "attendees": ["Maya Chen", "Daniel Okafor", "Priya Raman", "Leo Martins"],
  "approved_by": "Maya Chen",
  "approved_ms": 1795043614948,
  "minutes_version": 2,
  "summary": [
    {"id": "s1", "text": "Beta 2.0 is with about 400 testers since Thursday ...", "type": "point", "status": "draft",
     "source_line_ids": ["t1795043210507"], "version": 1},
    {"id": "s4", "text": "Launch moves from December 2 to Tuesday, December 9 ...", "type": "decision", "status": "approved",
     "source_line_ids": ["t1795043212691"], "version": 3}
  ],
  "actions": [
    {"id": "a1", "text": "Ship the offline-sync fix ...", "owner": "Daniel Okafor", "due": "2026-11-20",
     "status": "pending", "source_line_ids": ["t1795043213358"], "version": 1}
  ],
  "transcript": [
    {"id": "t1795043210257", "speaker": "Maya Chen", "start": 0.0, "end": 6.2, "text": "Okay, looks like we're all here ..."}
  ]
}
```

- **What's in the lists:** `summary` and `actions` are exactly what people approved, with their edits applied and deleted items left out. Decisions are `summary` items with `"type": "decision"`.
- **`sample`:** true if the summary or action items were sample data (no summarizer connected), not real output. Skip those, and skip `demo: true`.
- **Storage:** the record lives in the approval's line of `sessions/<meeting_id>/edits.jsonl` (field `"record"`).

## 5. Contract tests

```bash
.venv/bin/python -m pytest 1_ingestion/tests/test_contract.py -v
```

[`tests/test_contract.py`](../1_ingestion/tests/test_contract.py) calls **your** adapters on three meetings:
- a live meeting with a real-looking transcript;
- one with no transcript yet;
- a finished Demo meeting.

It checks:
- **Shapes:** every field, type and allowed value from section 2.
- **Ids:** unique, stable between two calls, no summary/action overlap, no reserved ids.
- **References:** `source_line_ids` exist in that meeting; owners are consented people or `""`; dates are `YYYY-MM-DD`.
- **Speed:** `summary_source`/`actions_source` take under 50 ms per call (median).
- **Demo mode** still returns the script's items.
- **Ask:** `ask_source` and `ask_info` have the right shape.
- **Hooks** have the right signatures. It **doesn't call** them, so no test data reaches your index.

A failing check says what's wrong, for example: `actions_source(bot_contract_live): a1: owner 'Somebody Else' isn't one of [...]`.

## 6. Merge notes

**Recommended order:**
1. **PR #9** (branch `ui-shared-view-and-editing`) into `main` first. It brings `ui/`, the adapters, the hooks and the contract test.
2. **Jithin's work:** merge `main` into his branch (or rebase), move the LLM code behind `summary_source`/`actions_source`/`background`, run the contract test, merge.
3. **Ashmit's work:** the same, with `ask_source`/`ask_info`/`minutes_approved`/`minutes_unapproved`.

Or build one integration branch: `git switch -c integration origin/main`, then `git merge` the three branches in that order.

**Likely conflicts, and how to resolve them:**
| File | What PR #9 added | Resolve by |
|---|---|---|
| `1_ingestion/bridge.py` | 9 lines: `import ui`; `if s.demo: return 200` at the top of `Bridge.attendee()`; `ui.meeting_ended(s)` in `on_bot_state` when the bot ends; the link + code message after "Thanks, everyone agreed" in `evaluate()`; `ui.meeting_deleted(s)` after `s.wipe()` in `decline()`; `ui.setup(app, bridge)` in `main()` | Keep **both** sides. These lines must stay exactly where they are. |
| `1_ingestion/settings.py` | A block at the end: `PUBLIC_BASE_URL`, `VIEW_ACCESS_DAYS`, `UI_SECRET` | Keep both blocks |
| `1_ingestion/session.py` | `self.demo = False` | Keep |
| `requirements.txt` | Section 4 (tests: pytest, Playwright) | Keep. **Packages your code needs inside the bridge go in section 1**: the Docker image installs only section 1 (`1_ingestion/Dockerfile`). Pin them with `==`. |
| `.env.example` | `PUBLIC_BASE_URL` block at the end | Keep both. Put new keys (e.g. an LLM API key) in `.env.example` with a comment. The bridge container gets the repo's `.env` automatically (`compose.local.yaml`: `env_file`). |
| `1_ingestion/Dockerfile`, `Dockerfile.dockerignore` | `COPY 1_ingestion/ui ./ui` and `!1_ingestion/ui` | Keep. **The image only contains `1_ingestion/*.py`, `*.html` and `ui/`.** If your code is elsewhere (`2_summarization/`, `4_qa/`), either move the bridge-side part into `1_ingestion/` (simplest), or add a `COPY` line **and** a `!path` line in `Dockerfile.dockerignore`. Then `dc build liveminutes-bridge`. |

**Don't change** (or the contract, security or edit history breaks):
- `ui/editlog.py`: the edit log format and the protection rules (section 3);
- `ui/access.py` and the guard in `ui/routes.py`: codes, cookies, CSRF, what a tunnel can reach;
- `ui/hub.py`, `ui/meeting.py`, `ui/static/`, `ui/templates/`;
- `ui/fixtures/demo_meeting.json`;
- `docs/UI_CONTRACT.md` (unless the whole team agrees to change the contract, and then `test_contract.py` too);
- the existing tests. Add tests; don't weaken them.

**Change:** `ui/adapters.py` (the functions above), your own modules, the `requirements.txt` section 1 and `.env.example` additions, and docs.

**Demo mode must keep working:**
- **Adapters:** keep the `if meeting.demo:` branch at the top of `summary_source` and `actions_source` (the contract test fails without it).
- **`background()`:** skip sessions with `s.demo`.
- **Knowledge-base hooks:** skip `record["demo"]`.
- **Demo meetings** have ids starting with `demo_` and need no Attendee, Groq or LLM.

## 7. Done checklist (after merging)

Run from the repo root:

```bash
.venv/bin/python -m pytest 1_ingestion/tests                       # unit + API + contract tests (all must pass)
.venv/bin/python -m pytest 1_ingestion/tests/test_contract.py -v   # your adapters fit the contract
.venv/bin/python -m playwright install chromium                    # once
.venv/bin/python -m pytest 1_ingestion/tests -m e2e                # browser tests (the real-tunnel test is skipped without cloudflared)
.venv/bin/python 2_summarization/run.py --model textrank --split smoke --no-log   # baseline: rouge1 0.26 (ROUGE-1 0.2600)
cd 1_ingestion && dc() { (cd attendee && docker compose -f dev.docker-compose.yaml -f ../compose.local.yaml "$@"); }
dc build liveminutes-bridge && dc up -d                            # only the bridge image changes
dc ps                                                              # 6 services Up; liveminutes-bridge (healthy)
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/accounts/login/   # 200
```

**Short manual check (about 10 minutes):**
1. **Demo:** on http://localhost:8776/live press **Start demo meeting**. Its summary and action items still show the **Demo** label and the scripted items.
2. **Real meeting:** send the bot to a real meeting with 2 people. After a minute of talk the summary and action items appear **without** a "Sample data" label, with source chips that jump to the transcript.
3. **Edit protection:** edit one summary item and approve one action item, then wait for the next LLM run. Both stay as people left them; untouched items keep updating.
4. **Approval:** press **Approve minutes**. The knowledge base receives the record (check its index), and the Ask panel no longer says "Preview · sample data". A question about the meeting cites it.
5. **Undo:** undo the approval. The knowledge base drops the meeting.
6. **Deletion:** in a throwaway meeting, someone types **no**. The bot leaves, the meeting is gone from the UI, and from the knowledge base.
