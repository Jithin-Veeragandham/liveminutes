#!/usr/bin/env bash
# Attendee only accepts wss:// websocket URLs. This makes a local CA and a
# cert for the bridge (as the liveminutes-bridge container, or bridge.py on
# this machine via host.docker.internal) so the bot can reach it over TLS. Run it once, after the Attendee
# image is built (it copies the image's CA list).
#
# certs/server.{crt,key}          used by bridge.py (and tools/receiver)
# certs/container/ca-bundle.pem   system CAs + our CA, mounted into the worker
#                                 container as SSL_CERT_FILE (see compose.local.yaml)
set -euo pipefail
export MSYS_NO_PATHCONV=1   # Git Bash on Windows: keep -subj "/CN=..." from being turned into a path
cd "$(dirname "$0")"
mkdir -p certs/container
cd certs

if [[ ! -f ca.crt ]]; then
  openssl req -x509 -newkey rsa:2048 -nodes -days 365 -sha256 \
    -subj "/CN=LiveMinutes Local Test CA" \
    -keyout ca.key -out ca.crt 2>/dev/null
fi

cat > server.ext <<'EOF'
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:liveminutes-bridge,DNS:host.docker.internal,DNS:host.lima.internal,DNS:localhost,IP:127.0.0.1,IP:192.168.5.2
EOF
openssl req -newkey rsa:2048 -nodes -sha256 -subj "/CN=host.docker.internal" \
  -keyout server.key -out server.csr 2>/dev/null
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -days 365 -sha256 -extfile server.ext -out server.crt 2>/dev/null
rm -f server.csr server.ext
chmod 600 ca.key server.key

# Keep the container's normal CAs so its other HTTPS calls still verify.
(cd ../attendee && docker compose -f dev.docker-compose.yaml run --rm --no-deps -T \
  --entrypoint cat attendee-worker-local /etc/ssl/certs/ca-certificates.crt) > container/ca-bundle.pem
cat ca.crt >> container/ca-bundle.pem

echo "certs ready: $(openssl x509 -in server.crt -noout -subject)"
