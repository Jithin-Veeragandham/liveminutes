# LiveMinutes

CS6170 AI Capstone, Northeastern. A real-time meeting tracker: a bot joins the meeting, gets consent in chat, transcribes live with speaker names, summarizes while the meeting runs, and answers questions about past meetings.

## Contents

- [Pipeline](#pipeline) · [Layout](#layout) · [Conventions](#conventions)
- [1. Ingestion](#1-ingestion): bot, consent, live transcript ([setup](#14-setup-once-per-machine) · [configuration](#15-configuration) · [run a meeting](#16-run-a-meeting) · [troubleshooting](#18-troubleshooting))
- [2. Summarization](#2-summarization) · [3. Action items](#3-action-items) · [4. Q&A](#4-qa-knowledge-base) · [5. Data](#5-data) · [6. Evaluation](#6-evaluation) · [7. Infra](#7-infra)
- [Origin](#origin)

## Pipeline

A meeting flows top to bottom. Each step reads what the step above it writes.

```
        Meet / Teams meeting
                │
                ▼
   ┌─────────────────────────┐
   │ 1  Ingestion            │  bot joins, asks consent, transcribes live
   └─────────────────────────┘
                │  transcript.txt   "[mm:ss] Name: text"
                ▼
   ┌─────────────────────────┐
   │ 2  Summarization        │  live summary while the meeting runs
   └─────────────────────────┘
                │
                ▼
   ┌─────────────────────────┐
   │ 3  Action items         │  task · owner · due date
   └─────────────────────────┘
                │  transcripts + summaries, indexed per meeting
                ▼
   ┌─────────────────────────┐
   │ 4  Q&A                  │  ask questions about past meetings
   └─────────────────────────┘

   Supporting:   5 Data (test meetings) · 6 Evaluation (scores 2–4) · 7 Infra (AWS)
```

| Step | In | Out | How |
|---|---|---|---|
| [1. Ingestion](#1-ingestion) | Live meeting audio | `sessions/<bot_id>/transcript.txt` | Attendee bot, consent gate, Groq Whisper |
| [2. Summarization](#2-summarization) | Transcript | Running summary | Extractive vs abstractive, TextRank baseline |
| [3. Action items](#3-action-items) | Transcript | Tasks with owner and due date | |
| [4. Q&A](#4-qa-knowledge-base) | Past transcripts + summaries | Answers to questions | Retrieval (BM25, vector, graph, reranker) → API-hosted LLM |
| [5. Data](#5-data) | | Test meetings | AMI Meeting Corpus + synthetic meetings (LLM-written, human-reviewed) |
| [6. Evaluation](#6-evaluation) | Outputs of 2–4 | Scores | ROUGE / BERTScore (2), P/R/F1 + owner/due accuracy (3), LLM-as-judge + human review (4) |
| [7. Infra](#7-infra) | | Deployment | AWS |

## Layout

Folders are numbered in pipeline order, and each has the matching section below.

| Folder | What |
|---|---|
| [1_ingestion/](1_ingestion/) | Attendee meeting bot → `bridge.py` → Groq Whisper. Consent gate, Meet per-speaker audio, Teams speaker labels |
| [2_summarization/](2_summarization/) | Feature 1: live summarization |
| [3_action_items/](3_action_items/) | Feature 2: action items |
| [4_qa/retrieval/bm25/](4_qa/retrieval/bm25/) | F3 baseline: BM25 |
| [4_qa/retrieval/reranker/](4_qa/retrieval/reranker/) | F3: LightGBM reranker over candidates |
| [4_qa/retrieval/vector/](4_qa/retrieval/vector/) | F3: vector retrieval |
| [4_qa/retrieval/graph/](4_qa/retrieval/graph/) | F3: Neo4j graph retrieval |
| [4_qa/answer/](4_qa/answer/) | F3: answer generation (API-hosted LLM) |
| [5_data/](5_data/) | Corpora, synthetic meetings, Q&A pairs |
| [6_evaluation/](6_evaluation/) | Metrics + judge harness for F1–F3 |
| [7_infra/aws/](7_infra/aws/) | AWS deployment (new-account credits) |

## Conventions

- **One README** (this file). Each component gets a numbered section here, not its own README.
- **Shared at the repo root:** one `requirements.txt` (Python packages for every component) and one `.env` (all secrets, copied from `.env.example`). Each component keeps its own `settings.py` (settings, committed).
- Secrets only in `.env`, never in code. Meeting audio/transcripts, `5_data/raw/` and `5_data/processed/` are gitignored. Never commit real people's voices or transcripts.
- Shared interchange format between components: the ingestion transcript line `[mm:ss] Name: text` (see [1.3 Output](#13-output-what-downstream-components-read)).

---

## 1. Ingestion

Folder: [1_ingestion/](1_ingestion/). The open-source [Attendee](https://github.com/attendee-labs/attendee) meeting bot joins Google Meet or Teams and streams audio to `bridge.py`, which runs the consent gate, cuts audio at pauses, and transcribes each piece with Groq `whisper-large-v3-turbo`.

```
Google Meet / Teams
   │  bot joins (Chrome inside Docker), waits in the lobby until admitted
   ▼
Attendee  (localhost:8000)
   │  audio over wss://   Meet: one stream per person   Teams: one mixed stream
   │  webhooks over http: names, chat replies, "who is speaking" events, bot state
   ▼
bridge.py  (liveminutes-bridge container)
   1. consent: pause the bot, ask in chat, keep nothing until everyone says yes
   2. cut audio at pauses (2–10 s pieces)
   3. upload each piece to Groq (max 12 uploads/min, fits the free tier)
   ▼
"[mm:ss] Name: text" printed live + sessions/<bot_id>/transcript.txt
```

### 1.1 Files

All in `1_ingestion/`. The bridge's Python files, in the order audio flows through them (it only needs `aiohttp`):

| File | What |
|---|---|
| `settings.py` | **Every setting**, one commented line each (ports, Groq model and rate limit, audio cutting, consent timers). See [1.5](#15-configuration) |
| `bridge.py` | The main program: receives Attendee's audio and webhooks, runs the consent flow, uploads pieces to Groq |
| `chat.py` | Reading consent replies (`yes`, `no`, `Room 4B yes`) and writing the bot's consent message |
| `audio.py` | Cutting each audio stream into 2–10 s pieces at pauses |
| `session.py` | One meeting: who is in it, consent state, Teams speaker labels, the transcript and its files |
| `live_view.py` + `home.html` + `live.html` | The live view at http://localhost:8776/live: Sessions and Send bot tabs (`home.html`), and each meeting's transcript + bot activity (`live.html`) |

Everything else:

| File | What |
|---|---|
| `send_bot.sh` | Sends an Attendee bot to a Meet or Teams link, wired to the bridge (same request as the live view's Send bot tab) |
| `make_account.sh` | Creates a verified Attendee login + API key and writes the key to the repo's `.env` (no browser sign-up) |
| `make_certs.sh` | Local TLS certs (Attendee only accepts `wss://`) |
| `Dockerfile` + `Dockerfile.dockerignore` | Image for the bridge (`liveminutes-bridge`): python:3.12-slim, non-root, `/health` check. Built from the repo root for the shared `requirements.txt` |
| `compose.local.yaml` | Our additions to Attendee's compose: the bridge container, the bot trusting our certs, two Attendee settings |
| `../.env.example`, `../requirements.txt` | At the repo root, shared by all components: the API key template (see [1.5](#15-configuration)) and Python packages |
| `tests/test_consent.py` | Offline unit tests for consent replies, vouching and chat formatting (Attendee mocked, no Groq) |
| `tests/test_live.py` | Offline unit tests for the live view: page data, Send bot (link checks, request body, cross-site refusal) and the merged bot logs |
| `tests/consent_sim.py` | Offline: mock Attendee API + fake webhooks/audio. Scenarios `yes_late`, `no`, `timeout`, `teams` |
| `tests/replay_to_bridge.py` | Offline: replays a WAV as two fake Meet participants |
| `tools/receiver/` | Raw per-speaker WAV recorder + offline faster-whisper. **No consent step.** |
| `patches/teams-speaker-clock.patch` | Our one change to Attendee, applied at setup: fixes Teams speaker detection (see setup step 1) |
| `attendee/` | Attendee itself, cloned at setup (gitignored) |

### 1.2 How the bridge works

- **Meet:** one audio stream per person. The name comes from the stream, so it never has to guess who spoke.
- **Teams:** one mixed stream. A piece ends at a pause **or where a different person starts talking**: when a speech-start event arrives and the piece being built already holds someone else's speech, it is cut at that event's timestamp (`cut_at_speaker_change`). The event comes ~0.5 s late, but the piece is usually still being built then. Each piece is labelled from speech start/stop events: a name, `Multiple speakers (A, B)` (now mostly when two people talk at the same moment, which one mixed stream can't separate), or `Unknown speaker`. Those events arrive ~0.7 s after the audio, so a piece goes to Groq at once and is named when Groq answers (waiting out what's left of `LABEL_GRACE_MS`, 1.2 s after speech end, usually nothing). Before, it waited the full 1.2 s and only then uploaded.
- **Consent** (on by default, `--no-consent` for tests):
  - Audio is dropped on arrival until **everyone present** has replied yes. The bot is also paused inside Attendee.
  - The chat message lists everyone in the meeting, one per line. Meeting rooms, phone lines and other bots can't type, so someone present answers for them with the name and yes: `Conference Room 4B yes` (also `yes for Room 4B`, or part of a name if it fits only one participant). The bot confirms ("Noted: yes for …, from …") and `consent.jsonl` records who vouched.
  - Late joiner → everyone paused until they (or someone for them) say yes.
  - Anyone says no, for themselves or as `<name> no` → "Recording stopped.", the bot leaves, and the session is deleted here and in Attendee (only `consent.jsonl` is kept).
  - No answer → reminder after 2 min, leave after 2 more.
- **Groq pacing:** pieces end at a 0.7 s pause or at 10 s (the billing minimum). Pieces with < 0.3 s of speech are skipped. At most 12 uploads/min, with backlog merged and 429s retried.
- **Context:** each speaker's last 40 words go in as the Whisper `prompt`.
- **Merging:** back-to-back pieces from the same speaker with ≤ 3 s pause become one transcript line (`MERGE_GAP_MS` in `settings.py`).
- **Live view:** http://localhost:8776/live has two tabs. **Sessions** lists sessions. **Send bot** takes a Meet or Teams link and a bot name, sends the bot (the same request as `send_bot.sh`), then shows its logs, updating every 2 s: Attendee's own record of the bot (joining, waiting room, fatal errors and why), what the bridge saw (webhooks, people, consent, chat) and every Groq upload, with filters per source. Any session's logs open from its **logs** link. Only the page itself can send bots: the endpoint takes JSON only, from `localhost` (or `LIVE_VIEW_URL`), so another website can't use your Attendee key. `/live/<bot_id>` shows the transcript, who has consented and the bot's activity (state changes, its chat messages, replies, Groq errors). It long-polls: the bridge answers the page the moment a line or event comes in, instead of the page asking once a second. The bot posts this link in the meeting chat after the consent message. It's `localhost`, so it opens only on the computer running the bridge. Finished sessions are read back from `sessions/`.

### 1.3 Output (what downstream components read)

`1_ingestion/sessions/<bot_id>/` (gitignored):

| File | Contents |
|---|---|
| `transcript.txt` | `[mm:ss] Name: text`, sorted by start time, same-speaker runs merged. **This is the input for [2](#2-summarization), [3](#3-action-items) and [4](#4-qa-knowledge-base).** |
| `pieces.jsonl` | Per upload: speaker, timings (`start_ms`, `speech_end_ms`, `done_ms`), audio length, text, dropped hallucinations |
| `consent.jsonl` | Every consent event: who, what they typed, decision, who vouched, time |
| `events.jsonl` | Raw Attendee webhooks |
| `captions.txt` | The platform's own captions, for comparison |
| `pieces/NNNN.wav` | The uploaded audio pieces |

### 1.4 Setup (once per machine)

Everything runs in Docker: Attendee (app, worker, scheduler, Postgres, Redis) and the bridge are one compose project on one network. The bot reaches the bridge as `liveminutes-bridge`, and the bridge calls Attendee as `attendee-app-local`. That is the same layout you'd use when deploying (see [7. Infra](#7-infra)).

**You need:** Docker ≥ 26 with Compose (Docker Desktop on Windows/macOS), git, openssl, bash, and a free Groq account for the speech-to-text key (see [1.5](#15-configuration)). On Windows, run every command in **Git Bash**. Attendee's bot image is Intel-only, so on Apple Silicon it runs under emulation.

All commands run from `1_ingestion/`. To save typing, define this once per terminal:

```bash
cd 1_ingestion
dc() { (cd attendee && docker compose -f dev.docker-compose.yaml -f ../compose.local.yaml "$@"); }
```

**1. Get Attendee** at the tested commit. `core.autocrlf=false` matters on Windows: without it Attendee's scripts get Windows line endings and the worker crashes with `bash\r: No such file or directory`.
```bash
git clone -c core.autocrlf=false https://github.com/attendee-labs/attendee.git attendee
git -C attendee checkout eaacec16f2939b595f196b3957a717bbb0ed23e9
git -C attendee apply ../patches/teams-speaker-clock.patch
```
The patch is our only change to Attendee. Its Teams bot decides who is speaking by checking which audio sources were heard in the last 50 ms, but it compared the sources' timestamps (the browser's `performance` clock) with `Date.now()` (the wall clock). When those drift apart, which happens when Docker resyncs its clock, nobody is ever "recent": no speech events, and every Teams line becomes "Unknown speaker". The patch compares on the same clock.

**2. Build the images** (Attendee + bridge). The first build takes 10–20 min (Chrome, X server, audio libraries). Later builds are cached.
```bash
dc build
```

**3. Create Attendee's settings and the local TLS certs** (Attendee only sends audio to `wss://`):
```bash
dc run --rm -T attendee-app-local python init_env.py > attendee/.env
./make_certs.sh
```

**4. Start Attendee and set up its database:**
```bash
dc up -d postgres redis attendee-app-local attendee-worker-local attendee-scheduler-local
dc exec -T attendee-app-local python manage.py migrate
```
Check: http://localhost:8000 shows Attendee's login page.

**5. Create your Attendee login and API key.** This makes the account with the email already verified (no confirmation link), creates an API key and writes it to the repo's `.env` (creating it from `.env.example` if needed) as `ATTENDEE_API_KEY`. It prints the password for http://localhost:8000.
```bash
./make_account.sh you@example.com
```
(Alternative: sign up in the browser at http://localhost:8000/accounts/signup/, get the confirmation link with `dc logs attendee-app-local | grep confirm-email`, create a key under **API Keys**, and paste it into `.env`.)

**6. Add your Groq key** to `.env` in the repo root: `GROQ_API_KEY=gsk_...` (get one at https://console.groq.com/keys; see [1.5](#15-configuration)). The file was created from `.env.example` in step 5. Then start the bridge:
```bash
dc up -d liveminutes-bridge
```

**7. Check it's all up:**
```bash
dc ps                                   # 6 services "Up"; liveminutes-bridge "(healthy)"
curl http://127.0.0.1:8776/health       # -> ok
```

### 1.5 Configuration

Settings live in three files:

| File | What | In git? | Who writes it |
|---|---|---|---|
| [`1_ingestion/settings.py`](1_ingestion/settings.py) | **Everything that isn't a secret:** ports, Groq model and rate limit, audio cutting, transcript merging, consent timers, live view link. One commented line per setting | yes | You, when you want to change a setting. Then `dc up -d --build liveminutes-bridge` (seconds) |
| `.env` (repo root) | **Secrets only**, for the whole repo; for ingestion, the two API keys. Template: [`.env.example`](.env.example) | **no** (gitignored) | You: `cp .env.example .env` in the repo root and fill in the keys (or `make_account.sh` creates it and fills in the Attendee key) |
| `1_ingestion/attendee/.env` | Attendee's own config (Django secret, credentials encryption key) | **no** | Generated once by setup step 3. Don't edit or share it; losing it means recreating the Attendee database |

The defaults are for Docker. Only two values differ when you run `bridge.py` on your own machine, so those two are environment variables: `ATTENDEE_URL` (set it to `http://localhost:8000`) and `BIND_HOST` (the Docker image sets `0.0.0.0`). `send_bot.sh` and the live view's Send bot tab likewise use `ATTENDEE_URL` and `BRIDGE_HOST` (how the bot reaches the bridge; default `liveminutes-bridge`).

`.env` format: `KEY=value`, one per line, no quotes, no spaces around `=`, `#` for comments.

**Getting the keys**

| Key | Where to get it |
|---|---|
| `ATTENDEE_API_KEY` | **Easiest:** `./make_account.sh you@example.com` (setup step 5) writes it for you. **By hand:** http://localhost:8000 → log in → **API Keys** (left sidebar) → **Create API Key** → copy it (shown only once). The key belongs to your local Attendee, so every teammate makes their own. |
| `GROQ_API_KEY` | https://console.groq.com/keys → sign in (Google/GitHub is fine) → **Create API Key** → copy (starts with `gsk_`). The free tier is enough. Each teammate can use their own key; the free limits are per key. |

**`.env` variables**

| Variable | Required | What |
|---|---|---|
| `ATTENDEE_API_KEY` | **yes** | Attendee API key (above) |
| `GROQ_API_KEY` | **yes** | Groq key for Whisper (above) |
| `ATTENDEE_WEBHOOK_SECRET` | no | Only for `tools/receiver` |

After changing `.env`, restart the bridge: `dc up -d liveminutes-bridge`.

Ports in use: 8000 Attendee, 8775/8776 bridge (8776 also serves the live view), 8765/8766 `tools/receiver`.

### 1.6 Run a meeting

1. **Start or open a meeting** and copy its link.
   - Teams: the organizer must allow anonymous/guest joins. School/work tenants may block this; a personal Teams account is more reliable.
   - Meet: host from a personal Gmail; school accounts may block guest bots.
2. **Send the bot:** open http://localhost:8776/live → **Send bot**, paste the link, press **Send bot**. Its logs appear below the form; if it never gets in, they say why (e.g. `could not join (meeting not found)`). Or from a terminal:
   ```bash
   ./send_bot.sh "<meeting link>"          # prints the bot id, e.g. bot_6Ydolj3iAUxeHn0s
   ```
3. **Admit "LiveMinutes"** from the lobby (it takes 20–60 s to appear).
4. **Consent in chat.** The bot posts a message listing everyone in the meeting. Each person replies `yes` (or `no` to stop and delete). For a meeting room, phone line or another bot, someone present replies with its name and yes: `Conference Room 4B yes`. Recording starts once everyone is covered; a late joiner pauses it until they're covered too.
5. **Open the live view.** Right after the consent message the bot posts `Live transcript (organizer's computer only): http://localhost:8776/live/<bot_id>`. Click it on the computer running the bridge (or open http://localhost:8776/live). It shows the transcript, who has said yes, and everything the bot does.
   Prefer a terminal? `dc logs -f --no-log-prefix liveminutes-bridge` prints the same lines.
6. **Talk.** Lines appear about 1.2–1.5 s after each pause (0.7 s to notice the pause + ~0.5 s Groq), in the live view and in `sessions/<bot_id>/transcript.txt`. Every upload's timings are in `sessions/<bot_id>/pieces.jsonl` (`delay_after_speech_end_s`, `groq_ms`, `label_wait_ms`) and in the Send bot tab's logs.
7. **End:** end the meeting or remove the bot from it. To make it leave from here:
   ```bash
   curl -X POST -H "Authorization: Token $(sed -n 's/^ATTENDEE_API_KEY=//p' ../.env)" http://localhost:8000/api/v1/bots/<bot_id>/leave
   ```
   To rejoin the same meeting, run `./send_bot.sh` again (each join is a new bot id and a new session folder).

### 1.7 Day to day

| Task | Command |
|---|---|
| Start after a reboot | Start Docker Desktop, then `dc up -d` (no rebuild needed) |
| Stop everything | `dc down` (data in Postgres and `sessions/` is kept) |
| After editing any `.py` or `.html` file | `dc up -d --build liveminutes-bridge` (seconds) |
| See sessions / transcripts | http://localhost:8776/live |
| Bridge log only, last 5 min | `dc logs --since 5m liveminutes-bridge` |

Offline tests (consent replies, vouching, chat formatting, live view; no meeting or Groq needed):
```bash
dc build liveminutes-bridge
docker run --rm -v "$(pwd -W)/tests:/app/tests:ro" --entrypoint python liveminutes-bridge:dev -m unittest tests.test_consent tests.test_live   # macOS/Linux: $(pwd)
```

### 1.8 Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Groq 403 ... Access denied. Please check your network settings.` | A VPN (or datacenter network) is on. Groq blocks those IPs. Turn the VPN off; no restart needed. Pieces that failed are lost. |
| `attendee-worker-local` exits, log says `bash\r: No such file or directory` | Attendee was cloned with Windows line endings. In `attendee/`: `git config core.autocrlf false && git rm -q --cached -r . && git reset -q --hard`, then `dc build` and `dc up -d`. |
| Bot never shows up in the lobby | Guest/anonymous join is blocked for that meeting or tenant. Check `dc logs attendee-worker-local`. Try a personal account. |
| `send_bot.sh` returns `401` / `Invalid token` | `ATTENDEE_API_KEY` in the repo's `.env` is empty or stale. Run `./make_account.sh <email>` again. |
| Consent never completes | Someone (or a room/bot) hasn't been covered. The bot reminds after 2 min and leaves after 4. Answer for rooms/bots with `<name> yes`. |
| `make_certs.sh` fails on Windows with a subject error | Run it from Git Bash, not PowerShell. |

**Without Docker (bridge on the host).** For quick debugging you can run `bridge.py` directly instead of the container: `uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r ../requirements.txt`, then `ATTENDEE_URL=http://localhost:8000 .venv/bin/python bridge.py`, and send bots with `BRIDGE_HOST=host.docker.internal ./send_bot.sh <link>`. Stop the bridge container first (`dc stop liveminutes-bridge`); don't run both at once.

### 1.9 Known gaps

- Live tests so far: Meet (~1 min, one person on two devices) and Teams (3 short calls, one person, 2026-11-06). A 10+ minute test with two real people is still needed.
- Teams two-speaker labelling and room/bot vouching are untested live (vouching is covered by `tests/test_consent.py`).
- A piece Groq rejects (e.g. 403 on VPN) is dropped, not retried.
- Whisper still produces "Thank you." on noisy Teams audio. A VAD (e.g. Silero) before upload would fix this.
- Attendee's local mode runs all bots in one worker. Production needs one bot per pod (see [7. Infra](#7-infra)).
- Licence: Attendee is Elastic License 2.0. Building on it is fine, but we can't host Attendee itself as a service for others.

---

## 2. Summarization

Folder: [2_summarization/](2_summarization/). Feature 1: summarize the meeting while it runs, from `1_ingestion/sessions/<bot_id>/transcript.txt`.

- **Baseline:** plain TextRank (extractive).
- **Compare:** extractive vs abstractive approaches.
- **Metrics:** ROUGE, BERTScore (harness in `6_evaluation/summarization/`, see [6](#6-evaluation)).
- **Storyboard B slice:** incorrect summarization (what a wrong summary looks like, and how we catch or flag it).
- **Data:** AMI abstractive/extractive summaries + synthetic edge-case meetings ([5](#5-data)).

## 3. Action items

Folder: [3_action_items/](3_action_items/). Feature 2: from `1_ingestion/sessions/<bot_id>/transcript.txt`, extract each action item: **task**, **owner**, **due date**, and the line it came from.

- **Baseline:** rule-based phrase patterns.
- **Compare:** rules vs LLM extraction.
- **Metrics:** item precision / recall / F1, owner and due-date accuracy ([6](#6-evaluation)).
- **Data:** gold action items per meeting in `5_data/synthetic/action_items/`.

## 4. Q&A (knowledge base)

Folder: [4_qa/](4_qa/). Feature 3: question → retrieve chunks from past meeting transcripts → API-hosted answer LLM → answer with cited meeting/time.

| Approach | Folder | What |
|---|---|---|
| BM25 (baseline) | `4_qa/retrieval/bm25/` | Lexical baseline over transcript chunks. Also the candidate generator for the reranker |
| LightGBM reranker | `4_qa/retrieval/reranker/` | Reranks top-k candidates (e.g. from BM25) with LightGBM learning-to-rank (lambdarank). Features: BM25 score, term overlap, speaker/time metadata |
| Vector retrieval | `4_qa/retrieval/vector/` | Dense embeddings over transcript chunks |
| Neo4j graph | `4_qa/retrieval/graph/` | Meetings, speakers, topics and utterances as a graph in Neo4j |
| Answer generation | `4_qa/answer/` | Takes retrieved chunks and calls an API-hosted LLM to write the answer, citing meeting and timestamp |

**Shared retriever contract** (so every row of the comparison table runs through the same evaluator):
`retrieve(question: str, k: int) -> list[{"meeting_id", "chunk_id", "text", "score"}]`

Evaluation: LLM-generated, manually validated Q&A pairs (`5_data/synthetic/qa/`). Correctness is scored by LLM-as-judge plus human review (`6_evaluation/qa/`).

## 5. Data

Folder: [5_data/](5_data/).

| Folder | Contents | In git? |
|---|---|---|
| `raw/` | AMI Meeting Corpus downloads (transcripts, summaries, annotations) | No |
| `processed/` | AMI converted to the shared transcript format `[mm:ss] Name: text`, plus chunked versions for retrieval | No |
| `synthetic/meetings/` | LLM-generated edge-case meetings (AMI is too clean), each human-reviewed | Yes, once reviewed |
| `synthetic/action_items/` | Gold action items per meeting, `<meeting>.gold.json` | Yes, once reviewed |
| `synthetic/qa/` | LLM-generated Q&A pairs, manually validated | Yes, once validated |

Rules:
- Every synthetic item records: generator model + prompt, reviewer, review date, accept/reject.
- Keep fixed train/dev/test splits by meeting ID, so nothing leaks between reranker training and evaluation.
- Real recorded meetings (from `1_ingestion/sessions/`) never go in here without everyone's consent.

## 6. Evaluation

Folder: [6_evaluation/](6_evaluation/).

- `summarization/`: ROUGE + BERTScore for Feature 1 (TextRank baseline vs extractive vs abstractive).
- Feature 2 (action items): item P/R/F1, owner and due accuracy (harness to be added).
- `qa/`: retrieval metrics (Recall@k, MRR, nDCG) per retriever, then answer correctness via LLM-as-judge + human review.

Every approach is run through the same script on the same split, and the results go into one comparison table.

## 7. Infra

Folder: [7_infra/aws/](7_infra/aws/). AWS deployment on new-account free credits. Things to plan for, from ingestion:

- Each Attendee bot runs a Chrome (~1 CPU, 1–2 GB RAM). Local mode runs all bots in one worker; for production, run one bot per container/pod.
- The bridge needs a real TLS cert for `wss://` (replacing `make_certs.sh`).
- The bridge already ships as an image (`1_ingestion/Dockerfile`, `liveminutes-bridge`). Push it to ECR and run it next to the Attendee worker; `sessions/` must be a persistent volume (or move to S3).
- Secrets go in AWS Secrets Manager / SSM, never in the image.

---

## Origin

`1_ingestion/` is the `attendee-groq-bridge/` system from [aatmaj28/ai-capstone](https://github.com/aatmaj28/ai-capstone) (commit `1854b3b`), since extended here (Docker image, consent vouching, transcript merging). The Vexa prototype from that repo was not carried over.
