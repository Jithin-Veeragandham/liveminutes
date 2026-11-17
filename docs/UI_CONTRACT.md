# UI data contract

The meeting page (`/m/<id>` for participants, `/live/<id>` for the organizer) shows four kinds of data:
- transcript lines;
- summary items;
- action items;
- answers about past meetings.

The UI gets the last three from three small **adapter functions** in [`1_ingestion/ui/adapters.py`](../1_ingestion/ui/adapters.py). Today they return sample data from [`1_ingestion/ui/fixtures/`](../1_ingestion/ui/fixtures/), shown with a visible "Sample data" label. Demo meetings get theirs from the demo script, labelled "Demo".

To plug in the real summarizer and action-item extraction (Jithin) or the knowledge base (Ashmit), **replace the body of the matching function**. Keep the shapes below; nothing else in the UI changes.

## Transcript line

Built by the UI from the bridge's own transcript (`Session.lines`, or `pieces.jsonl` after a restart). Teammates read these; they don't produce them.

```json
{"id": "t1794943226759", "speaker": "Maya Chen", "start": 12.4, "end": 18.9, "text": "Okay, looks like we're all here."}
```

| Field | Meaning |
|---|---|
| `id` | `"t" + start_ms`: the wall-clock start, in ms, of the first piece in the line. It's stable, live and after a restart. |
| `speaker` | The display name, `"Multiple speakers (A, B)"` or `"Unknown speaker"` (Teams) |
| `start`, `end` | Seconds from the start of the meeting |
| `text` | What was said. The same speaker within `MERGE_GAP_MS` is merged into one line, like `transcript.txt`. |

The UI also adds `multi` (bool) and `unknown` (bool). Inside the adapters, `meeting.lines()` returns these dicts. `meeting.rows()` returns the raw `(start_ms, end_ms, speaker, text)` pieces, and `meeting.line_id_for(start_ms)` maps a piece to its line id.

## `summary_source(meeting)`

```json
{
  "items": [
    {"id": "s4", "type": "decision", "version": 2,
     "text": "Launch moves from December 2 to Tuesday, December 9.",
     "source_line_ids": ["t1794943384110", "t1794943401230"]}
  ],
  "sample": false,
  "label": null,
  "note": ""
}
```

| Field | Rule |
|---|---|
| `id` | **Stable.** When the summarizer revises a point, keep its id and raise `version`. Edits and source links stay attached to the id. |
| `text` | One point, up to 600 characters |
| `type` | `"point"` (default) or `"decision"`. Decisions get their own section in the minutes. |
| `version` | Integer, starting at 1. Raise it whenever the text changes. |
| `source_line_ids` | Transcript line ids the point came from (shown as chips that jump to the line). `[]` is fine. |
| `sample`, `label`, `note` | `sample: true` shows the label chip and the note banner. Set `sample: false, label: null` for real output. |

## `actions_source(meeting)`

```json
{
  "items": [
    {"id": "a1", "text": "Ship the offline-sync fix to beta", "owner": "Daniel Okafor",
     "due": "2026-11-21", "status": "pending", "source_line_ids": ["t1794943502000"], "version": 1}
  ],
  "sample": false, "label": null, "note": ""
}
```

| Field | Rule |
|---|---|
| `id`, `version`, `source_line_ids` | As for summary items |
| `text` | The task, up to 300 characters |
| `owner` | A display name as it appears in the meeting (`meeting.consented()`), or `""` for unassigned |
| `due` | `YYYY-MM-DD`, or `""` |
| `status` | `"pending"`, `"approved"` or `"done"`. New items should be `"pending"`. |

## `ask_source(question, meeting)`

```json
{
  "question": "What did we decide about the launch date?",
  "answer": "The launch was planned for December 2 ...",
  "source_meetings": [{"id": "bot_abc123", "title": "Launch sync", "date": "2026-11-10", "snippet": "We keep December 2."}],
  "sample": false
}
```

| Field | Rule |
|---|---|
| `answer` | Plain text (the UI escapes it) |
| `source_meetings[].id` | A meeting id (session folder name) |
| `title`, `date` | Required; `date` is `YYYY-MM-DD` |
| `snippet` | Optional |

## What the adapters don't do: edits

People's edits never go back into the adapters. They're written to the meeting's **append-only edit log**, `sessions/<id>/edits.jsonl` (code: [`1_ingestion/ui/editlog.py`](../1_ingestion/ui/editlog.py)), and replayed on top of whatever the adapters return:

```json
{"seq": 7, "ms": 1794943600123, "who": "Maya Chen", "role": "participant", "change": "c3fa91b2e0",
 "op": "update", "kind": "action", "item": "a1", "field": "owner", "before": "", "after": "Daniel Okafor", "version": 3}
```

- One line per changed field. One save touching 2 fields gives 2 lines with the same `change`.
- `op` is one of `create`, `update`, `delete`, `undo` (with `undo_of`) or `approve`.
- Items of kind `meeting` (the title) and `minutes` (the approval) live in the same log.
- **Precedence:** a field someone has edited keeps the edited value, even if the summarizer later rewrites that item. Fields nobody edited follow the adapter.
- **Conflicts:** an item's `version` is its last log line's `version` (or the adapter's, if nobody has edited it). A save names the version it started from; if someone saved in between, it gets HTTP 409 and the page shows both versions.
- **Removal:** an item the adapter stops returning disappears from the page, but its log lines stay. So don't drop items people may have edited; mark them in the text instead.

## Live updates (for reference)

The page gets everything from one Server-Sent Events stream: `GET /m/<id>/api/stream` (participants) or `/live/<id>/api/stream` (organizer).
- **First event:** `snapshot`, the whole page.
- **Then:** `session`, `people`, `lines` (`{"upsert": [...], "remove": [...]}`), `summary`, `actions`, `presence`, `me`, `log`, and for the organizer `activity`.
- **Ending:** `revoked` when access ends (code expired, someone said no).

Any change to what an adapter returns reaches open pages on the next stream wake-up: a transcript line, a chat message, an edit, or the 15-second heartbeat. To push your own update right away, call `ui.hub.touch(meeting_id)`.
