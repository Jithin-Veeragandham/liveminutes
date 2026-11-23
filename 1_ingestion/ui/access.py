"""Who may open a meeting's live view: access codes, viewer cookies, CSRF tokens, rate limits,
and telling local (organizer) requests from public ones (through a tunnel).

Access code
  Made once, right after everyone has said yes, and posted in the meeting chat with the link.
  6 characters from ALPHABET (no 0/O, 1/I/L). Only a salted PBKDF2 hash is stored, in
  sessions/<id>/access.json. The plain code is kept in memory only, so the organizer and the
  share sheet can show it while the bridge runs. It stops working VIEW_ACCESS_DAYS after the
  meeting ends, when someone says no (the session folder is wiped), or when the organizer makes
  a new one (generation + 1: old cookies stop working too). Codes are never logged.

Viewer cookie (per meeting, Path=/m/<id>, HttpOnly, SameSite=Lax, Secure over https)
  Signed with HMAC-SHA256. Holds: meeting id, code generation, a random nonce, and the name the
  viewer picked. Picking a name is NOT a login: anyone with the code can pick any name of the
  people who consented. Edits are recorded under that name.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from collections import deque
from urllib.parse import urlsplit

import settings

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # 31 symbols: no 0/O, no 1/I/L
CODE_LEN = 6
ROUNDS = 120_000                                # PBKDF2-SHA256 rounds for the stored hash
COOKIE_PREFIX = "lm_v_"
PROXY_HEADERS = ("Cf-Connecting-Ip", "Cf-Ray", "X-Forwarded-For", "X-Forwarded-Host", "Forwarded", "X-Real-Ip")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "liveminutes-bridge"}

CODES = {}                                      # meeting id -> plain code, memory only (for the share sheet)
ORGANIZER_TOKEN = secrets.token_urlsafe(24)     # CSRF token for the organizer pages, new every bridge start


def now_ms():
    return int(time.time() * 1000)


# ================= Access codes =================

def new_code():
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))


def normalize_code(text):
    """What people type ("k7q-m4x", "K7Q M4X") -> "K7QM4X". Characters outside ALPHABET are dropped."""
    return "".join(c for c in str(text or "").upper() if c in ALPHABET)[:CODE_LEN * 2]


def pretty(code):
    return f"{code[:3]}-{code[3:]}" if len(code) == CODE_LEN else code


def _hash(code, salt_hex, rounds=ROUNDS):
    return hashlib.pbkdf2_hmac("sha256", code.encode(), bytes.fromhex(salt_hex), rounds).hex()


def _path(sid):
    return settings.SESSIONS_DIR / sid / "access.json"


def read_record(sid):
    try:
        return json.loads(_path(sid).read_text())
    except (OSError, ValueError):
        return None


def _write_record(sid, record):
    path = _path(sid)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record))
    os.replace(tmp, path)  # access.json is a small state file (not the edit log), replaced atomically


def create_code(sid):
    """A new code for this meeting (the old one, and cookies made with it, stop working). Returns it."""
    old = read_record(sid) or {}
    code = new_code()
    salt = secrets.token_hex(16)
    _write_record(sid, {"v": 1, "salt": salt, "hash": _hash(code, salt), "rounds": ROUNDS,
                        "generation": int(old.get("generation", 0)) + 1, "created_ms": now_ms(),
                        "ended_ms": old.get("ended_ms")})
    CODES[sid] = code
    return code


def check_code(sid, typed):
    record = read_record(sid)
    code = normalize_code(typed)
    if not record or len(code) != CODE_LEN:
        return False
    return hmac.compare_digest(_hash(code, record["salt"], record.get("rounds", ROUNDS)), record["hash"])


def mark_ended(sid, ms=None):
    record = read_record(sid)
    if record and not record.get("ended_ms"):
        record["ended_ms"] = ms or now_ms()
        _write_record(sid, record)


def expires_ms(sid, live):
    """When the code stops working: VIEW_ACCESS_DAYS after the meeting ended. None while it runs.
    A meeting that is not running and never got an "ended" event (the bridge restarted) counts as
    ended at its last file change."""
    if live:
        return None
    record = read_record(sid) or {}
    ended = record.get("ended_ms")
    if not ended:
        folder = settings.SESSIONS_DIR / sid
        times = [p.stat().st_mtime for p in folder.iterdir()] if folder.is_dir() else []
        ended = int(max(times, default=time.time()) * 1000)
    return int(ended + settings.VIEW_ACCESS_DAYS * 86_400_000)


# ================= Signed cookies and CSRF =================

def secret():
    """The signing key: UI_SECRET from .env, else a random one kept in sessions/.ui_secret (0600)."""
    if settings.UI_SECRET:
        return settings.UI_SECRET.encode()
    path = settings.SESSIONS_DIR / ".ui_secret"
    try:
        return bytes.fromhex(path.read_text().strip())
    except (OSError, ValueError):
        path.parent.mkdir(parents=True, exist_ok=True)
        key = secrets.token_bytes(32)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(key.hex())
        return key


def _sign(data):
    return hmac.new(secret(), data.encode(), hashlib.sha256).hexdigest()


def cookie_name(sid):
    return COOKIE_PREFIX + sid


def make_cookie(sid, generation, name=None, nonce=None):
    payload = {"s": sid, "g": generation, "n": name, "k": nonce or secrets.token_urlsafe(12), "t": now_ms()}
    data = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    return f"{data}.{_sign(data)}", payload


def read_cookie(sid, value):
    """The cookie's payload if the signature is right and it is for this meeting, else None."""
    try:
        data, sig = str(value).rsplit(".", 1)
        if not hmac.compare_digest(sig, _sign(data)):
            return None
        payload = json.loads(base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)))
    except (ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) and payload.get("s") == sid else None


def csrf_for(payload):
    return _sign("csrf:" + str(payload.get("k")))[:32]


# ================= Local (organizer) or public (tunnel) =================

def _hostname(host):
    host = (host or "").strip().lower()
    if host.startswith("["):
        return host[1:].split("]", 1)[0]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def public_base_url():
    """The participants' base URL: set while running (tunnel.sh / organizer view), else PUBLIC_BASE_URL."""
    try:
        url = (settings.SESSIONS_DIR / ".ui_public_url").read_text().strip()
    except OSError:
        url = ""
    return (url or settings.PUBLIC_BASE_URL).rstrip("/")


def set_public_base_url(url):
    url = str(url or "").strip().rstrip("/")
    parts = urlsplit(url)
    if url and (parts.scheme not in ("http", "https") or not parts.hostname or parts.path or parts.query):
        raise ValueError("Use a base URL like https://example.trycloudflare.com (no path).")
    path = settings.SESSIONS_DIR / ".ui_public_url"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(url)
    return url


def is_public(request):
    """True for requests that came through a tunnel or proxy, or by a host name that is not this computer.
    Those may only reach the participant pages (/m/...) and static files."""
    if any(h in request.headers for h in PROXY_HEADERS):
        return True
    return _hostname(request.host) not in LOCAL_HOSTS


def client_ip(request):
    if "Cf-Connecting-Ip" in request.headers:
        return request.headers["Cf-Connecting-Ip"].strip()[:64]
    forwarded = request.headers.get("X-Forwarded-For", "")
    return (forwarded.split(",")[0].strip() or request.remote or "?")[:64]


def is_https(request):
    proto = request.headers.get("X-Forwarded-Proto", "") or ""
    visitor = request.headers.get("Cf-Visitor", "") or ""
    return request.secure or proto.lower() == "https" or '"https"' in visitor or public_base_url().startswith("https://")


def same_origin(request):
    """A write must come from our own page: the Origin (when the browser sends one) must be this host,
    or the public URL. Browsers that say the request is cross-site are refused too."""
    if request.headers.get("Sec-Fetch-Site") == "cross-site":
        return False
    origin = request.headers.get("Origin")
    if origin is None:
        return True
    netloc = urlsplit(origin).netloc.lower()
    allowed = {(request.host or "").lower(), urlsplit(public_base_url()).netloc.lower(),
               (request.headers.get("X-Forwarded-Host") or "").lower()}
    return netloc in allowed - {""}


def is_browser(request):
    return "Origin" in request.headers or "Sec-Fetch-Mode" in request.headers


# ================= Rate limits =================

class Limiter:
    """At most `limit` hits per key in any `window_s` seconds. Memory only."""

    def __init__(self, limit, window_s):
        self.limit, self.window = limit, window_s
        self.hits = {}

    def hit(self, key):
        """Count one hit. Returns 0 if allowed, else the seconds to wait."""
        now = time.monotonic()
        q = self.hits.setdefault(key, deque())
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            return max(1, int(self.window - (now - q[0])) + 1)
        q.append(now)
        if len(self.hits) > 5000:  # forget idle keys now and then
            for k in [k for k, v in self.hits.items() if not v or now - v[-1] > self.window]:
                del self.hits[k]
        return 0

    def reset(self):
        self.hits.clear()


CODE_PER_IP = Limiter(6, 300)          # code tries per address per meeting: 6 per 5 min
CODE_PER_MEETING = Limiter(40, 3600)   # all addresses together: 40 per hour per meeting
EDITS = Limiter(90, 60)                # writes per viewer
ASK = Limiter(20, 60)
PRESENCE = Limiter(180, 60)
ALL_LIMITERS = (CODE_PER_IP, CODE_PER_MEETING, EDITS, ASK, PRESENCE)
