#!/usr/bin/env bash
# Share the participants' live view through a free Cloudflare quick tunnel (no account needed).
#   bash tunnel.sh            (from 1_ingestion/, with the bridge running: dc up -d)
# Needs cloudflared: macOS `brew install cloudflared`; others: see docs/UI.md.
# It starts `cloudflared tunnel --url http://localhost:8776`, reads its https://<random>.trycloudflare.com
# address and tells the running bridge to use it in the links it posts (no restart needed).
# Only the participant pages (/m/...) answer through the tunnel; the organizer pages stay on this computer.
# The bridge picks the address up while it runs, for the links it posts and for its Origin check on writes.
# Ctrl+C stops the tunnel, and links go back to localhost (unless another tunnel.sh set a newer address).
set -euo pipefail
BRIDGE="${BRIDGE_URL:-http://localhost:8776}"
command -v cloudflared >/dev/null || { echo "cloudflared isn't installed. macOS: brew install cloudflared (others: docs/UI.md)"; exit 1; }
curl -fsS "$BRIDGE/health" >/dev/null || { echo "The bridge isn't running at $BRIDGE. Start it first: dc up -d"; exit 1; }

# set_url <address> [only if the bridge still uses this one]
set_url() { curl -fsS -X POST -H "Content-Type: application/json" \
              -d "{\"url\": \"$1\"${2:+, \"only_if\": \"$2\"}}" "$BRIDGE/live/api/public-url" >/dev/null; }
log="$(mktemp)"
url=""
cloudflared tunnel --no-autoupdate --url "$BRIDGE" >"$log" 2>&1 &
pid=$!
# On exit, clear the address only if it is still ours: an older tunnel.sh stopping late must not wipe
# the address of a newer one (links would quietly fall back to localhost).
trap 'kill $pid 2>/dev/null || true; [ -z "$url" ] || set_url "" "$url" || true; rm -f "$log"' EXIT

for _ in $(seq 1 60); do
  url="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$log" | head -1 || true)"
  [ -n "$url" ] && break
  kill -0 $pid 2>/dev/null || break
  sleep 1
done
if [ -z "$url" ]; then echo "No tunnel address after 60 s. cloudflared said:"; tail -20 "$log"; exit 1; fi
set_url "$url"   # the running bridge uses it at once (and keeps it in sessions/.ui_public_url): no restart
# A new quick tunnel takes a few seconds before it answers; wait so the first link posted works.
for _ in $(seq 1 30); do curl -fsS -o /dev/null "$url/" 2>/dev/null && break; sleep 1; done
echo "Participants' links now use: $url"
echo "Opening $url/ shows a short note; meeting links look like $url/m/<meeting>."
echo "Quick tunnels don't pass the live stream on, so pages use polling there (shown as \"Live · polling\")."
echo "Keep this window open during the meeting. Ctrl+C stops the tunnel."
wait $pid
