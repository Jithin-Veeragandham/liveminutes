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

const STREAM_EVENTS = ["snapshot", "session", "people", "lines", "summary", "actions", "presence", "me", "log", "activity", "revoked", "ping"];
const sleep = (ms) => new Promise((ok) => setTimeout(ok, ms));

/**
 * Live updates. Uses the event stream (Server-Sent Events) when it gets through; if no event arrives
 * within `firstEventMs`, or the stream goes quiet for `silentMs` (the server pings every 15 s), it falls
 * back to a long poll (/poll), which works through any proxy, including Cloudflare quick tunnels (they
 * don't pass Server-Sent Events on).
 * onState gets: "connecting" | "live" (stream) | "polling" (long poll, still live) | "reconnecting" | "offline".
 */
export class Live {
  constructor(api, { onEvent, onState, onRevoked, cursor, firstEventMs = 4000, silentMs = 40000 }) {
    Object.assign(this, { api, onEvent, onState: onState || (() => {}), onRevoked: onRevoked || (() => {}),
      cursor: cursor || (() => ({})), firstEventMs, silentMs });
    this.mode = "stream";
    this.closed = false;
    this.tries = 0;
    this.token = "";
    window.addEventListener("online", () => { if (this.mode === "stream") this.reopen(0); });
    window.addEventListener("offline", () => this.setState("offline"));
    this.watchdog = setInterval(() => {
      if (this.mode === "stream" && this.gotEvent && Date.now() - this.lastEvent > this.silentMs) this.toPolling();
    }, 5000);
  }

  setState(s) { if (this.state !== s) { this.state = s; this.onState(s); } }

  start() { this.openStream(); }

  openStream() {
    if (this.closed || this.mode !== "stream") return;
    clearTimeout(this.firstTimer);
    this.setState(this.tries ? "reconnecting" : "connecting");
    this.gotEvent = false;
    const es = new EventSource(this.api.base + "/stream", { withCredentials: true });
    this.es = es;
    this.firstTimer = setTimeout(() => { if (!this.gotEvent) this.toPolling(); }, this.firstEventMs);
    for (const name of STREAM_EVENTS) es.addEventListener(name, (e) => this.streamEvent(name, e));
    es.onerror = () => {
      if (this.closed || this.mode !== "stream") return;
      es.close();
      if (!this.gotEvent) { this.toPolling(); return; } // it never worked here: don't keep retrying it
      this.tries += 1;
      this.setState(navigator.onLine === false ? "offline" : "reconnecting");
      this.reopen(Math.min(15000, 600 * 2 ** Math.min(this.tries, 5)));
    };
  }

  reopen(ms) {
    clearTimeout(this.timer);
    this.timer = setTimeout(() => { if (this.es) this.es.close(); this.openStream(); }, ms);
  }

  streamEvent(name, e) {
    if (this.mode !== "stream" || this.closed) return;
    this.gotEvent = true;
    this.lastEvent = Date.now();
    let data;
    try { data = JSON.parse(e.data); } catch { return; }
    if (name === "ping") return;
    if (name === "snapshot") { this.tries = 0; this.setState("live"); }
    if (name === "revoked") { this.close(); this.onRevoked(data); return; }
    this.onEvent(name, data);
  }

  toPolling() {
    if (this.closed || this.mode === "polling") return;
    this.mode = "polling";
    clearTimeout(this.firstTimer);
    clearTimeout(this.timer);
    if (this.es) this.es.close();
    this.pollLoop();
  }

  async pollLoop() {
    let failures = 0;
    while (!this.closed && this.mode === "polling") {
      try {
        const c = this.cursor();
        const q = new URLSearchParams({ v: this.token });
        if (c.after) q.set("after", c.after);
        if (c.act !== undefined && c.act !== null) q.set("act", String(c.act));
        const r = await this.api.get(`/poll?${q}`);
        if (this.closed) return;
        this.token = r.v;
        failures = 0;
        this.setState("polling");
        for (const [name, data] of r.events) this.onEvent(name, data);
      } catch (err) {
        if (this.closed) return;
        if ([401, 403, 404, 410].includes(err.status)) { this.close(); this.onRevoked(err.data || {}); return; }
        failures += 1;
        this.setState(navigator.onLine === false ? "offline" : "reconnecting");
        await sleep(Math.min(15000, 800 * 2 ** Math.min(failures, 4)));
      }
    }
  }

  close() {
    this.closed = true;
    clearTimeout(this.firstTimer);
    clearTimeout(this.timer);
    clearInterval(this.watchdog);
    if (this.es) this.es.close();
  }
}
