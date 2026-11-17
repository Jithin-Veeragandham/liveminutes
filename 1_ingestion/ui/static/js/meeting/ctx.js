// The meeting page's shared state, and the small API every view uses (ctx).

import { Api } from "../api.js";
import { setPeopleOrder } from "../util.js";

export function createCtx(cfg) {
  const isOrg = cfg.role === "organizer";
  let orgName = "Organizer";
  try { orgName = localStorage.getItem(`lm-org-name:${cfg.sid}`) || "Organizer"; } catch { /* ignore */ }

  const ctx = {
    cfg,
    isOrg,
    api: new Api(cfg.api, { csrf: cfg.csrf || "", name: isOrg ? orgName : null }),
    snap: null,           // the last snapshot, kept up to date by stream events
    lines: [],            // transcript lines, sorted by start
    lineIndex: new Map(), // id -> line
    conn: "connecting",
    serverOffset: 0,      // server clock - this clock, for the elapsed timer
    selected: { panel: "transcript", id: null },
    editing: null,        // {kind, id, base, draft} while an item is being edited
    activity: [],
    listeners: new Map(),
    ask: { thread: [], busy: false },
  };

  ctx.on = (part, fn) => {
    if (!ctx.listeners.has(part)) ctx.listeners.set(part, new Set());
    ctx.listeners.get(part).add(fn);
  };
  ctx.emit = (part, data) => (ctx.listeners.get(part) || []).forEach((fn) => fn(data));

  ctx.me = () => (ctx.snap && ctx.snap.me) || { role: cfg.role, name: null, can_edit: false };
  ctx.canEdit = () => !!ctx.me().can_edit && ctx.snap && ctx.snap.session.status !== "declined";
  ctx.myName = () => ctx.me().name;
  ctx.people = () => (ctx.snap ? ctx.snap.people : []);
  ctx.consented = () => ctx.people().filter((p) => p.decision === "yes").map((p) => p.name);
  ctx.person = (name) => ctx.people().find((p) => p.name === name);
  ctx.online = () => new Set((ctx.snap && ctx.snap.presence.online) || []);
  ctx.editingBy = (itemId) => {
    const e = (ctx.snap && ctx.snap.presence.editing) || {};
    return Object.entries(e).filter(([name, id]) => id === itemId && name !== ctx.myName()).map(([name]) => name);
  };
  ctx.now = () => Date.now() + ctx.serverOffset;
  ctx.setOrgName = (name) => {
    ctx.api.name = name;
    try { localStorage.setItem(`lm-org-name:${cfg.sid}`, name); } catch { /* ignore */ }
    if (ctx.snap) { ctx.snap.me = { ...ctx.snap.me, name }; ctx.emit("me"); }
  };
  ctx.item = (id) => {
    if (!ctx.snap) return null;
    return ctx.snap.summary.items.find((i) => i.id === id) || ctx.snap.actions.items.find((i) => i.id === id) || null;
  };
  ctx.replaceItem = (item) => {
    if (!item || !ctx.snap) return;
    const list = item.kind === "summary" ? ctx.snap.summary.items : ctx.snap.actions.items;
    const i = list.findIndex((x) => x.id === item.id);
    if (item.deleted) { if (i >= 0) list.splice(i, 1); }
    else if (i >= 0) list[i] = item;
    else list.unshift(item);
    ctx.emit(item.kind === "summary" ? "summary" : "actions");
  };
  ctx.citedBy = (lineId) => (ctx.snap ? ctx.snap.summary.items.filter((i) => (i.source_line_ids || []).includes(lineId)) : []);
  return ctx;
}

/** Fold one stream event into ctx.snap. Returns the parts that changed. */
export function applyEvent(ctx, name, data) {
  if (name === "snapshot" || name === "people") {
    const people = name === "snapshot" ? data.people : data;
    setPeopleOrder([...people.filter((p) => p.decision === "yes"), ...people].map((p) => p.name));
  }
  if (name === "snapshot") {
    ctx.snap = data;
    ctx.serverOffset = data.session.server_ms - Date.now();
    ctx.lines = [...data.lines];
    ctx.lineIndex = new Map(ctx.lines.map((l) => [l.id, l]));
    if (data.activity) ctx.activity = [...data.activity.items];
    return ["all"];
  }
  if (!ctx.snap) return [];
  if (name === "lines") {
    const added = [];
    for (const id of data.remove || []) ctx.lineIndex.delete(id);
    for (const l of data.upsert || []) {
      if (!ctx.lineIndex.has(l.id)) added.push(l.id);
      ctx.lineIndex.set(l.id, l);
    }
    ctx.lines = [...ctx.lineIndex.values()].sort((a, b) => a.start - b.start);
    ctx.snap.lines = ctx.lines;
    return [{ part: "lines", added, upsert: data.upsert || [], remove: data.remove || [] }];
  }
  if (name === "activity") {
    ctx.activity.push(...data.items);
    if (ctx.activity.length > 600) ctx.activity.splice(0, ctx.activity.length - 600);
    return [{ part: "activity", added: data.items }];
  }
  if (name === "session") ctx.serverOffset = data.server_ms - Date.now();
  ctx.snap[name] = data;
  return [name];
}
