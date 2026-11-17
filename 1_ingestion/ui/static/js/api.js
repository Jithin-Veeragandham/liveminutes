// Talking to the bridge: JSON calls (with the CSRF token on writes) and the live stream (SSE)
// with automatic reconnect and a visible connection state.

export class ApiError extends Error {
  constructor(status, data) {
    super((data && data.error) || `HTTP ${status}`);
    this.status = status;
    this.data = data || {};
    this.state = this.data.state || (status === 0 ? "offline" : "error");
  }
}

export class Api {
  constructor(base, { csrf = "", name = null } = {}) {
    this.base = base;
    this.csrf = csrf;
    this.name = name; // organizer: who edits are recorded as (X-LM-Name)
  }

  headers(extra = {}) {
    const h = { Accept: "application/json", ...extra };
    if (this.csrf) h["X-CSRF-Token"] = this.csrf;
    if (this.name) h["X-LM-Name"] = this.name;
    return h;
  }

  async request(method, path, body) {
    let r;
    try {
      r = await fetch(this.base + path, {
        method,
        credentials: "same-origin",
        cache: "no-store",
        headers: this.headers(body !== undefined ? { "Content-Type": "application/json" } : {}),
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
    } catch {
      throw new ApiError(0, { state: "offline", error: "Can't reach LiveMinutes. Check your connection." });
    }
    const type = r.headers.get("Content-Type") || "";
    const data = type.includes("json") ? await r.json().catch(() => ({})) : await r.text();
    if (!r.ok) throw new ApiError(r.status, typeof data === "object" ? data : { error: String(data).slice(0, 200) });
    return data;
  }

  get(path) { return this.request("GET", path); }
  post(path, body = {}) { return this.request("POST", path, body); }
}

/** A live stream that keeps itself connected. onState gets "connecting" | "live" | "reconnecting" | "offline". */
export class Stream {
  constructor(url, { onEvent, onState, onRevoked }) {
    this.url = url;
    this.onEvent = onEvent;
    this.onState = onState || (() => {});
    this.onRevoked = onRevoked || (() => {});
    this.tries = 0;
    this.closed = false;
    this.es = null;
    this.timer = null;
    this.events = ["snapshot", "session", "people", "lines", "summary", "actions", "presence", "me", "log", "activity", "revoked"];
    window.addEventListener("online", () => this.reconnectSoon(0));
    window.addEventListener("offline", () => this.setState("offline"));
  }

  setState(s) {
    if (this.state !== s) { this.state = s; this.onState(s); }
  }

  open() {
    if (this.closed) return;
    clearTimeout(this.timer);
    this.setState(this.tries ? "reconnecting" : "connecting");
    const es = new EventSource(this.url, { withCredentials: true });
    this.es = es;
    for (const name of this.events) {
      es.addEventListener(name, (e) => {
        let data;
        try { data = JSON.parse(e.data); } catch { return; }
        if (name === "snapshot") { this.tries = 0; this.setState("live"); }
        if (name === "revoked") { this.close(); this.onRevoked(data); return; }
        this.onEvent(name, data);
      });
    }
    es.onerror = () => {
      // The browser retries by itself on a dropped connection, but not after an HTTP error, and it
      // never tells us which. Close it and retry with backoff; the page re-checks access on reconnect.
      if (this.closed) return;
      es.close();
      this.tries += 1;
      this.setState(navigator.onLine === false ? "offline" : "reconnecting");
      this.reconnectSoon(Math.min(15000, 600 * 2 ** Math.min(this.tries, 5)));
    };
  }

  reconnectSoon(ms) {
    if (this.closed) return;
    clearTimeout(this.timer);
    this.timer = setTimeout(() => { if (this.es) this.es.close(); this.open(); }, ms);
  }

  restart() { this.tries = 0; if (this.es) this.es.close(); this.open(); }

  close() {
    this.closed = true;
    clearTimeout(this.timer);
    if (this.es) this.es.close();
  }
}
