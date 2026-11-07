#!/usr/bin/env bash
# Send an Attendee bot to a Google Meet or Teams meeting. Its audio and events go to bridge.py.
# Usage: ./send_bot.sh <meeting link> [bot name]
# The live view's "Send bot" tab sends the same request (live_view.bot_payload): change both together.
set -eu
cd "$(dirname "$0")"
source ../.env                                     # the repo's .env: ATTENDEE_API_KEY

LINK="${1:?usage: ./send_bot.sh <meeting link> [bot name]}"
NAME="${2:-LiveMinutes}"
ATTENDEE="${ATTENDEE_URL:-http://localhost:8000}"  # Attendee, as seen from this computer
BRIDGE="${BRIDGE_HOST:-liveminutes-bridge}"        # the bridge, as seen from the bot inside Docker

# Meet gives one audio stream per person. Teams gives one mixed stream, so the bridge
# also needs "who is speaking" events to put names on it.
case "$LINK" in
  *teams.*) AUDIO=audio;                 SPEECH_EVENTS=true ;;
  *)        AUDIO=per_participant_audio; SPEECH_EVENTS=false ;;
esac

curl "$ATTENDEE/api/v1/bots" \
  -H "Authorization: Token $ATTENDEE_API_KEY" \
  -H "Content-Type: application/json" \
  -d @- <<EOF
{
  "meeting_url": "$LINK",
  "bot_name": "$NAME",
  "metadata": {"bot_name": "$NAME"},
  "recording_settings": {
    "format": "none",
    "record_chat_messages_when_paused": true,
    "record_participant_speech_start_stop_events": $SPEECH_EVENTS
  },
  "websocket_settings": {"$AUDIO": {"url": "wss://$BRIDGE:8775/attendee-websocket"}},
  "webhooks": [{
    "url": "http://$BRIDGE:8776/webhook",
    "triggers": ["bot.state_change", "participant_events.join_leave", "chat_messages.update",
                 "participant_events.speech_start_stop", "transcript.update"]
  }]
}
EOF
echo
