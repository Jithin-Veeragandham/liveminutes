"""All bridge settings in one place. After editing: dc up -d --build liveminutes-bridge"""

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENV_FILE = HERE.parent / ".env"  # shared by the whole repo

# Secrets (GROQ_API_KEY, ATTENDEE_API_KEY) come from the repo's .env, see .env.example.
# In Docker, compose passes them in as environment variables instead.
if ENV_FILE.exists():
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
ATTENDEE_API_KEY = os.environ.get("ATTENDEE_API_KEY", "")

# ---- Where things are ----
# Defaults are for Docker. Running bridge.py on your own machine: set ATTENDEE_URL=http://localhost:8000.
ATTENDEE_URL = os.environ.get("ATTENDEE_URL", "http://attendee-app-local:8000")
BIND_HOST = os.environ.get("BIND_HOST", "127.0.0.1")  # the Docker image sets 0.0.0.0
WS_PORT = 8775          # audio from the bot, wss:// (Attendee only streams to TLS websockets)
WEBHOOK_PORT = 8776     # webhooks from Attendee + the live view, plain http://
CERT_DIR = HERE / "certs"         # made by make_certs.sh
SESSIONS_DIR = HERE / "sessions"  # one folder per bot: transcript.txt, pieces.jsonl, consent.jsonl, ...
LIVE_VIEW_URL = "http://localhost:8776"  # posted in the meeting chat as <url>/live/<bot_id>; "" = don't post
# The bridge as the bot (inside Docker) reaches it. Used by the live view's "Send bot" tab, like send_bot.sh.
# Bridge running on your own machine instead of in Docker: BRIDGE_HOST=host.docker.internal
BRIDGE_HOST = os.environ.get("BRIDGE_HOST", "liveminutes-bridge")

# ---- Groq speech-to-text (free tier: 20 requests/min, 7,200 audio-seconds/hour, 10 s billed minimum) ----
GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3-turbo"
# Groq's limits are 20/min AND 7,200 billed s/hour, so bursts above 12/min are fine as long as the
# hour's audio budget holds. Meetings of 3-5 people needed up to 16 uploads/min (Nov 2026 logs):
# with the old flat 12/min, lines queued for up to 50 s.
RPM_LIMIT = 19             # uploads per rolling minute across all speakers (Groq: 20)
AUDIO_S_PER_HOUR = 7000    # billed audio-seconds per rolling hour (Groq: 7,200; each upload >= 10 s)
BILLED_MIN_S = 10          # Groq bills every upload as at least this long
MAX_IN_FLIGHT = 3          # uploads running at the same time
# Also: 2,000 uploads per day (x-ratelimit-remaining-requests in pieces.jsonl). A 30 min meeting used ~320.
# Whisper invents these on near-silence; dropped when Groq also thinks it may not be speech.
HALLUCINATIONS = {"thank you.", "thanks for watching!", "thank you for watching.", "you", "bye.", "."}

# ---- Cutting live audio into pieces for upload ----
RATE = 16000           # samples per second (16-bit mono), what Attendee sends and Whisper wants
SPEECH_DBFS = -45.0    # a chunk louder than this counts as speech
MIN_VOICED_MS = 300    # a piece with less speech than this is a click or noise: skipped
PAUSE_MS = 700         # this much quiet ends a piece...
MIN_PIECE_S = 2.0      # ...once it is at least this long
END_OF_TURN_MS = 1500  # this much quiet ends even a short piece
MAX_PIECE_S = 10.0     # cut here at the latest (Groq bills 10 s anyway)
MAX_MERGED_S = 25.0    # pieces that queued up behind the rate limit are merged up to this length
PREROLL_MS = 200       # keep this much audio from just before speech starts

# ---- Transcript ----
MERGE_GAP_MS = 3000    # same speaker, pause up to this: one transcript line (0 = one line per piece)
LABEL_GRACE_MS = 1200  # Teams: wait this long after a piece for late "who is speaking" webhooks
MIN_TALK_MS = 400      # Teams: someone must talk this long in a piece (and 25% of it) to be named

# ---- Consent ----
CONSENT_REQUIRED = "--no-consent" not in sys.argv  # bridge.py --no-consent records without asking (tests)
# Environment variables only so tests/consent_sim.py can use short timers.
REMIND_AFTER_S = int(os.environ.get("CONSENT_REMIND_S", 120))  # remind people who haven't answered
LEAVE_AFTER_S = int(os.environ.get("CONSENT_LEAVE_S", 240))    # then the bot leaves

# ---- Live notes: summary + action items from the LLM during the meeting (notes.py) ----
# Uses 2_summarization/llm_summarizer.py. Provider, model and key come from the repo's .env
# (LIVEMINUTES_LLM_PROVIDER, LIVEMINUTES_LLM_MODEL, GEMINI_API_KEY or ANTHROPIC_API_KEY; see .env.example).
NOTES_ENABLED = os.environ.get("LIVE_NOTES", "1") != "0"   # LIVE_NOTES=0 in .env turns them off
NOTES_INTERVAL_S = 30      # one update per 30 s of meeting: the new lines + the notes so far
NOTES_SETTLE_MS = 5000     # lines from the last 5 s wait for the next update (they can still arrive out of order)
# Our own caps, shown as the rate-limit bars on the live page. At one call per 30 s a meeting uses ~2/min
# and ~120/hour. Set these to your key's real free-tier limits (Google AI Studio > Usage) if they are lower.
NOTES_RPM_LIMIT = 10       # calls per rolling minute, all meetings together (updates wait if reached)
NOTES_RPD_LIMIT = 1000     # calls per rolling day, shown on the page only
# Where llm_summarizer.py is: the repo's 2_summarization/ when run from the repo, ./summarization/ in Docker.
SUMMARIZATION_DIR = (HERE.parent / "2_summarization" if (HERE.parent / "2_summarization").is_dir()
                     else HERE / "summarization")
