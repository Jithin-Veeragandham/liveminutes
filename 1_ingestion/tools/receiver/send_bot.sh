#!/usr/bin/env bash
# Dev tool: send an Attendee bot to a Google Meet, streaming raw audio + events to receiver.py.
# No consent step. Only use with people who agreed to be recorded.
# Usage: ./send_bot.sh https://meet.google.com/abc-defg-hij ["Bot Name"]
set -euo pipefail
cd "$(dirname "$0")"
set -a; source ../../../.env; set +a   # the repo's .env: ATTENDEE_API_KEY
MEET_URL="${1:?usage: ./send_bot.sh <meet url> [bot name]}"
BOT_NAME="${2:-LiveMinutes Test Bot}"
: "${ATTENDEE_API_KEY:?put ATTENDEE_API_KEY in the repo's .env}"
WS_URL="wss://host.docker.internal:${RECEIVER_WS_PORT:-8765}/attendee-websocket"
HOOK_URL="http://host.docker.internal:${RECEIVER_WEBHOOK_PORT:-8766}/webhook"

curl -sS -X POST "${ATTENDEE_URL:-http://localhost:8000}/api/v1/bots" \
  -H "Authorization: Token ${ATTENDEE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d @- <<EOF
{
  "meeting_url": "${MEET_URL}",
  "bot_name": "${BOT_NAME}",
  "recording_settings": {"format": "none"},
  "websocket_settings": {
    "audio": {"url": "${WS_URL}", "sample_rate": 16000},
    "per_participant_audio": {"url": "${WS_URL}", "sample_rate": 16000}
  },
  "webhooks": [{
    "url": "${HOOK_URL}",
    "triggers": ["bot.state_change", "participant_events.join_leave",
                 "participant_events.speech_start_stop", "transcript.update", "bot_logs.update"]
  }]
}
EOF
echo
