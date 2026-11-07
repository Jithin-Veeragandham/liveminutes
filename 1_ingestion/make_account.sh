#!/usr/bin/env bash
# Create (or reset) a local Attendee login with its email already verified, make an API key,
# and write it to the repo's .env as ATTENDEE_API_KEY. Replaces the browser sign-up + confirmation link.
# Attendee must be running and migrated (README section 1.4, step 4).
# Usage: ./make_account.sh you@example.com
set -euo pipefail
export MSYS_NO_PATHCONV=1   # Git Bash on Windows
cd "$(dirname "$0")"
EMAIL="${1:?usage: ./make_account.sh <email>}"
ENV_FILE=../.env                       # shared by the whole repo
[[ -f $ENV_FILE ]] || cp ../.env.example $ENV_FILE

OUT=$(cd attendee && docker compose -f dev.docker-compose.yaml -f ../compose.local.yaml \
  exec -T -e LM_EMAIL="$EMAIL" attendee-app-local python manage.py shell <<'EOF'
import os, secrets
from accounts.models import User
from allauth.account.models import EmailAddress
from bots.models import Project, ApiKey
email = os.environ["LM_EMAIL"]
pw = secrets.token_urlsafe(12)
u = User.objects.filter(email=email).first() or User(email=email)
u.set_password(pw)
u.save()
EmailAddress.objects.update_or_create(user=u, email=email, defaults={"verified": True, "primary": True})
project = Project.objects.filter(organization=u.organization).first()
_, key = ApiKey.create(project=project, name="liveminutes-bridge")
print("PW=" + pw)
print("KEY=" + key)
EOF
)
KEY=$(sed -n 's/^KEY=//p' <<<"$OUT")
PW=$(sed -n 's/^PW=//p' <<<"$OUT")
[[ -n "$KEY" ]] || { echo "$OUT"; echo "failed: is Attendee running and migrated?"; exit 1; }

if grep -q '^ATTENDEE_API_KEY=' $ENV_FILE; then
  sed -i "s|^ATTENDEE_API_KEY=.*|ATTENDEE_API_KEY=$KEY|" $ENV_FILE
else
  echo "ATTENDEE_API_KEY=$KEY" >> $ENV_FILE
fi
echo "Attendee login (http://localhost:8000): $EMAIL / $PW"
echo "API key written to the repo's .env (restart the bridge if it is already running)"
