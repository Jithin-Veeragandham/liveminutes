// Small helpers shared by every page. Everything that shows people's text uses textContent
// (through h()), never innerHTML, so transcript lines and edits can't inject markup.

/** Create an element. props: class, text, attrs (object), on (events), dataset, style. Children: nodes, strings. */
export function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k === "on") for (const [ev, fn] of Object.entries(v)) el.addEventListener(ev, fn);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k === "style") Object.assign(el.style, v);
    else if (k === "attrs") for (const [a, val] of Object.entries(v)) { if (val !== undefined && val !== null && val !== false) el.setAttribute(a, val === true ? "" : val); }
    else if (k in el && typeof v !== "string") el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  append(el, children);
  return el;
}

export function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

// ---------- people ----------
/** "Veeragandham, Jithin" -> "JV", "Maya Chen" -> "MC", "Conference Room 4B" -> "C4". */
export function initials(name) {
  let n = String(name || "?").trim();
  if (n.includes(",")) n = n.split(",").reverse().join(" ");
  const parts = n.split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  const first = parts[0][0];
  const last = parts.length > 1 ? parts[parts.length - 1][0] : (parts[0][1] || "");
  return (first + last).toUpperCase();
}

const palette = new Map(); // name -> color slot, set from the meeting's people (consented first, in joining order)

/** Give the meeting's people distinct colors, in the same order for every viewer. */
export function setPeopleOrder(names) {
  palette.clear();
  [...new Set(names.filter(Boolean))].forEach((n, i) => palette.set(n, String(i % 8)));
}

/** The same color slot (0-7) for a name on every screen and for every viewer. */
export function colorOf(name) {
  if (palette.has(name)) return palette.get(name);
  const s = String(name || "");
  let hash = 2166136261;
  for (let i = 0; i < s.length; i++) { hash ^= s.charCodeAt(i); hash = Math.imul(hash, 16777619); }
  return String(Math.abs(hash) % 8);
}

/** Display name: "Veeragandham, Jithin" -> "Jithin Veeragandham". */
export function displayName(name) {
  const n = String(name || "");
  if (/^[^,]+,\s*[^,]+$/.test(n) && !/\d/.test(n)) return n.split(",").map((x) => x.trim()).reverse().join(" ");
  return n;
}

export function firstName(name) {
  return displayName(name).split(/\s+/)[0] || name;
}

// ---------- time ----------
export function mmss(seconds) {
  const s = Math.max(0, Math.floor(seconds || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${String(m).padStart(2, "0")}:${ss}`;
}

const rtf = typeof Intl !== "undefined" && Intl.RelativeTimeFormat ? new Intl.RelativeTimeFormat(undefined, { numeric: "auto", style: "short" }) : null;

/** "just now", "2 min ago", "yesterday". `now` lets the page use the server's clock. */
export function ago(ms, now = Date.now()) {
  if (!ms) return "";
  const diff = Math.round((ms - now) / 1000);
  const abs = Math.abs(diff);
  if (abs < 10) return "just now";
  if (!rtf) return new Date(ms).toLocaleString();
  if (abs < 60) return rtf.format(diff, "second");
  if (abs < 3600) return rtf.format(Math.round(diff / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(diff / 3600), "hour");
  return rtf.format(Math.round(diff / 86400), "day");
}

export function clock(ms) {
  return ms ? new Date(ms).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }) : "";
}

export function dateLong(ms) {
  return ms ? new Date(ms).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric", year: "numeric" }) : "";
}

export function dueLabel(iso) {
  if (!iso) return { text: "", overdue: false };
  const [y, m, d] = iso.split("-").map(Number);
  const due = new Date(y, m - 1, d);
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const days = Math.round((due - today) / 86400000);
  let text = due.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
  if (days === 0) text = "Today";
  else if (days === 1) text = "Tomorrow";
  return { text, overdue: days < 0, days };
}

// ---------- misc ----------
export function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

export function clamp(n, lo, hi) { return Math.max(lo, Math.min(hi, n)); }

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = h("textarea", { class: "sr-only", attrs: { "aria-hidden": "true" } });
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch { ok = false; }
    ta.remove();
    return ok;
  }
}

export function download(filename, text, type = "text/markdown") {
  const url = URL.createObjectURL(new Blob([text], { type: `${type};charset=utf-8` }));
  const a = h("a", { href: url, download: filename });
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

export function isTyping(target) {
  const t = target || document.activeElement;
  if (!t) return false;
  const tag = t.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || t.isContentEditable;
}

export const isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
export const modKey = isMac ? "⌘" : "Ctrl";

/** Word-level diff: [["same"|"del"|"ins", text]]. Small LCS, fine for summary-sized texts. */
export function wordDiff(a, b) {
  const x = String(a ?? "").split(/(\s+)/);
  const y = String(b ?? "").split(/(\s+)/);
  if (x.length * y.length > 250000) return [["del", String(a ?? "")], ["ins", String(b ?? "")]];
  const dp = Array.from({ length: x.length + 1 }, () => new Uint16Array(y.length + 1));
  for (let i = x.length - 1; i >= 0; i--) for (let j = y.length - 1; j >= 0; j--)
    dp[i][j] = x[i] === y[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out = [];
  const push = (kind, t) => { if (!t) return; const last = out[out.length - 1]; if (last && last[0] === kind) last[1] += t; else out.push([kind, t]); };
  let i = 0, j = 0;
  while (i < x.length && j < y.length) {
    if (x[i] === y[j]) { push("same", x[i]); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { push("del", x[i]); i++; }
    else { push("ins", y[j]); j++; }
  }
  while (i < x.length) push("del", x[i++]);
  while (j < y.length) push("ins", y[j++]);
  return out;
}
