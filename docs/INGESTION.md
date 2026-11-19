# 1. Ingestion (meeting bot): full reference

Folder: [1_ingestion/](../1_ingestion/). The open-source [Attendee](https://github.com/attendee-labs/attendee) meeting bot joins Google Meet or Teams and streams audio to `bridge.py`, which runs the consent gate, cuts audio at pauses, and transcribes each piece with Groq `whisper-large-v3-turbo`.

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
   3. upload each piece to Groq (max 19/min and 7,000 billed s/hour, fits the free tier)
   ▼
"[mm:ss] Name: text" printed live + sessions/<bot_id>/transcript.txt
```

## 1.1 Files

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
| `Dockerfile` + `Dockerfile.dockerignore` | Image for the bridge (`liveminutes-bridge`): python:3.12-slim, non-root, `/health` check. Built from the repo root for the shared `requirements.txt`, and installs only its section 1 (ingestion), so torch stays out |
| `compose.local.yaml` | Our additions to Attendee's compose: the bridge container, the bot trusting our certs, two Attendee settings |
| `../.env.example`, `../requirements.txt` | At the repo root, shared by all components: the API key template (see [1.5](#15-configuration)) and Python packages |
| `tests/test_consent.py` | Offline unit tests for consent replies, vouching and chat formatting (Attendee mocked, no Groq) |
| `tests/test_live.py` | Offline unit tests for the live view: page data, Send bot (link checks, request body, cross-site refusal) and the merged bot logs |
| `tests/consent_sim.py` | Offline: mock Attendee API + fake webhooks/audio. Scenarios `yes_late`, `no`, `timeout`, `teams` |
| `tests/replay_to_bridge.py` | Offline: replays a WAV as two fake Meet participants |
| `tools/receiver/` | Raw per-speaker WAV recorder + offline faster-whisper. **No consent step.** |
| `patches/teams-speaker-clock.patch` | Our one change to Attendee, applied at setup: fixes Teams speaker detection (see setup step 1) |
| `attendee/` | Attendee itself, cloned at setup (gitignored) |

## 1.2 How the bridge works

- **Meet:** one audio stream per person. The name comes from the stream, so it never has to guess who spoke.
- **Teams:** one mixed stream. A piece ends at a pause **or where a different person starts talking**: when a speech-start event arrives and the piece being built already holds someone else's speech, it is cut at that event's timestamp (`cut_at_speaker_change`). The event comes ~0.5 s late, but the piece is usually still being built then. Each piece is labelled from speech start/stop events: a name, `Multiple speakers (A, B)` (now mostly when two people talk at the same moment, which one mixed stream can't separate), or `Unknown speaker`. Those events arrive ~0.7 s after the audio, so a piece goes to Groq at once and is named when Groq answers (waiting out what's left of `LABEL_GRACE_MS`, 1.2 s after speech end, usually nothing). Before, it waited the full 1.2 s and only then uploaded.
- **Consent** (on by default, `--no-consent` for tests):
  - Audio is dropped on arrival until **everyone present** has replied yes. The bot is also paused inside Attendee.
  - The chat message lists everyone in the meeting, one per line. Meeting rooms, phone lines and other bots can't type, so someone present answers for them with the name and yes: `Conference Room 4B yes` (also `yes for Room 4B`, or part of a name if it fits only one participant). The bot confirms ("Noted: yes for …, from …") and `consent.jsonl` records who vouched.
  - Late joiner → everyone paused until they (or someone for them) say yes.
  - Anyone says no, for themselves or as `<name> no` → "Recording stopped.", the bot leaves, and the session is deleted here and in Attendee (only `consent.jsonl` is kept).
  - No answer → reminder after 2 min, leave after 2 more.
- **Groq pacing:** pieces end at a 0.7 s pause or at 10 s (the billing minimum). Pieces with < 0.3 s of speech are skipped. Uploads stay inside both of Groq's free-tier limits: at most 19 per rolling minute (Groq: 20) and 7,000 billed audio-seconds per rolling hour (Groq: 7,200; every upload counts as at least 10 s). The old flat 12/min queued lines for up to 50 s in 3–5 person meetings, which needed up to 16 uploads/min. Backlog is still merged and 429s retried. Groq also allows 2,000 uploads a day; a 30 min meeting uses ~320 (`left_req_day` in `pieces.jsonl` shows what's left).
- **Context:** each speaker's last 40 words go in as the Whisper `prompt`.
- **Merging:** back-to-back pieces from the same speaker with ≤ 3 s pause become one transcript line (`MERGE_GAP_MS` in `settings.py`).
- **Live notes:** every 30 s (`NOTES_INTERVAL_S`) the bridge sends the transcript lines that came in since the last update, plus the notes so far, to the LLM summarizer (`notes.py` → `2_summarization/llm_summarizer.py`; provider, model and key from `.env`, default Gemini `gemini-3.5-flash-lite`). `/live/<bot_id>` shows a **Live summary** and **Action items** (task, owner, due), a countdown bar to the next update, the LLM rate-limit use (calls this minute / today, capped by `NOTES_RPM_LIMIT`), and **Sent to the LLM**: one entry per call with the transcript range, line and word counts, time taken, tokens, and the exact text sent. Saved in `sessions/<bot_id>/notes.json`; one last update runs when the meeting ends. No key → the bridge runs as before and the page says why. Demo without a meeting: `python tests/replay_transcript.py sessions/<bot_id>/transcript.txt`, then open http://localhost:8777/live/bot_replayDemo.
- **Live view:** http://localhost:8776/live has two tabs. **Sessions** lists sessions. **Send bot** takes a Meet or Teams link and a bot name, sends the bot (the same request as `send_bot.sh`), then shows its logs, updating every 2 s: Attendee's own record of the bot (joining, waiting room, fatal errors and why), what the bridge saw (webhooks, people, consent, chat) and every Groq upload, with filters per source. Any session's logs open from its **logs** link. Only the page itself can send bots: the endpoint takes JSON only, from `localhost` (or `LIVE_VIEW_URL`), so another website can't use your Attendee key. `/live/<bot_id>` shows the transcript, who has consented and the bot's activity (state changes, its chat messages, replies, Groq errors). It long-polls: the bridge answers the page the moment a line or event comes in, instead of the page asking once a second. The bot posts this link in the meeting chat after the consent message. It's `localhost`, so it opens only on the computer running the bridge. Finished sessions are read back from `sessions/`.

## 1.3 Output (what downstream components read)

`1_ingestion/sessions/<bot_id>/` (gitignored):

| File | Contents |
|---|---|
| `transcript.txt` | `[mm:ss] Name: text`, sorted by start time, same-speaker runs merged. **This is the input for [2](SUMMARIZATION.md), [3](PROJECT.md#3-action-items) and [4](PROJECT.md#4-qa-knowledge-base).** |
| `pieces.jsonl` | Per upload: speaker, timings (`start_ms`, `speech_end_ms`, `done_ms`), audio length, text, dropped hallucinations |
| `consent.jsonl` | Every consent event: who, what they typed, decision, who vouched, time |
| `events.jsonl` | Raw Attendee webhooks |
| `captions.txt` | The platform's own captions, for comparison |
| `pieces/NNNN.wav` | The uploaded audio pieces |

## 1.4 Setup (once per machine)

Everything runs in Docker: Attendee (app, worker, scheduler, Postgres, Redis) and the bridge are one compose project on one network. The bot reaches the bridge as `liveminutes-bridge`, and the bridge calls Attendee as `attendee-app-local`. That is the same layout you'd use when deploying (see [7. Infra](PROJECT.md#7-infra)).

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

**2. Create the repo's `.env`.** It must exist before any `dc` command: `compose.local.yaml` loads it for the bridge, and `dc exec` (step 4) fails with `env file .../.env not found` without it.
```bash
[ -f ../.env ] || cp ../.env.example ../.env
grep -v '^ATTENDEE_API_KEY=' ../.env > ../.env.tmp && mv ../.env.tmp ../.env
```
The `grep` line removes an old `ATTENDEE_API_KEY=` line, so `make_account.sh` (step 5) appends the new key. That's needed on macOS: the script's in-place edit (`sed -i`) only works with GNU sed, and without this line the key is created in Attendee but never written to `.env`.

**2b. Build the images** (Attendee + bridge). The first build takes 10–20 min (Chrome, X server, audio libraries). Later builds are cached.
```bash
dc build
```

**3. Create Attendee's settings and the local TLS certs** (Attendee only sends audio to `wss://`):
```bash
dc run --rm -T attendee-app-local python init_env.py > attendee/.env
bash make_certs.sh
```
**One Attendee database per computer.** Docker names Attendee's database volume `attendee_postgres` in every clone (the compose project is always the folder name `attendee`). `attendee/.env` holds the key that encrypts each project's webhook secret in that database. So if this computer already ran Attendee from another clone (`docker volume ls` shows `attendee_postgres`), copy that clone's `attendee/.env` here instead of running `init_env.py` again.

**4. Start Attendee and set up its database:**
```bash
dc up -d postgres redis attendee-app-local attendee-worker-local attendee-scheduler-local
dc exec -T attendee-app-local python manage.py migrate
```
Check: http://localhost:8000 shows Attendee's login page.

**5. Create your Attendee login and API key.** This makes the account with the email already verified (no confirmation link), creates an API key and writes it to the repo's `.env` (made in step 2) as `ATTENDEE_API_KEY`. It prints the password for http://localhost:8000.
```bash
bash make_account.sh you@example.com
```
(Alternative: sign up in the browser at http://localhost:8000/accounts/signup/, get the confirmation link with `dc logs attendee-app-local | grep confirm-email`, create a key under **API Keys**, and paste it into `.env`.)

**6. Add your Groq key** to `.env` in the repo root: `GROQ_API_KEY=gsk_...` (get one at https://console.groq.com/keys; see [1.5](#15-configuration)). The file was created from `.env.example` in step 2. Then start the bridge:
```bash
dc up -d liveminutes-bridge
```

**7. Check it's all up:**
```bash
dc ps                                   # 6 services "Up"; liveminutes-bridge "(healthy)"
curl http://127.0.0.1:8776/health       # -> ok
```

## 1.5 Configuration

Settings live in three files:

| File | What | In git? | Who writes it |
|---|---|---|---|
| [`1_ingestion/settings.py`](../1_ingestion/settings.py) | **Everything that isn't a secret:** ports, Groq model and rate limit, audio cutting, transcript merging, consent timers, live view link. One commented line per setting | yes | You, when you want to change a setting. Then `dc up -d --build liveminutes-bridge` (seconds) |
| `.env` (repo root) | **Secrets only**, for the whole repo; for ingestion, the two API keys. Template: [`.env.example`](../.env.example) | **no** (gitignored) | You: `cp .env.example .env` in the repo root and fill in the keys (or `make_account.sh` creates it and fills in the Attendee key) |
| `1_ingestion/attendee/.env` | Attendee's own config (Django secret, credentials encryption key) | **no** | Generated once by setup step 3. Don't edit or share it; losing it means recreating the Attendee database |

The defaults are for Docker. Only two values differ when you run `bridge.py` on your own machine, so those two are environment variables: `ATTENDEE_URL` (set it to `http://localhost:8000`) and `BIND_HOST` (the Docker image sets `0.0.0.0`). `send_bot.sh` and the live view's Send bot tab likewise use `ATTENDEE_URL` and `BRIDGE_HOST` (how the bot reaches the bridge; default `liveminutes-bridge`).

`.env` format: `KEY=value`, one per line, no quotes, no spaces around `=`, `#` for comments.

**Getting the keys**

| Key | Where to get it |
|---|---|
| `ATTENDEE_API_KEY` | **Easiest:** `bash make_account.sh you@example.com` (setup step 5) writes it for you. **By hand:** http://localhost:8000 → log in → **API Keys** (left sidebar) → **Create API Key** → copy it (shown only once). The key belongs to your local Attendee, so every teammate makes their own. |
| `GROQ_API_KEY` | https://console.groq.com/keys → sign in (Google/GitHub is fine) → **Create API Key** → copy (starts with `gsk_`). The free tier is enough. Each teammate can use their own key; the free limits are per key. |

**`.env` variables**

| Variable | Required | What |
|---|---|---|
| `ATTENDEE_API_KEY` | **yes** | Attendee API key (above) |
| `GROQ_API_KEY` | **yes** | Groq key for Whisper (above) |
| `ATTENDEE_WEBHOOK_SECRET` | no | Only for `tools/receiver` |

After changing `.env`, restart the bridge: `dc up -d liveminutes-bridge`.

Ports in use: 8000 Attendee, 8775/8776 bridge (8776 also serves the live view), 8765/8766 `tools/receiver`.

## 1.6 Run a meeting

1. **Start or open a meeting** and copy its link.
   - Teams: the organizer must allow anonymous/guest joins. School/work tenants may block this; a personal Teams account is more reliable.
   - Meet: host from a personal Gmail; school accounts may block guest bots.
2. **Send the bot:** open http://localhost:8776/live → **Send bot**, paste the link, press **Send bot**. Its logs appear below the form; if it never gets in, they say why (e.g. `could not join (meeting not found)`). Or from a terminal:
   ```bash
   bash send_bot.sh "<meeting link>"            # prints the bot id, e.g. bot_6Ydolj3iAUxeHn0s
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
   To rejoin the same meeting, run `bash send_bot.sh` again (each join is a new bot id and a new session folder).

## 1.7 Day to day

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
docker run --rm -v "$(pwd)/tests:/app/tests:ro" --entrypoint python liveminutes-bridge:dev -m unittest tests.test_consent tests.test_live   # Windows Git Bash: $(pwd -W)
```

## 1.8 Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Groq 403 ... Access denied. Please check your network settings.` | A VPN (or datacenter network) is on. Groq blocks those IPs. Turn the VPN off; no restart needed. Pieces that failed are lost. |
| `attendee-worker-local` exits, log says `bash\r: No such file or directory` | Attendee was cloned with Windows line endings. In `attendee/`: `git config core.autocrlf false && git rm -q --cached -r . && git reset -q --hard`, then `dc build` and `dc up -d`. |
| Bot never shows up in the lobby | Guest/anonymous join is blocked for that meeting or tenant. Check `dc logs attendee-worker-local`. Try a personal account. |
| `send_bot.sh` returns `401` / `Invalid token` | `ATTENDEE_API_KEY` in the repo's `.env` is empty or stale. Run `bash make_account.sh <email>` again. |
| Consent never completes | Someone (or a room/bot) hasn't been covered. The bot reminds after 2 min and leaves after 4. Answer for rooms/bots with `<name> yes`. |
| `make_certs.sh` fails on Windows with a subject error | Run it from Git Bash, not PowerShell. |
| `./make_certs.sh: permission denied` (macOS/Linux) | The scripts are committed without the executable bit. Run them with `bash`, e.g. `bash make_certs.sh`. |
| `Bind for 127.0.0.1:8000 failed: port is already allocated` | Another container or program uses port 8000. Find it with `docker ps` (look for `:8000->`) and stop it, then `dc up -d` again. |
| `liveminutes-bridge` keeps restarting, log says `GROQ_API_KEY is missing` | Put your Groq key in the repo's `.env`, then `dc up -d liveminutes-bridge`. The live view (port 8776) is served by the bridge, so it is down until then. |

**Without Docker (bridge on the host).** For quick debugging you can run `bridge.py` directly instead of the container: `uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r ../requirements.txt`, then `ATTENDEE_URL=http://localhost:8000 .venv/bin/python bridge.py`, and send bots with `BRIDGE_HOST=host.docker.internal bash send_bot.sh <link>`. Stop the bridge container first (`dc stop liveminutes-bridge`); don't run both at once.

## 1.9 Known gaps

- Live tests so far: Meet (~1 min, one person on two devices) and Teams (3 short calls, one person, 2026-11-06). A 10+ minute test with two real people is still needed.
- Teams two-speaker labelling and room/bot vouching are untested live (vouching is covered by `tests/test_consent.py`).
- A piece Groq rejects (e.g. 403 on VPN) is dropped, not retried.
- Whisper still produces "Thank you." on noisy Teams audio. A VAD (e.g. Silero) before upload would fix this.
- Attendee's local mode runs all bots in one worker. Production needs one bot per pod (see [7. Infra](PROJECT.md#7-infra)).
- Licence: Attendee is Elastic License 2.0. Building on it is fine, but we can't host Attendee itself as a service for others.
