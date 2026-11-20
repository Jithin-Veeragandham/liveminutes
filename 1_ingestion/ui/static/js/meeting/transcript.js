// The live transcript: keyed rendering (new lines slide in), auto-scroll that pauses when you scroll up
// ("Jump to live"), search with highlights, a speaker filter, "Multiple speakers" lines, and a polite
// screen-reader announcement for new lines.

import { h, clear, mmss, displayName, debounce } from "../util.js";
import { icon } from "../icons.js";
import { avatar, nameEl, empty, skeletonLines, menu, tooltip } from "../ui.js";

const CONT_GAP_S = 75; // same speaker within this many seconds: no repeated avatar and name

function multiNames(speaker) {
  const m = /^Multiple speakers \((.*)\)$/.exec(speaker || "");
  return m ? m[1].split(/,\s*/).filter(Boolean) : [];
}

/** Text with each match of `q` wrapped in <mark>, built from text nodes (never HTML). */
function highlighted(text, q) {
  if (!q) return [text];
  const out = [];
  const lower = text.toLowerCase();
  let i = 0;
  for (;;) {
    const j = lower.indexOf(q, i);
    if (j < 0) break;
    if (j > i) out.push(text.slice(i, j));
    out.push(h("mark", { text: text.slice(j, j + q.length) }));
    i = j + q.length;
  }
  out.push(text.slice(i));
  return out;
}

export function transcriptView(ctx) {
  const els = new Map(); // line id -> element
  let query = "";
  let speakers = null; // null = all, else Set of names
  let follow = true;
  let unseen = 0;
  let announced = 0;

  const count = h("span", { class: "count", text: "0" });
  const filterBtn = h("button", { class: "btn btn--sm", type: "button", attrs: { "aria-haspopup": "menu", "aria-expanded": "false" } },
    icon("filter", { size: 14 }), h("span", { class: "filter-label", text: "All speakers" }), icon("chevron-down", { size: 14 }));
  const search = h("input", { class: "input", type: "search", placeholder: "Search transcript", attrs: { "aria-label": "Search the transcript", autocomplete: "off" } });
  const matchInfo = h("span", { class: "muted", style: { fontSize: "12px", whiteSpace: "nowrap" }, attrs: { "aria-live": "polite" } });
  const list = h("div", { class: "transcript", attrs: { role: "list", "aria-label": "Transcript lines" } });
  const status = h("div");
  const body = h("div", { class: "panel-body", attrs: { tabindex: "-1" } }, status, list);
  const jump = h("button", { class: "jump", type: "button", hidden: true, on: { click: () => jumpLive() } }, icon("arrow-down", { size: 14 }), h("span", { text: "Jump to live" }));
  const panel = h("section", { class: "panel panel--transcript", attrs: { "aria-labelledby": "h-transcript" }, dataset: { panel: "transcript" } },
    h("div", { class: "panel-head" }, icon("message-square", { size: 16, cls: "head-icon" }), h("h2", { id: "h-transcript", text: "Transcript" }), count,
      h("span", { class: "spacer" }), filterBtn),
    h("div", { class: "panel-tools" },
      h("div", { class: "input-icon grow" }, icon("search", { size: 15 }), search, h("span", { class: "kbd hide-phone", text: "/" })), matchInfo),
    body, jump);

  body.appendChild(skeletonLines(7));

  search.addEventListener("input", debounce(() => { query = search.value.trim().toLowerCase(); render({ full: true }); }, 120));
  search.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { search.value = ""; query = ""; render({ full: true }); search.blur(); body.focus(); }
    if (e.key === "Enter") { const first = [...els.values()].find((el) => !el.hidden); if (first) { selectLine(first.dataset.id, { scroll: true, flash: true }); } }
  });
  body.addEventListener("scroll", () => {
    const near = body.scrollHeight - body.scrollTop - body.clientHeight < 90;
    if (near !== follow) { follow = near; if (follow) { unseen = 0; } updateJump(); }
  }, { passive: true });
  filterBtn.addEventListener("click", () => {
    const names = [...new Set(ctx.lines.map((l) => l.speaker))];
    menu(filterBtn, [
      { label: "All speakers", checked: speakers === null, run: () => { speakers = null; render({ full: true }); } },
      "sep",
      ...names.map((n) => ({ label: displayName(n), checked: speakers !== null && speakers.has(n), run: () => {
        speakers = speakers ? new Set(speakers) : new Set();
        if (speakers.has(n)) speakers.delete(n); else speakers.add(n);
        if (!speakers.size) speakers = null;
        render({ full: true });
      } })),
    ], { label: "Filter by speaker" });
  });

  function updateJump() {
    jump.hidden = follow || !ctx.lines.length;
    jump.lastChild.textContent = unseen ? `Jump to live · ${unseen} new` : "Jump to live";
  }

  function jumpLive() {
    follow = true;
    unseen = 0;
    body.scrollTo({ top: body.scrollHeight, behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    updateJump();
  }

  function lineEl(l) {
    const el = h("div", {
      class: "line", attrs: { role: "listitem", tabindex: "-1", "aria-current": null }, dataset: { id: l.id },
      on: { click: () => selectLine(l.id), focus: () => { if (ctx.selected.id !== l.id) selectLine(l.id, { quiet: true }); } },
    });
    fillLine(el, l);
    return el;
  }

  function fillLine(el, l) {
    clear(el);
    const names = multiNames(l.speaker);
    el.classList.toggle("line--multi", !!l.multi);
    el.classList.toggle("line--unknown", !!l.unknown);
    const who = l.multi
      ? h("span", { class: "mini-avatars line-avatar", attrs: { "aria-hidden": "true" } }, (names.length ? names : ["?"]).slice(0, 3).map((n) => avatar(n, { size: "xs" })))
      : avatar(l.speaker, { cls: "line-avatar" });
    if (!l.multi) who.classList.add("line-avatar");
    const label = l.multi
      ? h("span", { class: "name" }, "Multiple speakers", names.length ? h("span", { class: "muted", style: { fontStyle: "normal", fontWeight: "400" }, text: ` · ${names.map(displayName).join(", ")}` }) : null)
      : nameEl(l.speaker);
    const cited = ctx.citedBy(l.id);
    const meta = h("div", { class: "line-meta" }, label, h("time", { class: "time", text: mmss(l.start) }),
      cited.length ? citedChip(cited) : null);
    el.append(h("span", { class: "line-time-cont", attrs: { "aria-hidden": "true" }, text: mmss(l.start) }), who,
      h("div", { class: "grow" }, meta, h("div", { class: "line-text" }, highlighted(l.text, query))));
    el.setAttribute("aria-label", `${l.multi ? "Multiple speakers" : displayName(l.speaker)} at ${mmss(l.start)}: ${l.text}`);
  }

  function citedChip(items) {
    const b = h("button", {
      class: "cited", type: "button",
      attrs: { "aria-label": `Cited in ${items.length} summary item${items.length > 1 ? "s" : ""}` },
      on: { click: (e) => { e.stopPropagation(); ctx.emit("goto-item", { id: items[0].id }); } },
    }, icon("sparkles", { size: 11 }), String(items.length));
    tooltip(b, `Cited in the summary (${items.length}). Click to see it.`);
    return b;
  }

  function matches(l) {
    if (speakers && !speakers.has(l.speaker)) return false;
    if (!query) return true;
    return l.text.toLowerCase().includes(query) || displayName(l.speaker).toLowerCase().includes(query);
  }

  function renderStatus() {
    clear(status);
    const s = ctx.snap && ctx.snap.session;
    if (!s) return;
    if (s.status === "paused" && ctx.lines.length) {
      status.appendChild(h("div", { class: "paused-bar", attrs: { role: "status" } }, icon("pause", { size: 15 }),
        h("span", {}, h("strong", { text: "Recording paused. " }), s.waiting.length ? `Waiting for ${s.waiting.map(displayName).join(", ")} to reply yes in the chat.` : "Waiting for consent.")));
    }
  }

  function emptyState() {
    const s = ctx.snap.session;
    if (s.status === "declined") return empty({ iconName: "ban", kind: "warning", title: "Recording was stopped", text: "Someone said no, so nothing was kept." });
    if (s.status === "joining") return empty({ iconName: "bot", kind: "accent", title: "The bot is joining", text: "Admit “LiveMinutes” from the lobby. It asks everyone for consent before recording." });
    if (s.status === "paused") return empty({ iconName: "shield-check", kind: "accent", title: "Waiting for everyone to agree",
      text: s.waiting.length ? `Recording starts once ${s.waiting.map(displayName).join(", ")} ${s.waiting.length > 1 ? "reply" : "replies"} yes in the meeting chat.` : "Recording starts once everyone replies yes in the meeting chat." });
    if (s.status === "ended") return empty({ iconName: "message-square", title: "No transcript", text: "Nothing was transcribed in this meeting." });
    return empty({ iconName: "mic", kind: "accent", title: "Listening…", text: "Lines appear about 1.5 seconds after someone stops talking." });
  }

  function render(change = {}) {
    if (!ctx.snap) return;
    const skeleton = body.querySelector('[aria-hidden="true"]:not(.transcript)');
    if (skeleton && skeleton.parentNode === body) skeleton.remove();
    renderStatus();
    const added = change.added || [];
    const wasNear = follow;
    const seen = new Set();
    let prev = null;
    let shown = 0;
    let firstHit = null;
    // build/refresh in order
    let cursor = null;
    for (const l of ctx.lines) {
      seen.add(l.id);
      let el = els.get(l.id);
      const isNew = !el;
      if (isNew) { el = lineEl(l); els.set(l.id, el); if (added.includes(l.id)) el.classList.add("is-new"); }
      else if (change.full || (change.upsert || []).some((u) => u.id === l.id) || change.cites) fillLine(el, l);
      const cont = prev && prev.speaker === l.speaker && !l.multi && l.start - prev.end < CONT_GAP_S;
      el.classList.toggle("is-cont", !!cont);
      const visible = matches(l);
      el.hidden = !visible;
      if (visible) { shown++; if (!firstHit && query) firstHit = el; }
      const want = cursor ? cursor.nextSibling : list.firstChild;
      if (want !== el) list.insertBefore(el, want);
      cursor = el;
      prev = l;
    }
    for (const [id, el] of els) if (!seen.has(id)) { el.remove(); els.delete(id); }
    count.textContent = String(ctx.lines.length);
    matchInfo.textContent = query || speakers ? `${shown} of ${ctx.lines.length}` : "";
    filterBtn.querySelector(".filter-label").textContent = speakers ? `${speakers.size} speaker${speakers.size > 1 ? "s" : ""}` : "All speakers";
    filterBtn.classList.toggle("btn--primary", false);
    body.querySelectorAll(":scope > .empty").forEach((el) => el.remove());
    if (!ctx.lines.length) body.appendChild(emptyState());
    else if (!shown) body.appendChild(empty({ iconName: "search", title: "No matches", text: "Try another word, or clear the speaker filter." }));
    if (added.length) {
      if (wasNear) requestAnimationFrame(() => { body.scrollTop = body.scrollHeight; });
      else { unseen += added.length; }
      announce(added);
    }
    if (change.initial) requestAnimationFrame(() => { body.scrollTop = body.scrollHeight; });
    updateJump();
    if (change.full && firstHit && query) firstHit.scrollIntoView({ block: "center" });
    setTimeout(() => list.querySelectorAll(".is-new").forEach((el) => el.classList.remove("is-new")), 400);
  }

  const sr = document.getElementById("sr-live");
  const flushAnnounce = debounce(() => {
    const fresh = ctx.lines.slice(announced).filter(matches).slice(-2);
    announced = ctx.lines.length;
    if (sr && fresh.length) sr.textContent = fresh.map((l) => `${l.multi ? "Multiple speakers" : displayName(l.speaker)}: ${l.text}`).join(" … ");
  }, 900);
  function announce() { flushAnnounce(); }

  function selectLine(id, { scroll = false, flash = false, quiet = false } = {}) {
    const el = els.get(id);
    if (!el) return;
    ctx.selected = { panel: "transcript", id };
    ctx.emit("select");
    if (el.hidden) { query = ""; search.value = ""; speakers = null; render({ full: true }); }
    if (scroll) { el.scrollIntoView({ block: "center", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" }); follow = false; updateJump(); }
    if (flash) { el.classList.remove("is-flash"); void el.offsetWidth; el.classList.add("is-flash"); }
    if (!quiet) el.focus({ preventScroll: true });
  }

  function markSelected() {
    for (const [id, el] of els) {
      const on = ctx.selected.panel === "transcript" && ctx.selected.id === id;
      if (on) el.setAttribute("aria-current", "true"); else el.removeAttribute("aria-current");
      el.tabIndex = on ? 0 : -1;
    }
  }
  ctx.on("select", markSelected);

  function move(dir) {
    const visible = ctx.lines.filter(matches);
    if (!visible.length) return;
    let i = visible.findIndex((l) => l.id === ctx.selected.id);
    i = i < 0 ? (dir > 0 ? 0 : visible.length - 1) : Math.max(0, Math.min(visible.length - 1, i + dir));
    selectLine(visible[i].id, { scroll: false });
    els.get(visible[i].id).scrollIntoView({ block: "nearest" });
  }

  return { id: "transcript", label: "Transcript", iconName: "message-square", panel, render, selectLine, move, jumpLive, focusSearch: () => { search.focus(); search.select(); }, matches };
}
