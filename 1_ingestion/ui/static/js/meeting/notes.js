// The LLM's live notes (notes.py): a status line under the Summary and Action items headings (when the last
// update was, a countdown to the next one, "delayed" while the LLM is slow or failing), and, for the organizer,
// every update with how long it took. Data: snapshot.session.llm (ui/live_notes.py status()).
import { h, clear, ago, clock } from "../util.js";
import { icon } from "../icons.js";

const SLOW_S = 20;      // an update running longer than this shows as delayed
const OVERDUE_S = 15;   // an update this late (e.g. our per-minute cap was reached) shows as delayed

const secs = (ms) => Math.max(0, Math.round(ms / 1000));

/** What to show now: { kind, text, detail, frac, busy } or null (nothing to show). */
export function describe(l, now, isOrg) {
  if (!l) return null;
  if (!l.enabled) return isOrg && l.reason ? { kind: "off", text: "Live summary is off", detail: l.reason } : null;
  const updated = l.updated_ms ? `Updated ${ago(l.updated_ms, now)}` : "";
  if (!l.live) return l.updated_ms ? { kind: "final", text: `Final notes · last updated ${clock(l.updated_ms)}` } : null;
  if (l.waiting) return { kind: "waiting", text: "The live summary starts once people talk" };
  if (l.busy) {
    const s = l.busy_since_ms ? secs(now - l.busy_since_ms) : 0;
    return s >= SLOW_S
      ? { kind: "delayed", text: `Summary update delayed · waiting for the LLM (${s} s)`, detail: updated, busy: true }
      : { kind: "updating", text: "Updating the summary…", detail: updated, busy: true };
  }
  const left = l.next_due_ms ? l.next_due_ms - now : null;
  if (l.error) {
    return { kind: "delayed", text: `Summary update delayed · ${left > 0 ? `trying again in ${secs(left)} s` : "trying again now"}`,
      detail: isOrg ? `Last try failed: ${l.error}` : updated };
  }
  if (left === null) return { kind: "waiting", text: "Waiting for the first update" };
  if (left < -OVERDUE_S * 1000) return { kind: "delayed", text: "Summary update delayed · it runs as soon as the LLM is free", detail: updated };
  const frac = 1 - Math.min(1, Math.max(0, left) / ((l.interval_s || 30) * 1000));
  const next = left > 0 ? `next in ${secs(left)} s` : "next update now";
  return { kind: "scheduled", text: l.updates ? `${updated} · ${next}` : `First summary ${left > 0 ? `in ${secs(left)} s` : "now"}`, frac };
}

const KIND_ICON = { off: "info", final: "circle-check", waiting: "clock", updating: "refresh-cw", delayed: "alert", scheduled: "sparkles" };

/** The status line. compact: one line, no bar and no detail (the Action items panel). */
export function notesStatus(ctx, { compact = false } = {}) {
  const textEl = h("span", { class: "llm-text" });
  const detailEl = h("span", { class: "llm-detail" });
  const fill = h("i");
  const bar = h("span", { class: "llm-bar", attrs: { "aria-hidden": "true" } }, fill);
  const iconBox = h("span", { class: "llm-icon", attrs: { "aria-hidden": "true" } });
  const el = h("div", { class: `llm-status${compact ? " llm-status--compact" : ""}`, hidden: true },
    iconBox, h("span", { class: "llm-lines" }, textEl, compact ? null : detailEl), compact ? null : bar);
  let shownKind = null;
  function render() {
    const l = ctx.snap && ctx.snap.session && ctx.snap.session.llm;
    const d = describe(l, ctx.now(), ctx.isOrg);
    el.hidden = !d;
    if (!d) return;
    if (d.kind !== shownKind) {
      el.className = `llm-status llm-status--${d.kind}${compact ? " llm-status--compact" : ""}`;
      clear(iconBox).appendChild(icon(KIND_ICON[d.kind], { size: 13 }));
      shownKind = d.kind;
    }
    textEl.textContent = d.text;
    el.title = d.detail ? `${d.text}. ${d.detail}` : d.text;
    if (!compact) {
      detailEl.textContent = d.detail || (l && l.model && ctx.isOrg ? `${l.model} · every ${l.interval_s} s` : "");
      detailEl.hidden = !detailEl.textContent;
      bar.hidden = !(d.kind === "scheduled" || d.busy);
      bar.classList.toggle("is-busy", !!d.busy);
      fill.style.width = d.busy ? "" : `${Math.round((d.frac || 0) * 100)}%`;
    }
  }
  setInterval(() => { if (el.isConnected) render(); }, 1000);
  return { el, render };
}

/** Organizer only: every LLM update, newest first, with its latency and tokens, and what was sent. */
export function notesUpdates(ctx) {
  const list = h("ol", { class: "llm-calls", attrs: { "aria-label": "Live notes updates" } });
  const head = h("span", { class: "llm-calls-head" });
  const box = h("details", { class: "llm-box" }, h("summary", {}, icon("sparkles", { size: 14 }), h("span", { text: "Live notes" }), head), list);
  let key = "";
  function render() {
    const l = ctx.snap && ctx.snap.session && ctx.snap.session.llm;
    box.hidden = !(ctx.isOrg && l && l.history);
    if (box.hidden) return;
    const hist = l.history;
    const ok = hist.filter((x) => x.status === "ok" && typeof x.seconds === "number").map((x) => x.seconds).sort((a, b) => a - b);
    const median = ok.length ? ok[Math.floor((ok.length - 1) / 2)] : null;
    head.textContent = [`${l.updates} update${l.updates === 1 ? "" : "s"}`, median !== null ? `median ${median.toFixed(1)} s` : "",
      l.last_seconds !== null && l.last_seconds !== undefined ? `last ${Number(l.last_seconds).toFixed(1)} s` : "",
      l.rate ? `${l.rate.minute}/${l.rate.minute_limit} calls this minute` : ""].filter(Boolean).join(" · ");
    const k = `${hist.length}:${hist.length ? hist[hist.length - 1].ms : ""}`;
    if (k === key) return;
    key = k;
    clear(list);
    if (!hist.length) list.appendChild(h("li", { class: "muted", text: "No update yet." }));
    for (const x of hist.slice().reverse()) {
      const status = x.status === "ok" ? h("span", { class: "chip chip--success", text: `Update ${x.part}` })
        : x.status === "error" ? h("span", { class: "chip chip--danger", text: "Failed" })
        : h("span", { class: "chip", text: "Skipped" });
      const row = h("li", { class: `llm-call llm-call--${x.status}` },
        h("time", { text: x.time || clock(x.ms) }), status,
        x.status === "skipped" ? h("span", { class: "muted", text: x.note || "No new speech" })
          : h("span", { class: "llm-range", text: `${x.from}–${x.to} · ${x.lines} line${x.lines === 1 ? "" : "s"}` }),
        typeof x.seconds === "number" ? h("span", { class: "llm-lat", text: `${x.seconds.toFixed(1)} s` }) : null);
      if (x.status === "ok") row.appendChild(h("span", { class: "llm-tok muted", text: `${(x.input_tokens || 0).toLocaleString()} → ${(x.output_tokens || 0).toLocaleString()} tokens` }));
      if (x.status === "error") row.appendChild(h("span", { class: "llm-err", text: x.error || "" }));
      if (x.sent) row.appendChild(h("details", { class: "llm-sent" }, h("summary", { text: "Transcript sent" }), h("pre", { text: x.sent })));
      list.appendChild(row);
    }
  }
  return { el: box, render };
}
