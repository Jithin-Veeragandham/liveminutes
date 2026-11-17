// The right-hand rail: Ask (past meetings, sample data for now), People (consent + who's online),
// and for the organizer the whole Edit log and the bot's Activity (+ Bot logs).

import { h, clear, displayName, ago, clock, debounce } from "../util.js";
import { icon } from "../icons.js";
import { avatar, empty, toast, drawer, skeletonLines, iconButton, menu } from "../ui.js";
import { diffRow, opVerb, fieldValue } from "./items.js";

const KIND_LABEL = { summary: "summary item", action: "action item", meeting: "meeting title", minutes: "minutes" };
const withArticle = (w) => (w === "minutes" ? "the minutes" : w === "meeting title" ? "the meeting title" : `${/^[aeiou]/.test(w) ? "an" : "a"} ${w}`);

// ================= Ask =================
export function askView(ctx) {
  const thread = h("div", { class: "thread", attrs: { role: "log", "aria-live": "polite", "aria-label": "Answers" } });
  const input = h("textarea", { class: "textarea", attrs: { rows: "1", placeholder: "Ask about past meetings…", "aria-label": "Your question", maxlength: "300" } });
  const send = h("button", { class: "btn btn--primary btn--icon", type: "submit", attrs: { "aria-label": "Ask" } }, icon("send", { size: 15 }));
  const form = h("form", { class: "ask-form", on: { submit: (e) => { e.preventDefault(); ask(input.value); } } }, input, send);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(input.value); } });
  input.addEventListener("input", () => { input.style.height = "auto"; input.style.height = `${Math.min(120, input.scrollHeight + 2)}px`; });
  const body = h("div", { class: "panel-body" }, thread);
  const panel = h("section", { class: "panel panel--ask", attrs: { "aria-labelledby": "h-ask" }, dataset: { panel: "ask" } },
    h("div", { class: "panel-head" }, icon("sparkles", { size: 16, cls: "head-icon" }), h("h2", { id: "h-ask", text: "Ask past meetings" }), h("span", { class: "spacer" }),
      h("span", { class: "chip chip--warning", text: "Preview · sample data" })),
    body, h("div", { class: "panel-foot" }, form));

  function render() {
    clear(thread);
    const st = ctx.ask;
    if (!st.thread.length) {
      thread.appendChild(h("div", { class: "callout callout--warning" }, icon("info", { size: 15 }),
        h("span", { text: (ctx.snap && ctx.snap.ask.note) || "Preview with sample data." })));
      const sug = (ctx.snap && ctx.snap.ask.suggestions) || [];
      thread.appendChild(h("div", { class: "empty", style: { minHeight: "0", padding: "20px 4px 8px" } },
        h("div", { class: "empty-art empty-art--accent" }, icon("message-square", { size: 22 })),
        h("h3", { text: "Ask about any past meeting" }),
        h("p", { text: "“What did we decide about…?” Answers say which meetings they came from." })));
      thread.appendChild(h("div", { class: "suggestions" }, sug.map((q) => h("button", { class: "suggestion", type: "button", on: { click: () => ask(q) } }, icon("arrow-up-right", { size: 14 }), q))));
    }
    for (const m of st.thread) {
      if (m.q) thread.appendChild(h("div", { class: "bubble bubble--q", text: m.q }));
      else if (m.typing) thread.appendChild(h("div", { class: "bubble bubble--a typing", attrs: { "aria-label": "Thinking" } }, h("span"), h("span"), h("span")));
      else if (m.error) thread.appendChild(h("div", { class: "bubble bubble--a", text: m.error }));
      else thread.appendChild(h("div", { class: "bubble bubble--a" }, h("div", { text: m.answer }),
        m.source_meetings.length ? h("div", { class: "sources", attrs: { "aria-label": "From these meetings" } }, m.source_meetings.map((s) =>
          h("div", { class: "source-card" }, h("strong", { text: s.title }), h("span", { text: `${new Date(s.date + "T12:00").toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}${s.snippet ? " · “" + s.snippet + "”" : ""}` })))) : null,
        h("div", { class: "fine", style: { marginTop: "8px" }, text: "Sample data · not from your meetings yet" })));
    }
    body.scrollTop = body.scrollHeight;
  }

  async function ask(q) {
    q = (q || "").trim();
    if (!q || ctx.ask.busy) return;
    input.value = "";
    input.style.height = "auto";
    ctx.ask.busy = true;
    ctx.ask.thread.push({ q }, { typing: true });
    render();
    try {
      const [r] = await Promise.all([ctx.api.post("/ask", { question: q }), new Promise((ok) => setTimeout(ok, 650))]);
      ctx.ask.thread.splice(-1, 1, r);
    } catch (err) {
      ctx.ask.thread.splice(-1, 1, { error: err.message });
    }
    ctx.ask.busy = false;
    render();
    input.focus();
  }

  return { id: "ask", label: "Ask", iconName: "sparkles", panel, render, focus: () => input.focus() };
}

// ================= People =================
export function consentText(p) {
  if (p.decision === "yes") return p.vouched_by ? `Said yes (via ${displayName(p.vouched_by)})` : "Said yes";
  if (p.decision === "no") return "Said no";
  if (p.asked === false) return "Not asked yet";
  return p.present ? "Waiting for an answer" : "Left before answering";
}

export function peopleView(ctx) {
  const list = h("ul", { class: "people", attrs: { "aria-label": "People in this meeting" } });
  const meBox = h("div");
  const count = h("span", { class: "count", text: "0" });
  const panel = h("section", { class: "panel panel--people", attrs: { "aria-labelledby": "h-people" }, dataset: { panel: "people" } },
    h("div", { class: "panel-head" }, icon("users", { size: 16, cls: "head-icon" }), h("h2", { id: "h-people", text: "People" }), count),
    h("div", { class: "panel-body" }, meBox, list));

  function render() {
    if (!ctx.snap) return;
    const online = ctx.online();
    const editing = ctx.snap.presence.editing || {};
    clear(list);
    clear(meBox);
    const me = ctx.me();
    if (!ctx.isOrg) {
      meBox.appendChild(h("div", { class: "callout", style: { margin: "12px 12px 4px" } }, icon(me.name ? "user" : "eye", { size: 15 }),
        h("span", { class: "grow" }, me.name ? h("span", {}, "You're ", h("strong", { text: displayName(me.name) }), ". Edits are recorded under this name (name selection, not a login).") : "You're viewing only. Pick your name to edit."),
        h("button", { class: "btn btn--sm", type: "button", text: me.name ? "Switch" : "Pick name", on: { click: () => ctx.emit("switch-name") } })));
    }
    count.textContent = String(ctx.people().length);
    if (!ctx.people().length) list.appendChild(h("li", {}, empty({ iconName: "users", title: "Nobody yet", text: "People appear when they join the call." })));
    for (const p of ctx.people()) {
      const state = p.decision === "yes" ? null : p.decision === "no" ? "no" : "waiting";
      list.appendChild(h("li", { class: "person" },
        avatar(p.name, { size: "lg", state, online: online.has(p.name) }),
        h("div", { class: "grow" },
          h("div", { class: "person-name", text: displayName(p.name) }),
          h("div", { class: "person-sub" }, consentText(p), online.has(p.name) ? " · viewing now" : "", editing[p.name] ? " · editing" : "", !p.present && p.decision === "yes" && ctx.snap.session.status !== "ended" ? " · left" : "")),
        p.decision === "yes" ? h("span", { class: "chip chip--success" }, icon("check", { size: 11 }), "Yes") : p.decision === "no" ? h("span", { class: "chip chip--danger", text: "No" }) : h("span", { class: "chip chip--warning", text: "Waiting" })));
    }
  }
  ctx.on("people", render);
  ctx.on("presence", render);
  ctx.on("me", render);
  return { id: "people", label: "People", iconName: "users", panel, render };
}

// ================= Edit log (organizer) =================
export function editLogView(ctx) {
  let who = "";
  let loaded = false;
  const list = h("ol", { class: "log", attrs: { "aria-label": "Edit log, newest first" } });
  const filter = h("button", { class: "btn btn--sm", type: "button" }, icon("filter", { size: 14 }), h("span", { text: "Everyone" }), icon("chevron-down", { size: 14 }));
  const count = h("span", { class: "count", text: "0" });
  const body = h("div", { class: "panel-body" }, skeletonLines(3), list);
  const panel = h("section", { class: "panel panel--editlog", attrs: { "aria-labelledby": "h-editlog" }, dataset: { panel: "editlog" } },
    h("div", { class: "panel-head" }, icon("history", { size: 16, cls: "head-icon" }), h("h2", { id: "h-editlog", text: "Edit log" }), count, h("span", { class: "spacer" }), filter),
    body);
  let people = [];
  filter.addEventListener("click", () => menu(filter, [
    { label: "Everyone", checked: !who, run: () => { who = ""; load(); } },
    "sep",
    ...people.map((p) => ({ label: displayName(p), checked: who === p, run: () => { who = p; load(); } })),
  ], { label: "Filter by person" }));

  async function load() {
    try {
      const r = await ctx.api.get(`/editlog${who ? "?who=" + encodeURIComponent(who) : ""}`);
      people = r.people;
      loaded = true;
      body.querySelectorAll(':scope > [aria-hidden="true"]').forEach((x) => x.remove());
      filter.querySelector("span").textContent = who ? displayName(who) : "Everyone";
      count.textContent = String(r.changes.length);
      clear(list);
      if (!r.changes.length) list.appendChild(h("li", {}, empty({ iconName: "history", title: who ? "No edits by this person" : "No edits yet",
        text: "Every change to the summary, action items, title and minutes is logged here: who, when, before and after. Nothing is ever removed." })));
      for (const c of r.changes) {
        const item = ctx.item(c.item);
        list.appendChild(h("li", { class: "log-entry" },
          avatar(c.who, { size: "sm" }),
          h("div", { class: "log-head" }, h("strong", { text: displayName(c.who) }), ` ${opVerb(c).replace("this", withArticle(KIND_LABEL[c.kind] || "item"))} `,
            h("span", { class: "when", text: `· ${ago(c.ms, ctx.now())} · v${c.version}` }),
            item ? h("div", {}, h("button", { class: "log-item-link ellipsis", type: "button", style: { maxWidth: "100%" }, text: `“${(item.text || "").slice(0, 70)}${(item.text || "").length > 70 ? "…" : ""}”`, on: { click: () => ctx.emit("goto-item", { id: c.item }) } })) : null),
          h("div", { class: "diff" }, c.fields.flatMap((f) => [f.field && f.field !== "text" ? h("div", { class: "diff-field", text: f.field }) : null, diffRow(f)]))));
      }
    } catch (err) {
      clear(list);
      list.appendChild(h("li", {}, empty({ iconName: "alert-circle", title: "Couldn't load the edit log", text: err.message })));
    }
  }
  const reload = debounce(() => { if (loaded || panel.isConnected) load(); }, 400);
  ctx.on("log", reload);
  return { id: "editlog", label: "Edit log", iconName: "history", panel, render: () => { if (!loaded) load(); }, load };
}

// ================= Activity (organizer) =================
const ACT_ICON = { bot: "bot", chat: "message-square", people: "users", consent: "shield-check", state: "activity", error: "alert", upload: "zap" };

export function activityView(ctx) {
  const list = h("ol", { class: "activity", attrs: { "aria-label": "Bot activity" } });
  const body = h("div", { class: "panel-body" }, list);
  const logsBtn = h("button", { class: "btn btn--sm", type: "button", on: { click: () => botLogs(ctx) } }, icon("terminal", { size: 14 }), "Bot logs");
  const panel = h("section", { class: "panel panel--activity", attrs: { "aria-labelledby": "h-activity" }, dataset: { panel: "activity" } },
    h("div", { class: "panel-head" }, icon("activity", { size: 16, cls: "head-icon" }), h("h2", { id: "h-activity", text: "Activity" }), h("span", { class: "spacer" }), logsBtn),
    body);
  function row(a) {
    return h("li", { class: `k-${a.kind}` }, h("time", { text: a.time || clock(a.ms) }), icon(ACT_ICON[a.kind] || "info", { size: 14 }), h("span", { class: "a-text", text: a.text }));
  }
  function render(change) {
    if (!ctx.snap) return;
    logsBtn.hidden = !!ctx.snap.session.demo;
    if (change && change.added) {
      const near = body.scrollHeight - body.scrollTop - body.clientHeight < 60;
      change.added.forEach((a) => list.appendChild(row(a)));
      if (near) body.scrollTop = body.scrollHeight;
      return;
    }
    clear(list);
    if (!ctx.activity.length) list.appendChild(h("li", { style: { display: "block" } }, empty({ iconName: "activity", title: "No activity yet" })));
    ctx.activity.forEach((a) => list.appendChild(row(a)));
    requestAnimationFrame(() => { body.scrollTop = body.scrollHeight; });
  }
  return { id: "activity", label: "Activity", iconName: "activity", panel, render };
}

function botLogs(ctx) {
  const box = h("div", { class: "log-box", attrs: { "aria-live": "off" } });
  const head = h("p", { class: "muted", style: { fontSize: "13px", marginBottom: "12px" }, text: "Attendee's record of the bot, what the bridge saw, and every Groq upload. Updates every 3 s." });
  const d = drawer({ title: "Bot logs", body: h("div", {}, head, box), iconName: "terminal", onClose: () => clearInterval(timer) });
  const load = async () => {
    try {
      const r = await fetch(`/live/${encodeURIComponent(ctx.cfg.sid)}/logs`, { cache: "no-store" }).then((x) => x.json());
      clear(box);
      if (r.attendee && r.attendee.error) box.appendChild(h("div", { class: "k-error" }, h("span", { text: "" }), h("span", { class: "src", text: "attendee" }), h("span", { text: r.attendee.error })));
      for (const e of r.log) box.appendChild(h("div", { class: e.kind === "error" ? "k-error" : "" }, h("span", { text: e.ms ? new Date(e.ms).toLocaleTimeString() : "" }), h("span", { class: "src", text: e.source }), h("span", { text: e.text })));
      if (!r.log.length) box.appendChild(h("div", {}, h("span"), h("span"), h("span", { text: "Nothing logged yet." })));
    } catch (err) { toast("Couldn't load the bot logs", { kind: "error" }); }
  };
  load();
  const timer = setInterval(() => { if (!document.contains(d.panel)) clearInterval(timer); else load(); }, 3000);
}

export { fieldValue };
