// Organizer home (/live): meetings, Start demo, Send bot (with its logs), public link status.
// Uses the bridge's existing endpoints for sending bots and their logs (/live/api/send, /live/<id>/logs).

import { h, clear, displayName, ago, dateLong, clock } from "./util.js";
import { icon } from "./icons.js";
import * as theme from "./theme.js";
import { avatar, toast, menu, dialog, empty, tooltip, skeletonLines } from "./ui.js";

const cfg = JSON.parse(document.getElementById("lm-config").textContent);
const root = document.getElementById("root");
const STATUS = {
  recording: ["Live", "chip--danger", "circle-dot"], paused: ["Paused", "chip--warning", "pause"], joining: ["Joining", "", "loader-circle"],
  ended: ["Ended", "", "circle-check"], declined: ["Stopped", "chip--danger", "ban"],
};

async function post(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": cfg.csrf }, body: JSON.stringify(body || {}) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(data.error || `HTTP ${r.status}`), { data });
  return data;
}

function themeButton() {
  const b = h("button", { class: "btn btn--ghost btn--icon btn--sm", type: "button", attrs: { "aria-label": "Theme" } });
  const paint = () => { clear(b).appendChild(icon(theme.choice() === "dark" ? "moon" : theme.choice() === "light" ? "sun" : "monitor", { size: 16 })); b.setAttribute("aria-label", `Theme: ${theme.choice()}. Change.`); };
  b.addEventListener("click", () => {
    const c = theme.choice();
    menu(b, [{ heading: "Theme" },
      { label: "Light", icon: "sun", checked: c === "light", run: () => theme.set("light") },
      { label: "Dark", icon: "moon", checked: c === "dark", run: () => theme.set("dark") },
      { label: "System", icon: "monitor", checked: c === "system", run: () => theme.set("system") }], { label: "Theme" });
  });
  theme.onChange(paint);
  paint();
  return b;
}

function start() {
  const list = h("div", { class: "meetings", attrs: { role: "list", "aria-label": "Meetings" } }, skeletonLines(4));
  const banner = h("div");
  const demoBtn = h("button", { class: "btn btn--lg", type: "button", on: { click: () => startDemo(1) } }, icon("play", { size: 16 }), "Start demo meeting");
  const demoMore = h("button", { class: "btn btn--lg btn--icon", type: "button", attrs: { "aria-label": "Demo speed" }, on: { click: () => menu(demoMore, [
    { heading: "Demo speed" },
    { label: "Real speed (about 5 min)", run: () => startDemo(1) },
    { label: "2× (about 2.5 min)", run: () => startDemo(2) },
    { label: "5× (about 1 min)", run: () => startDemo(5) },
  ], { label: "Demo speed" }) } }, icon("chevron-down", { size: 16 }));
  tooltip(demoMore, "Demo speed");
  const sendBtn = h("button", { class: "btn btn--primary btn--lg", type: "button", on: { click: sendBot } }, icon("bot", { size: 16 }), "Send bot to a meeting");
  const app = h("div", { class: "home" },
    h("header", { class: "header" },
      h("a", { class: "brand", href: "/live" }, h("img", { class: "logo", src: "/static/img/logo.svg", alt: "" }), h("span", { text: "LiveMinutes" })),
      h("span", { class: "chip", text: "Organizer" }),
      h("div", { class: "header-end" },
        h("a", { class: "btn btn--ghost btn--sm hide-phone", href: "/live?classic=1" }, "Classic view"),
        themeButton())),
    h("main", { class: "home-main", id: "main" },
      h("div", { class: "home-hero" },
        h("div", {}, h("h1", { text: "Meetings" }),
          h("p", { text: "Send the bot to a Meet or Teams call. Once everyone agrees in the chat, it posts a link and code so participants can follow along and fix the notes." })),
        h("div", { class: "actions" }, h("div", { class: "row", style: { gap: "4px" } }, demoBtn, demoMore), sendBtn)),
      banner,
      h("div", { class: "section-title" }, icon("list", { size: 15 }), "Recent meetings"),
      list,
      h("p", { class: "fine", style: { marginTop: "18px" } }, "Meetings are folders in 1_ingestion/sessions/ on this computer. Only this computer can open this page; participants use their own link.")));
  clear(root);
  root.removeAttribute("aria-busy");
  root.appendChild(app);

  async function loadConfig() {
    try {
      const c = await fetch("/live/api/ui-config", { cache: "no-store" }).then((r) => r.json());
      clear(banner);
      if (!c.public_base_url) {
        banner.appendChild(h("div", { class: "callout callout--warning", style: { marginBottom: "8px" } }, icon("globe", { size: 16 }),
          h("div", { class: "grow" }, h("strong", { text: "Participants can't open their link yet. " }),
            "Links point to localhost until you set a public address. Start a free tunnel with ", h("code", { text: "bash 1_ingestion/tunnel.sh" }),
            " (or ", h("code", { text: "cloudflared tunnel --url http://localhost:8776" }), " and paste the https address in a meeting's Share dialog).")));
      } else {
        banner.appendChild(h("div", { class: "callout", style: { marginBottom: "8px" } }, icon("globe", { size: 16 }),
          h("div", { class: "grow" }, "Participants open links at ", h("code", { text: c.public_base_url }), ".")));
      }
    } catch { /* the list still loads */ }
  }

  async function load() {
    let rows;
    try { rows = await fetch("/live/api/meetings", { cache: "no-store" }).then((r) => r.json()); }
    catch { return; }
    clear(list);
    if (!rows.length) {
      list.appendChild(empty({ iconName: "inbox", kind: "accent", title: "No meetings yet",
        text: "Try the demo: a scripted 5-minute meeting with consent, a late joiner, a plan change and two action items.",
        action: h("button", { class: "btn btn--primary", type: "button", on: { click: () => startDemo(1) } }, icon("play", { size: 15 }), "Start demo meeting") }));
      return;
    }
    for (const m of rows) {
      const [label, cls, ic] = STATUS[m.status] || [m.status, "", "info"];
      const row = h("a", { class: "meeting-row", href: `/live/${encodeURIComponent(m.id)}`, attrs: { role: "listitem" } },
        h("span", { class: `platform platform--${m.platform}` }, icon(m.platform === "teams" ? "users" : "video", { size: 15 })),
        h("div", { style: { minWidth: "0" } },
          h("div", { class: "m-title" }, h("span", { text: m.title }), m.demo ? h("span", { class: "chip chip--accent", text: "Demo" }) : null,
            m.approved ? h("span", { class: "chip chip--success" }, icon("badge-check", { size: 11 }), "Approved") : null),
          h("div", { class: "m-sub" },
            h("span", { text: m.started_ms ? `${dateLong(m.started_ms)}, ${clock(m.started_ms)}` : `Changed ${ago(m.changed_ms)}` }),
            h("span", { text: m.people.length ? m.people.map(displayName).join(", ") : "No one consented yet" }))),
        h("div", { class: "m-stats" },
          h("span", { attrs: { "aria-label": `${m.lines} transcript lines` } }, icon("message-square", { size: 13 }), String(m.lines)),
          h("span", { attrs: { "aria-label": `${m.edits} edits` } }, icon("pencil", { size: 13 }), String(m.edits)),
          m.people.length ? h("span", { class: "avatars", attrs: { "aria-hidden": "true" } }, m.people.slice(0, 4).map((p) => avatar(p, { size: "sm" }))) : null),
        h("span", { class: `chip ${cls}` }, icon(ic, { size: 11 }), label));
      list.appendChild(row);
    }
  }

  async function startDemo(speed) {
    demoBtn.disabled = true;
    try {
      const r = await post(`/live/api/demo?speed=${speed}`);
      location.href = r.organizer_url;
    } catch (err) {
      demoBtn.disabled = false;
      toast(err.message, { kind: "error" });
    }
  }

  loadConfig();
  load();
  setInterval(load, 4000);
}

function sendBot() {
  const link = h("input", { class: "input", required: true, type: "url", placeholder: "https://meet.google.com/abc-defg-hij or a Teams link", attrs: { "aria-label": "Meeting link", autocomplete: "off" } });
  const name = h("input", { class: "input", value: "LiveMinutes", attrs: { "aria-label": "Bot name", maxlength: "60" } });
  name.value = "LiveMinutes";
  const msg = h("p", { class: "form-msg", attrs: { role: "status", "aria-live": "polite" }, text: "The bot joins, pauses itself and asks everyone for consent in the chat." });
  const logs = h("div", { class: "log-box", hidden: true, attrs: { "aria-label": "Bot logs" } });
  const open = h("a", { class: "btn btn--primary", hidden: true }, icon("arrow-up-right", { size: 15 }), "Open meeting");
  let timer = null;
  const submit = async () => {
    msg.classList.remove("is-error");
    msg.textContent = "Sending the bot…";
    try {
      const r = await fetch("/live/api/send", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ link: link.value, name: name.value }) });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || "Couldn't send the bot.");
      msg.textContent = `Sent “${d.name}”. Admit it from the lobby; everyone then replies yes in the chat.`;
      open.hidden = false;
      open.href = `/live/${encodeURIComponent(d.bot_id)}`;
      logs.hidden = false;
      const poll = async () => {
        try {
          const lr = await fetch(`/live/${encodeURIComponent(d.bot_id)}/logs`, { cache: "no-store" }).then((x) => x.json());
          clear(logs);
          if (lr.attendee && lr.attendee.error) logs.appendChild(h("div", { class: "k-error" }, h("span"), h("span", { class: "src", text: "attendee" }), h("span", { text: lr.attendee.error })));
          for (const e of lr.log) logs.appendChild(h("div", { class: e.kind === "error" ? "k-error" : "" }, h("span", { text: e.ms ? new Date(e.ms).toLocaleTimeString() : "" }), h("span", { class: "src", text: e.source }), h("span", { text: e.text })));
          logs.scrollTop = logs.scrollHeight;
        } catch { /* keep polling */ }
      };
      poll();
      timer = setInterval(poll, 2000);
    } catch (err) {
      msg.textContent = err.message;
      msg.classList.add("is-error");
    }
  };
  const body = h("form", { class: "stack", style: { gap: "14px" }, on: { submit: (e) => { e.preventDefault(); submit(); } } },
    h("div", { class: "field" }, h("label", { text: "Meeting link" }), link),
    h("div", { class: "field" }, h("label", { text: "Bot name" }), name),
    msg, logs,
    h("button", { type: "submit", hidden: true }));
  dialog({ title: "Send the bot to a meeting", iconName: "bot", description: "Google Meet or Microsoft Teams.", body, size: "wide",
    onClose: () => clearInterval(timer),
    actions: [{ label: "Close" }, { label: "Send bot", primary: true, icon: "send", run: () => submit() }] });
  setTimeout(() => {
    const foot = document.querySelector(".dialog-foot");
    if (foot) foot.insertBefore(open, foot.lastChild);
  }, 0);
}

start();
