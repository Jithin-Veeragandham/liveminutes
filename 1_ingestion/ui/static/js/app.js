// The meeting page, for participants (/m/<id>) and the organizer (/live/<id>). One app, two roles.

import { h, clear, $, displayName, firstName, mmss, isTyping, modKey, ago } from "./util.js";
import { icon } from "./icons.js";
import * as theme from "./theme.js";
import { ApiError, Live } from "./api.js";
import { avatar, toast, tooltip, menu, dialog, palette, kbd, iconButton, dialogOpen, closePopover } from "./ui.js";
import { codeScreen, nameScreen, errorScreen } from "./join.js";
import { createCtx, applyEvent } from "./meeting/ctx.js";
import { transcriptView } from "./meeting/transcript.js";
import { itemsView } from "./meeting/items.js";
import { askView, peopleView, editLogView, activityView, consentText } from "./meeting/rail.js";
import { shareDialog, copyLink, copyCode, exportMarkdown, printMinutes } from "./meeting/share.js";

const cfg = JSON.parse(document.getElementById("lm-config").textContent);
const root = document.getElementById("root");

// ================= participant entry =================
async function boot() {
  const ctx = createCtx(cfg);
  if (ctx.isOrg) return start(ctx);
  let me;
  try { me = await ctx.api.get("/me"); } catch (err) { return errorScreen(err.state, err.message, { retry: () => location.reload() }); }
  route(ctx, me);
}

function route(ctx, me) {
  if (me.csrf) ctx.api.csrf = me.csrf;
  if (me.state === "need_code") {
    const prefill = new URLSearchParams(location.hash.slice(1)).get("code") || "";
    if (prefill) history.replaceState(null, "", location.pathname + location.search); // don't keep the code in the address bar
    return codeScreen({
      prefill,
      onSubmit: async (code) => {
        try {
          const r = await ctx.api.post("/code", { code });
          ctx.api.csrf = r.csrf;
          route(ctx, { state: "need_name", people: r.people, csrf: r.csrf });
        } catch (err) {
          if (["expired", "declined", "not_found", "not_shared"].includes(err.state)) { errorScreen(err.state, err.message); return; }
          throw err;
        }
      },
    });
  }
  if (me.state === "need_name") {
    return nameScreen({
      people: me.people || [],
      onPick: async (name) => {
        if (!name) { start(ctx); return; }
        try {
          const r = await ctx.api.post("/name", { name });
          ctx.api.csrf = r.csrf;
          start(ctx);
        } catch (err) { toast(err.message, { kind: "error" }); route(ctx, { ...me }); }
      },
    });
  }
  if (me.state === "ok") return start(ctx);
  return errorScreen(me.state, me.error, { retry: me.state === "not_shared" ? () => location.reload() : null });
}

// ================= the app =================
function start(ctx) {
  closePopover();
  const isOrg = ctx.isOrg;
  const T = transcriptView(ctx);
  const I = itemsView(ctx);
  const A = askView(ctx);
  const P = peopleView(ctx);
  const L = isOrg ? editLogView(ctx) : null;
  const V = isOrg ? activityView(ctx) : null;
  const views = { transcript: T, summary: I.summary, actions: I.actions, ask: A, people: P };
  if (L) views.editlog = L;
  if (V) views.activity = V;

  // ---------- header ----------
  const platformEl = h("span", { class: "platform", attrs: { role: "img", "aria-label": "Google Meet" } }, icon("video", { size: 15 }));
  const titleEl = h("button", { class: "meeting-title", type: "button" }, "Loading…");
  const demoChip = h("span", { class: "chip chip--accent hide-phone", hidden: true }, icon("play", { size: 11 }), "Demo");
  const subEl = h("span", { class: "meeting-sub hide-tablet" });
  const phoneSub = h("span", { class: "meeting-sub phone-only" });
  const livePill = h("span", { class: "live-pill", hidden: true, attrs: { role: "status" } }, h("span", { class: "pulse", attrs: { "aria-hidden": "true" } }), "Live");
  const stateChip = h("span", { class: "chip state-chip", hidden: true });
  const elapsed = h("span", { class: "elapsed tnum", attrs: { "aria-label": "Elapsed time" } });
  const conn = h("span", { class: "conn row", hidden: true, attrs: { role: "status" } });
  const avatars = h("div", { class: "avatars hide-narrow", attrs: { role: "group", "aria-label": "People in the meeting" } });
  const shareBtn = h("button", { class: "btn btn--sm", type: "button", attrs: { "aria-label": "Share" }, on: { click: () => ctx.snap && shareDialog(ctx) } }, icon("share-2", { size: 14 }), h("span", { class: "hide-phone", text: "Share" }));
  const cmdBtn = h("button", { class: "btn btn--sm hide-narrow", type: "button", on: { click: () => openPalette() } },
    icon("search", { size: 14 }), h("span", { class: "sr-only", text: "Commands " }), h("span", { class: "kbd", text: `${modKey}K` }));
  tooltip(cmdBtn, `Commands (${modKey}K)`);
  const themeBtn = h("button", { class: "btn btn--ghost btn--icon btn--sm hide-phone", type: "button", attrs: { "aria-label": "Theme" }, on: { click: (e) => themeMenu(e.currentTarget) } });
  const meBtn = h("button", { class: "me-btn", type: "button", attrs: { "aria-haspopup": "menu" }, on: { click: (e) => meMenu(e.currentTarget) } });
  const header = h("header", { class: "header" },
    h(isOrg ? "a" : "span", { class: "brand hide-phone", href: isOrg ? "/live" : null, attrs: { "aria-label": isOrg ? "LiveMinutes home" : "LiveMinutes" } },
      h("img", { class: "logo", src: "/static/img/logo.svg", alt: "" }), h("span", { class: "hide-tablet", text: "LiveMinutes" })),
    h("span", { class: "header-sep hide-phone" }),
    h("div", { class: "title-block" }, platformEl, h("div", { class: "title-stack" }, h("div", { class: "row", style: { gap: "6px", minWidth: "0" } }, titleEl, demoChip), subEl, phoneSub)),
    h("div", { class: "header-status hide-phone" }, livePill, stateChip, elapsed),
    conn,
    h("div", { class: "header-end" }, avatars, shareBtn, cmdBtn, themeBtn, meBtn));

  const banners = h("div");
  const colA = h("div", { class: "col col--transcript" });
  const colB = h("div", { class: "col col--mid" });
  const railTabs = h("div", { class: "tabs rail-tabs", attrs: { role: "tablist", "aria-label": "Panels" } });
  const colC = h("div", { class: "col rail" });
  const main = h("main", { class: "main", id: "main", attrs: { tabindex: "-1" } }, colA, colB, colC);
  const tabbar = h("nav", { class: "tabbar", attrs: { "aria-label": "Sections" } });
  const app = h("div", { class: "app" }, header, banners, main, tabbar);
  clear(root);
  root.removeAttribute("aria-busy");
  root.appendChild(app);

  // ---------- layout ----------
  let mode = null;
  let railTab = "ask";
  let phoneTab = "transcript";
  const desktopRail = ["ask", "people", ...(isOrg ? ["editlog", "activity"] : [])];
  const tabletRail = ["summary", "actions", ...desktopRail];
  const phoneTabs = ["transcript", "summary", "actions", "ask", "more"];
  const moreViews = ["people", ...(isOrg ? ["editlog", "activity"] : [])];

  function currentMode() { return innerWidth >= 1200 ? "desktop" : innerWidth >= 768 ? "tablet" : "phone"; }

  function layout(force = false) {
    const m = currentMode();
    if (m === mode && !force) return;
    if (mode === null) railTab = m === "desktop" ? "ask" : "summary"; // two columns: open on the summary
    mode = m;
    colA.append(T.panel);
    if (mode === "desktop") {
      colB.append(I.minutesBar, I.summary.panel, I.actions.panel);
      if (!desktopRail.includes(railTab)) railTab = "ask";
    } else {
      if (mode === "tablet" && !tabletRail.includes(railTab)) railTab = "summary";
    }
    showRail();
    renderTabbar();
  }

  function railList() {
    if (mode === "desktop") return desktopRail;
    if (mode === "tablet") return tabletRail;
    if (phoneTab === "transcript") return []; // the transcript has its own column
    return phoneTab === "more" ? moreViews : [phoneTab];
  }

  function showRail() {
    const list = railList();
    colA.classList.toggle("is-phone-active", mode !== "phone" || phoneTab === "transcript");
    colC.classList.toggle("is-phone-active", mode === "phone" && phoneTab !== "transcript");
    if (!list.length) return;
    if (!list.includes(railTab)) railTab = list[0];
    clear(railTabs);
    for (const id of list) {
      const v = views[id];
      const b = h("button", {
        class: "tab", type: "button", id: `tab-${id}`,
        attrs: { role: "tab", "aria-selected": String(id === railTab), "aria-controls": `rail-${id}`, tabindex: id === railTab ? "0" : "-1" },
        on: { click: () => { railTab = id; showRail(); }, keydown: tabKeys },
      }, icon(v.iconName, { size: 14 }), v.label);
      railTabs.appendChild(b);
    }
    clear(colC);
    const showTabs = (mode !== "phone" || phoneTab === "more") && list.length > 1;
    if (showTabs) colC.appendChild(railTabs);
    if (mode !== "desktop" && (railTab === "summary" || railTab === "actions")) colC.appendChild(I.minutesBar);
    const v = views[railTab];
    v.panel.id = `rail-${railTab}`;
    v.panel.setAttribute("role", showTabs ? "tabpanel" : "region");
    colC.appendChild(v.panel);
    v.render && v.render();
  }

  function tabKeys(e) {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    const list = railList();
    const i = list.indexOf(railTab);
    railTab = list[(i + (e.key === "ArrowRight" ? 1 : list.length - 1)) % list.length];
    showRail();
    $(`#tab-${railTab}`)?.focus();
  }

  function renderTabbar() {
    clear(tabbar);
    if (mode !== "phone") return;
    const labels = { transcript: ["Transcript", "message-square"], summary: ["Summary", "sparkles"], actions: ["Actions", "list-checks"], ask: ["Ask", "message-square"], more: ["More", "users"] };
    labels.ask[1] = "sparkles";
    for (const id of phoneTabs) {
      const [label, ic] = labels[id];
      const pending = id === "actions" && ctx.snap ? ctx.snap.actions.items.filter((a) => a.status === "pending").length : 0;
      tabbar.appendChild(h("button", {
        type: "button", attrs: { "aria-current": phoneTab === id ? "page" : null },
        on: { click: () => { phoneTab = id; if (id !== "transcript" && id !== "more") railTab = id; if (id === "more") railTab = moreViews[0]; showRail(); renderTabbar(); } },
      }, icon(ic, { size: 20 }), h("span", { text: label }), pending ? h("span", { class: "badge", attrs: { "aria-label": `${pending} pending` }, text: String(pending) }) : null));
    }
  }

  function showPanel(id) {
    if (id === "transcript") { if (mode === "phone") { phoneTab = "transcript"; showRail(); renderTabbar(); } return; }
    if (mode === "desktop" && (id === "summary" || id === "actions")) return;
    if (mode === "phone") { phoneTab = moreViews.includes(id) ? "more" : id; renderTabbar(); }
    railTab = id;
    showRail();
  }
  ctx.on("show-panel", showPanel);
  ctx.on("goto-line", ({ id }) => { showPanel("transcript"); setTimeout(() => T.selectLine(id, { scroll: true, flash: true }), 30); });
  addEventListener("resize", () => layout());
  layout(true);

  // ---------- header rendering ----------
  function renderHeader() {
    const s = ctx.snap.session;
    document.title = `${s.title} · LiveMinutes`;
    titleEl.textContent = s.title;
    titleEl.disabled = !ctx.canEdit();
    titleEl.title = ctx.canEdit() ? "Rename" : "";
    titleEl.setAttribute("aria-label", ctx.canEdit() ? `${s.title} (rename)` : s.title);
    platformEl.className = `platform platform--${s.platform}`;
    platformEl.setAttribute("aria-label", s.platform === "teams" ? "Microsoft Teams" : "Google Meet");
    clear(platformEl).appendChild(icon(s.platform === "teams" ? "users" : "video", { size: 15 }));
    demoChip.hidden = !s.demo;
    subEl.textContent = s.started_ms ? new Date(s.started_ms).toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "";
    const live = s.status === "recording" && (ctx.conn === "live" || ctx.conn === "polling");
    livePill.hidden = !live;
    stateChip.hidden = live;
    const st = {
      joining: ["Joining", "", "loader-circle"], paused: [s.waiting.length ? `Paused · waiting for ${s.waiting.map(firstName).join(", ")}` : "Paused", "chip--warning", "pause"],
      ended: ["Ended", "", "circle-check"], declined: ["Stopped · notes deleted", "chip--danger", "ban"], recording: ["Recording", "chip--danger", "circle-dot"],
    }[s.status] || [s.status, "", "info"];
    stateChip.className = `chip state-chip ${st[1]}`;
    clear(stateChip).append(icon(st[2], { size: 12, cls: s.status === "joining" ? "spin" : "" }), h("span", { class: "ellipsis", style: { maxWidth: "34ch" }, text: st[0] }));
    tick();
    renderAvatars();
    renderMe();
    renderBanners();
  }

  function renderAvatars() {
    clear(avatars);
    const online = ctx.online();
    const people = [...ctx.people()].sort((a, b) => (b.present - a.present));
    const shown = people.slice(0, 5);
    for (const p of shown) {
      const state = p.decision === "yes" ? null : p.decision === "no" ? "no" : "waiting";
      const b = h("button", { class: "avatar-btn", type: "button", on: { click: () => showPanel("people") } },
        avatar(p.name, { state, online: online.has(p.name) }), h("span", { class: "sr-only", text: `${displayName(p.name)}: ${consentText(p)}` }));
      tooltip(b, () => [h("strong", { text: displayName(p.name) }), h("div", { class: "tooltip-sub", text: `${consentText(p)}${online.has(p.name) ? " · viewing now" : ""}${p.present ? "" : " · not in the call"}` })]);
      avatars.appendChild(b);
    }
    if (people.length > shown.length) avatars.appendChild(h("span", { class: "avatar avatars-more", text: `+${people.length - shown.length}` }));
  }

  function renderMe() {
    clear(meBtn);
    const me = ctx.me();
    // names come from the content (sr-only words around the visible ones), so they always match what's shown
    if (isOrg) {
      meBtn.append(avatar(me.name === "Organizer" ? "Organizer" : me.name, { size: "sm" }), h("span", { class: "sr-only", text: `Editing as ${displayName(me.name)}` }),
        h("span", { class: "me-name hide-phone", attrs: { "aria-hidden": "true" }, text: me.name === "Organizer" ? "Organizer" : firstName(me.name) }), icon("chevron-down", { size: 13 }));
    } else if (me.name) {
      meBtn.append(avatar(me.name, { size: "sm" }), h("span", { class: "sr-only", text: `You are ${displayName(me.name)}, menu` }),
        h("span", { class: "me-name hide-phone", attrs: { "aria-hidden": "true" }, text: firstName(me.name) }), icon("chevron-down", { size: 13 }));
    } else {
      meBtn.append(icon("eye", { size: 14 }), h("span", { class: "me-name", text: "View only" }), h("span", { class: "sr-only", text: ", pick your name" }));
    }
    const choice = theme.choice();
    clear(themeBtn).appendChild(icon(choice === "dark" ? "moon" : choice === "light" ? "sun" : "monitor", { size: 16 }));
    themeBtn.setAttribute("aria-label", `Theme: ${choice}. Change.`);
  }

  function renderBanners() {
    clear(banners);
    const s = ctx.snap.session;
    const add = (kind, ic, text, action) => banners.appendChild(h("div", { class: `banner banner--${kind}`, attrs: { role: kind === "danger" ? "alert" : "status" } },
      icon(ic, { size: 15 }), h("span", { class: "grow" }, text), action || null));
    if (ctx.conn === "reconnecting" || ctx.conn === "offline")
      add("warning", "wifi-off", ctx.conn === "offline" ? "You're offline. The page catches up as soon as you're back." : "Connection lost. Reconnecting…");
    if (s.status === "declined") add("danger", "ban", "Recording was stopped because someone said no. The bridge deleted the transcript and the notes.");
    else if (s.status === "ended" && s.expires_ms) add("info", "circle-check", `This meeting has ended. The notes stay open for editing until ${new Date(s.expires_ms).toLocaleDateString(undefined, { month: "short", day: "numeric" })}.`);
    if (!isOrg && !ctx.me().name && s.status !== "declined")
      add("info", "eye", "You're viewing only. Pick your name to edit the summary and action items.", h("button", { class: "btn btn--sm", type: "button", text: "Pick my name", on: { click: switchName } }));
    if (isOrg && !s.demo && s.status !== "declined") {
      const live = s.status !== "ended", share = s.share || {}, tunnel = "bash 1_ingestion/tunnel.sh";
      const button = (text) => h("button", { class: "btn btn--sm", type: "button", text, on: { click: () => shareDialog(ctx) } });
      if (live && share.chat === "failed")
        add("warning", "globe", "The participants' link and code couldn't be posted in the chat (Attendee refused the message). Post them again: Share, then Make a new code.", button("Share"));
      else if (live && share.chat === "no_public_url")
        add("warning", "globe", share.public
          ? "The participants' link and code weren't posted in the chat: there was no public address when recording started. The tunnel is on now. Post them: Share, then Make a new code."
          : `The participants' link and code weren't posted in the chat: there's no public address. Start the tunnel (${tunnel}), then Share, then Make a new code.`,
          button("Share"));
      else if (live && !share.public && share.chat !== "posted")
        add("warning", "globe", `No public address: when everyone agrees, the participants' link won't be posted in the chat. Start the tunnel first: ${tunnel}`,
          button("Set up sharing"));
      else if (s.shared && !share.public)
        add("warning", "globe", "Participants can't open the live notes now: the link points to localhost.", button("Set up sharing"));
    }
  }

  let ticker = null;
  function tick() {
    const s = ctx.snap && ctx.snap.session;
    if (!s || !s.started_ms) { elapsed.textContent = ""; return; }
    const end = s.status === "ended" || s.status === "declined" ? (s.ended_ms || s.started_ms) : ctx.now();
    elapsed.textContent = mmss((end - s.started_ms) / 1000);
    const word = { recording: ctx.conn === "polling" ? "● Live · polling" : "● Live", paused: "Paused", joining: "Joining", ended: "Ended", declined: "Stopped" }[s.status] || "";
    phoneSub.textContent = `${s.demo ? "Demo · " : ""}${word} · ${elapsed.textContent}`;
    phoneSub.dataset.status = s.status;
  }
  ticker = setInterval(tick, 1000);

  // ---------- title editing ----------
  titleEl.addEventListener("click", () => {
    if (!ctx.canEdit()) return;
    const s = ctx.snap.session;
    const input = h("input", { class: "meeting-title", value: s.title, attrs: { "aria-label": "Meeting title", maxlength: "120" } });
    input.value = s.title;
    titleEl.replaceWith(input);
    input.focus();
    input.select();
    let done = false;
    const finish = async (saveIt) => {
      if (done) return;
      done = true;
      input.replaceWith(titleEl);
      const v = input.value.trim();
      if (!saveIt || !v || v === s.title) return;
      titleEl.textContent = v;
      try {
        const r = await ctx.api.post("/edit", { item: "meeting", base_version: s.title_version, changes: { title: v } });
        ctx.snap.session.title = r.item.title;
        ctx.snap.session.title_version = r.item.version;
        renderHeader();
        toast("Renamed", { kind: "success" });
      } catch (err) {
        titleEl.textContent = s.title;
        toast(err.status === 409 ? "Someone renamed it at the same time. Try again." : err.message, { kind: "error" });
      }
    };
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") finish(true); if (e.key === "Escape") finish(false); });
    input.addEventListener("blur", () => finish(true));
  });

  // ---------- menus ----------
  function themeMenu(anchor) {
    const c = theme.choice();
    menu(anchor, [
      { heading: "Theme" },
      { label: "Light", icon: "sun", checked: c === "light", run: () => theme.set("light") },
      { label: "Dark", icon: "moon", checked: c === "dark", run: () => theme.set("dark") },
      { label: "System", icon: "monitor", checked: c === "system", run: () => theme.set("system") },
    ], { label: "Theme" });
  }
  theme.onChange(() => renderMe());

  function meMenu(anchor) {
    const me = ctx.me();
    if (isOrg) {
      menu(anchor, [
        { heading: "Record my edits as" },
        { label: "Organizer", checked: me.name === "Organizer", run: () => ctx.setOrgName("Organizer") },
        ...ctx.consented().map((n) => ({ label: displayName(n), checked: me.name === n, run: () => ctx.setOrgName(n) })),
        "sep",
        { label: theme.current() === "dark" ? "Light theme" : "Dark theme", icon: theme.current() === "dark" ? "sun" : "moon", run: () => theme.toggle() },
      ], { label: "Organizer" });
      return;
    }
    const c = theme.choice();
    menu(anchor, [
      me.name ? { heading: `You're ${displayName(me.name)}` } : { heading: "View only" },
      { label: me.name ? "Switch name" : "Pick my name", icon: "user", run: switchName },
      { label: "Keyboard shortcuts", icon: "keyboard", hint: "?", run: shortcuts },
      "sep",
      { heading: "Theme" },
      { label: "Light", icon: "sun", checked: c === "light", run: () => theme.set("light") },
      { label: "Dark", icon: "moon", checked: c === "dark", run: () => theme.set("dark") },
      { label: "System", icon: "monitor", checked: c === "system", run: () => theme.set("system") },
    ], { label: "You" });
  }

  async function switchName() {
    live.close();
    clearInterval(ticker);
    try {
      const r = await ctx.api.post("/forget-name", {});
      ctx.api.csrf = r.csrf;
      route(ctx, { state: "need_name", people: r.people, csrf: r.csrf });
    } catch (err) {
      if (err.state === "need_code") route(ctx, { state: "need_code" }); else errorScreen(err.state, err.message);
    }
  }
  ctx.on("switch-name", switchName);
  ctx.on("need-name", () => {
    if (isOrg) return;
    dialog({ title: "Pick your name to edit", iconName: "user", size: "sm",
      description: "Edits are recorded under your name, so everyone can see who changed what.",
      actions: [{ label: "Not now" }, { label: "Pick my name", primary: true, autofocus: true, run: (close) => { close(); switchName(); } }] });
  });
  ctx.on("export", () => exportMarkdown(ctx));
  ctx.on("print", () => printMinutes(ctx));
  ctx.refresh = async () => { const snap = await ctx.api.get("/snapshot"); handle("snapshot", snap); };

  // ---------- palette + shortcuts ----------
  function openPalette() {
    if (!ctx.snap) return;
    const go = (id, label, ic) => ({ group: "Go to", label, icon: ic, keywords: "panel jump", run: () => { showPanel(id); setTimeout(() => (views[id].panel.querySelector("input, textarea, [tabindex='0'], button") || views[id].panel).focus(), 50); } });
    const cmds = [
      go("transcript", "Transcript", "message-square"), go("summary", "Summary", "sparkles"), go("actions", "Action items", "list-checks"),
      go("ask", "Ask past meetings", "sparkles"), go("people", "People", "users"),
      ...(isOrg ? [go("editlog", "Edit log", "history"), go("activity", "Activity", "activity")] : []),
      { group: "Go to", label: "Jump to live", icon: "arrow-down", run: () => { showPanel("transcript"); T.jumpLive(); } },
      { group: "Actions", label: "Search the transcript", icon: "search", hint: "/", run: () => { showPanel("transcript"); T.focusSearch(); } },
      ...(ctx.canEdit() ? [
        { group: "Actions", label: "Add an action item", icon: "plus", keywords: "new task todo", run: () => { showPanel("actions"); I.addNew(); } },
        { group: "Actions", label: "Approve minutes", icon: "badge-check", run: () => I.approveMinutes() },
      ] : []),
      { group: "Actions", label: "Export minutes (Markdown)", icon: "download", keywords: "download md", run: () => exportMarkdown(ctx) },
      { group: "Actions", label: "Print or save as PDF", icon: "printer", keywords: "pdf print minutes", run: () => printMinutes(ctx) },
      { group: "Share", label: "Copy link", icon: "link", run: () => copyLink(ctx) },
      { group: "Share", label: "Copy access code", icon: "copy", run: () => copyCode(ctx) },
      { group: "Share", label: "Show QR code and sharing", icon: "qr-code", run: () => shareDialog(ctx) },
      { group: "Appearance", label: "Toggle light / dark", icon: theme.current() === "dark" ? "sun" : "moon", keywords: "theme dark mode", run: () => theme.toggle() },
      { group: "Appearance", label: "Use system theme", icon: "monitor", keywords: "theme auto", run: () => theme.set("system") },
      { group: "Help", label: "Keyboard shortcuts", icon: "keyboard", hint: "?", run: shortcuts },
      ...(!isOrg ? [{ group: "Help", label: ctx.me().name ? "Switch name" : "Pick my name", icon: "user", run: switchName }] : []),
      ...(isOrg ? [{ group: "Help", label: "Back to all meetings", icon: "arrow-left", run: () => { location.href = "/live"; } }] : []),
    ];
    palette(cmds);
  }

  function shortcuts() {
    const rows = [["Search the transcript", ["/"]], ["Move down / up in a list", ["j", "k"]], ["Edit the selected item", ["e"]],
      ["Save an edit", [modKey, "↵"]], ["Cancel, close", ["esc"]], ["Command palette", [modKey, "K"]], ["Show shortcuts", ["?"]]];
    dialog({ title: "Keyboard shortcuts", iconName: "keyboard", size: "sm",
      body: h("div", { class: "shortcuts" }, rows.flatMap(([label, keys]) => [h("span", { text: label }), h("span", { class: "keys" }, kbd(...keys))])),
      actions: [{ label: "Close", primary: true }] });
  }

  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); if (!dialogOpen()) openPalette(); return; }
    if (e.metaKey || e.ctrlKey || e.altKey || isTyping(e.target) || dialogOpen()) return;
    if (e.key === "/") { e.preventDefault(); showPanel("transcript"); T.focusSearch(); }
    else if (e.key === "?") { e.preventDefault(); shortcuts(); }
    else if (e.key === "j" || e.key === "k") {
      e.preventDefault();
      const dir = e.key === "j" ? 1 : -1;
      const p = ctx.selected.panel;
      if (p === "summary" || p === "actions") I.move(p, dir); else T.move(dir);
    } else if (e.key === "e") {
      if (ctx.selected.panel === "summary" || ctx.selected.panel === "actions") { e.preventDefault(); I.editSelected(); }
    } else if (e.key === "Escape") {
      ctx.selected = { panel: ctx.selected.panel, id: null };
      ctx.emit("select");
    }
  });
  // which list j/k move in: the one you last clicked or focused
  main.addEventListener("focusin", (e) => {
    const panel = e.target.closest("[data-panel]");
    if (panel && ["transcript", "summary", "actions"].includes(panel.dataset.panel) && ctx.selected.panel !== panel.dataset.panel && !e.target.closest("[data-item], .line")) ctx.selected = { panel: panel.dataset.panel, id: null };
  });

  // ---------- the live stream ----------
  let citedBefore = new Set();
  function citedNow() { return new Set(ctx.snap ? ctx.snap.summary.items.flatMap((i) => i.source_line_ids || []) : []); }

  function handle(name, data) {
    const changed = applyEvent(ctx, name, data);
    for (const c of changed) {
      if (c === "all") {
        renderHeader();
        T.render({ initial: true });
        I.render();
        P.render();
        A.render();
        if (V) V.render();
        if (L && L.panel.isConnected) L.load();
        renderTabbar();
        citedBefore = citedNow();
      } else if (c.part === "lines") {
        T.render(c);
        I.summary.render();
        I.actions.render();
      } else if (c.part === "activity") {
        if (V) V.render(c);
      } else if (c === "session") {
        renderHeader();
        I.renderMinutes();
        T.render({});
        ctx.emit("llm");
      } else if (c === "people") {
        renderHeader();
        P.render();
        if (!ctx.editing) I.actions.render();
      } else if (c === "summary" || c === "actions") {
        if (c === "summary") {
          const now = citedNow();
          const diff = [...now].filter((x) => !citedBefore.has(x)).concat([...citedBefore].filter((x) => !now.has(x)));
          citedBefore = now;
          if (diff.length) T.render({ upsert: diff.map((id) => ({ id })) });
        }
        ctx.emit(c);
        renderTabbar();
      } else if (c === "presence") {
        ctx.emit("presence-hints");
        I.summary.render();
        I.actions.render();
        P.render();
        renderAvatars();
      } else if (c === "me") {
        renderMe();
        renderBanners();
        I.render();
      } else if (c === "log") {
        ctx.emit("log");
        I.renderMinutes();
      }
    }
  }

  const CONN = {
    polling: ["radio", "Live · polling", "This network doesn't pass the live stream (Cloudflare quick tunnels don't), so the page asks for news instead. Updates still arrive within a second or two."],
    reconnecting: ["loader-circle", "Reconnecting…", "Lost the connection. Trying again…"],
    offline: ["wifi-off", "Offline", "You're offline. The page catches up when you're back."],
  };
  function showConn(s) {
    ctx.conn = s;
    const c = CONN[s];
    conn.hidden = !c;
    conn.dataset.state = s;
    clear(conn);
    if (c) {
      conn.append(icon(c[0], { size: 13, cls: s === "reconnecting" ? "spin" : "" }), h("span", { class: "hide-phone", text: c[1] }));
      conn.title = c[2];
      conn.setAttribute("aria-label", c[1]);
    }
    if (ctx.snap) renderHeader();
    const up = s === "live" || s === "polling";
    if (up) { if (wasDown) toast("Back online", { kind: "success", timeout: 2600 }); wasDown = false; }
    else if (s === "reconnecting" || s === "offline") wasDown = true;
  }
  function revoked(d) {
    clearInterval(ticker);
    live.close();
    if (isOrg) { toast(d.error || "This meeting is no longer available", { kind: "error", timeout: 0 }); return; }
    if (d.state === "need_code") route(ctx, { state: "need_code" });
    else errorScreen(d.state || "error", d.error);
  }
  let wasDown = false;
  const live = new Live(ctx.api, {
    onEvent: handle,
    onState: showConn,
    onRevoked: revoked,
    // what the page already has, so a long poll only sends what's new
    cursor: () => ({ after: ctx.lines.length ? ctx.lines[ctx.lines.length - 1].id : "", act: isOrg ? (ctx.activityNext || 0) : null }),
  });
  // Render the current state from a plain GET first: the page works even if live updates never connect.
  ctx.api.get("/snapshot")
    .then((snap) => { if (!ctx.snap) handle("snapshot", snap); })
    .catch((err) => { if ([401, 403, 404, 410].includes(err.status)) revoked(err.data || {}); });
  live.start();
  window.addEventListener("beforeunload", () => live.close());
}

boot().catch((err) => {
  console.warn(err);
  errorScreen(err instanceof ApiError ? err.state : "error", err.message);
});
