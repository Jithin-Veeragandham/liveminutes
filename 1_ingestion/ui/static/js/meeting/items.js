// Summary and action items, and everything about editing them: inline edit, approve, status, add,
// delete (with Undo), the conflict dialog (optimistic concurrency), the history drawer with diffs and
// Undo, presence hints ("Jithin is editing"), and the minutes bar (Approve minutes).

import { h, clear, append, displayName, firstName, ago, clock, dueLabel, mmss, wordDiff, modKey } from "../util.js";
import { icon } from "../icons.js";
import { avatar, nameEl, empty, toast, menu, dialog, drawer, confirmDialog, tooltip, iconButton, skeletonLines } from "../ui.js";
import { notesStatus } from "./notes.js";

const FIELD_LABELS = { text: "Text", owner: "Owner", due: "Due", status: "Status", deleted: "Deleted", title: "Title", approved: "Minutes approval" };
const STATUS_LABEL = { pending: "Pending", approved: "Approved", done: "Done", draft: "Draft" };
const KIND_LABEL = { summary: "Summary item", action: "Action item", meeting: "Meeting title", minutes: "Minutes" };

export function fieldValue(field, v) {
  if (v === null || v === undefined || v === "") return field === "owner" ? "Unassigned" : field === "due" ? "No date" : "—";
  if (field === "status") return STATUS_LABEL[v] || v;
  if (field === "due") return dueLabel(v).text;
  if (field === "deleted") return v ? "deleted" : "restored";
  if (field === "approved") return v && v.by ? `approved by ${displayName(v.by)}` : "not approved";
  if (field === "owner") return displayName(v);
  return String(v);
}

/** A diff row for one changed field: words struck/added for text, before → after otherwise. */
export function diffRow(f) {
  const row = h("div", { class: "diff-row" });
  if (f.field === "text" || f.field === "title") {
    for (const [kind, t] of wordDiff(f.before, f.after)) row.appendChild(kind === "same" ? document.createTextNode(t) : h(kind === "del" ? "del" : "ins", { text: t }));
  } else if (f.field === null) {
    row.appendChild(h("ins", { text: (f.after && f.after.text) || "added" }));
  } else {
    row.append(h("span", { class: "del", text: fieldValue(f.field, f.before) }), h("span", { class: "arrow", text: "→" }), h("span", { class: "ins", text: fieldValue(f.field, f.after) }));
  }
  return row;
}

export function opVerb(c) {
  if (c.op === "create") return "added this";
  if (c.op === "delete") return "deleted this";
  if (c.op === "undo") return "undid a change";
  if (c.op === "approve") return c.kind === "minutes" ? "approved the minutes" : "approved this";
  return "edited this";
}

export function itemsView(ctx) {
  let actionFilter = "all";
  let presenceTimer = null;
  const known = new Map(); // id -> version, to flash items others changed

  // ---------- API calls ----------
  async function save(item, changes, { quiet = false } = {}) {
    try {
      const r = await ctx.api.post("/edit", { item: item.id, base_version: item.version, changes });
      if (r.item) { known.set(r.item.id, r.item.version); ctx.replaceItem(r.item); }
      if (r.minutes) { ctx.snap.session.minutes = r.minutes; ctx.emit("minutes"); }
      if (!quiet && r.change) toast("Saved", { kind: "success", action: { label: "Undo", run: () => undo(r.item, r.change) } });
      return r;
    } catch (err) {
      if (err.status === 409) { conflict(item, changes, err.data); return null; }
      toast(err.message, { kind: "error" });
      throw err;
    }
  }

  async function undo(item, change) {
    try {
      const r = await ctx.api.post("/undo", { item: item.id, change });
      if (r.item) ctx.replaceItem(r.item);
      if (r.minutes) { ctx.snap.session.minutes = r.minutes; ctx.emit("minutes"); }
      toast("Undone", { kind: "success" });
      return r;
    } catch (err) {
      toast(err.status === 409 ? "Can't undo: it changed again since. Open its history to see." : err.message, { kind: "error", timeout: 6000 });
      return null;
    }
  }

  async function remove(item) {
    try {
      const r = await ctx.api.post("/delete", { item: item.id, base_version: item.version });
      ctx.replaceItem({ ...item, deleted: true });
      toast(`${item.kind === "action" ? "Action item" : "Summary item"} deleted`, { action: { label: "Undo", run: () => undo({ ...item, version: item.version + 1 }, r.change) } });
    } catch (err) {
      if (err.status === 409) conflict(item, {}, err.data); else toast(err.message, { kind: "error" });
    }
  }

  function setPresence(itemId) {
    clearInterval(presenceTimer);
    const ping = () => ctx.api.post("/presence", { item: itemId }).catch(() => {});
    if (!ctx.canEdit()) return;
    ping();
    if (itemId) presenceTimer = setInterval(ping, 8000);
  }

  // ---------- conflict dialog ----------
  function conflict(mine, changes, data) {
    const theirs = data.item || {};
    const who = data.last_change ? displayName(data.last_change.who) : "Someone";
    const when = data.last_change ? ago(data.last_change.ms, ctx.now()) : "";
    const fields = mine.kind === "action" ? ["text", "owner", "due", "status"] : ["text", "status"];
    const card = (title, item, cls) => h("div", { class: `version-card ${cls}` }, h("h3", { text: title }),
      h("dl", {}, fields.flatMap((f) => [h("dt", { text: FIELD_LABELS[f] }), h("dd", { text: fieldValue(f, item[f]) })])));
    const yours = { ...mine, ...changes };
    const dlg = dialog({
      title: "Someone changed this while you were editing",
      description: `${who} saved a new version ${when}. Nothing was overwritten. Choose what to keep.`,
      iconName: "alert", iconKind: "warning", size: "wide",
      body: h("div", { class: "conflict-grid" }, card(`Their version · ${who}`, theirs, "version-card--theirs"), card("Your version", yours, "")),
      actions: [
        { label: "Keep theirs", run: (close) => { ctx.replaceItem(theirs); ctx.editing = null; ctx.emit(mine.kind === "summary" ? "summary" : "actions"); close(); toast("Kept their version"); } },
        { label: "Edit again", run: (close) => { ctx.replaceItem(theirs); startEdit(theirs, { draft: yours }); close(); } },
        { label: "Save mine", primary: true, autofocus: true, run: async (close) => { close(); ctx.editing = null; await save(theirs, changes); } },
      ],
    });
    return dlg;
  }

  // ---------- history drawer ----------
  async function history(item) {
    const list = h("div", {}, skeletonLines(3));
    const d = drawer({ title: `History · ${KIND_LABEL[item.kind] || "Item"}`, body: list, iconName: "history" });
    try {
      const r = await ctx.api.get(`/history?item=${encodeURIComponent(item.id)}`);
      clear(list);
      const current = ctx.item(item.id) || item;
      list.appendChild(h("p", { class: "muted", style: { fontSize: "13px", marginBottom: "18px" } },
        h("span", { text: current.text || "" })));
      if (!r.changes.length) {
        list.appendChild(empty({ iconName: "history", title: "No edits yet", text: item.origin === "added" ? "Added by hand." : "This is exactly as LiveMinutes wrote it." }));
        return;
      }
      r.changes.forEach((c, i) => {
        const canUndo = i === 0 && ctx.canEdit() && current.last_change === c.change;
        list.appendChild(h("div", { class: "history-change" },
          avatar(c.who, { size: "sm" }),
          h("div", { class: "log-head" }, h("strong", { text: displayName(c.who) }), ` ${opVerb(c)} `, h("span", { class: "when", text: `· ${ago(c.ms, ctx.now())}` }),
            c.role === "organizer" ? h("span", { class: "chip", style: { marginLeft: "6px" }, text: "Organizer view" }) : null),
          h("div", { class: "diff" }, c.fields.flatMap((f) => [f.field ? h("div", { class: "diff-field", text: FIELD_LABELS[f.field] || f.field }) : null, diffRow(f)])),
          canUndo ? h("button", { class: "btn btn--sm", type: "button", on: { click: async () => { const ok = await undo(current, c.change); if (ok) d.close(); } } }, icon("undo-2", { size: 14 }), "Undo this change") : null));
      });
      list.appendChild(h("p", { class: "fine", style: { marginTop: "6px" } }, "Undo adds a new entry to the edit log. Nothing in the log is ever removed."));
    } catch (err) {
      clear(list);
      list.appendChild(empty({ iconName: "alert-circle", title: "Couldn't load the history", text: err.message }));
    }
  }

  // ---------- editing ----------
  function startEdit(item, { draft = null } = {}) {
    if (!ctx.canEdit()) { ctx.emit("need-name"); return; }
    ctx.editing = { kind: item.kind, id: item.id, base: item, draft: draft || { ...item } };
    setPresence(item.id);
    ctx.emit(item.kind === "summary" ? "summary" : "actions");
    setTimeout(() => {
      const box = document.querySelector(`[data-item="${CSS.escape(item.id)}"]`);
      box?.querySelector("textarea")?.focus({ preventScroll: true });
      box?.scrollIntoView({ block: "nearest", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    }, 20);
  }

  function stopEdit() {
    const kind = ctx.editing && ctx.editing.kind;
    const id = ctx.editing && ctx.editing.id;
    ctx.editing = null;
    if (id) document.querySelector(`[data-item="${CSS.escape(id)}"]`)?.removeAttribute("data-item");
    setPresence(null);
    if (kind) ctx.emit(kind === "summary" ? "summary" : kind === "new" ? "actions" : "actions");
  }

  function editForm(item, kind) {
    const d = ctx.editing.draft;
    const text = h("textarea", { class: "textarea", attrs: { "aria-label": kind === "summary" ? "Summary text" : "Task", rows: "3", maxlength: kind === "summary" ? "600" : "300" } });
    text.value = d.text || "";
    const grow = () => { text.style.height = "auto"; text.style.height = `${Math.min(220, text.scrollHeight + 2)}px`; };
    text.addEventListener("input", () => { d.text = text.value; grow(); });
    setTimeout(grow, 0);
    const extra = [];
    if (kind === "action") {
      const owner = h("select", { class: "select", attrs: { "aria-label": "Owner" } }, h("option", { value: "", text: "Unassigned" }),
        [...new Set([...ctx.consented(), d.owner].filter(Boolean))].map((n) => h("option", { value: n, text: displayName(n) })));
      owner.value = d.owner || "";
      owner.addEventListener("change", () => { d.owner = owner.value; });
      const due = h("input", { class: "input", type: "date", attrs: { "aria-label": "Due date" } });
      due.value = d.due || "";
      due.addEventListener("change", () => { d.due = due.value; });
      const status = h("select", { class: "select", attrs: { "aria-label": "Status" } }, ["pending", "approved", "done"].map((s) => h("option", { value: s, text: STATUS_LABEL[s] })));
      status.value = d.status || "pending";
      status.addEventListener("change", () => { d.status = status.value; });
      extra.push(h("div", { class: "edit-grid" },
        h("div", { class: "field field--owner" }, h("label", { text: "Owner" }), owner),
        h("div", { class: "field" }, h("label", { text: "Due" }), due),
        h("div", { class: "field" }, h("label", { text: "Status" }), status)));
    }
    const isNew = item.id === "__new";
    const submit = async () => {
      const value = (d.text || "").trim();
      if (!value) { text.focus(); toast("Write something first", { kind: "warning" }); return; }
      saveBtn.disabled = true;
      if (isNew) {
        try {
          const r = await ctx.api.post("/create", { kind: "action", fields: { text: value, owner: d.owner || "", due: d.due || "", status: d.status || "pending" } });
          ctx.editing = null;
          setPresence(null);
          ctx.replaceItem(r.item);
          toast("Action item added", { kind: "success", action: { label: "Undo", run: () => undo(r.item, r.change) } });
        } catch (err) { saveBtn.disabled = false; toast(err.message, { kind: "error" }); }
        return;
      }
      const changes = {};
      for (const f of kind === "action" ? ["text", "owner", "due", "status"] : ["text"]) {
        const v = f === "text" ? value : (d[f] || "");
        if (v !== (item[f] || "")) changes[f] = v;
      }
      if (!Object.keys(changes).length) { stopEdit(); return; }
      // optimistic: show it right away, put it back if the save fails
      const before = { ...item };
      ctx.editing = null;
      setPresence(null);
      ctx.replaceItem({ ...item, ...changes, saving: true });
      try { const r = await save(before, changes); if (!r) ctx.replaceItem(ctx.item(before.id) || before); }
      catch { ctx.replaceItem(before); }
    };
    const saveBtn = h("button", { class: "btn btn--primary btn--sm", type: "button", on: { click: submit } }, icon("check", { size: 14 }), isNew ? "Add" : "Save");
    const presence = h("div", { class: "form-presence", dataset: { presenceFor: item.id }, attrs: { role: "status", "aria-live": "polite" } });
    paintPresence(presence, item.id);
    const form = h("div", { class: "edit-form", on: { keydown: (e) => {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); submit(); }
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); stopEdit(); }
    } } }, presence, text, extra,
      h("div", { class: "row" }, h("span", { class: "help" }, `As ${firstName(ctx.myName())} · ${modKey}↵ saves · Esc cancels`),
        h("button", { class: "btn btn--sm", type: "button", text: "Cancel", on: { click: stopEdit } }), saveBtn));
    return form;
  }

  // ---------- shared bits ----------
  function sourceChips(item) {
    return (item.source_line_ids || []).map((id) => {
      const line = ctx.lineIndex.get(id);
      const b = h("button", {
        class: "chip src-chip", type: "button", disabled: !line,
        attrs: { "aria-label": line ? `Source: ${displayName(line.speaker)} at ${mmss(line.start)}` : "Source line not available" },
        on: { click: (e) => { e.stopPropagation(); if (line) ctx.emit("goto-line", { id }); } },
      }, icon("quote", { size: 11 }), line ? mmss(line.start) : "—");
      if (line) tooltip(b, [h("strong", { text: displayName(line.speaker) }), document.createTextNode(`: ${line.text.slice(0, 140)}${line.text.length > 140 ? "…" : ""}`)]);
      return b;
    });
  }

  function editedNote(item) {
    if (item.saving) return h("span", { class: "edited", text: "Saving…" });
    if (!item.edited_by) return null;
    return h("span", { class: "edited", text: `${item.origin === "added" && item.version === 1 ? "Added" : "Edited"} by ${firstName(item.edited_by)} · ${ago(item.edited_ms, ctx.now())}` });
  }

  function editingHint(item) {
    const others = ctx.editingBy(item.id);
    if (!others.length) return null;
    return h("span", { class: "editing-hint", attrs: { role: "status" } }, avatar(others[0], { size: "xs" }), `${firstName(others[0])}${others.length > 1 ? ` +${others.length - 1}` : ""} is editing…`);
  }

  /** "Maya is editing this too", inside an open editor; kept current as presence changes. */
  function paintPresence(el, itemId) {
    clear(el);
    const others = ctx.editingBy(itemId);
    el.hidden = !others.length;
    if (others.length) el.append(icon("users", { size: 13 }), document.createTextNode(`${others.map(firstName).join(", ")} ${others.length > 1 ? "are" : "is"} editing this too. Whoever saves second will be asked which version to keep.`));
  }
  ctx.on("presence-hints", () => document.querySelectorAll("[data-presence-for]").forEach((el) => paintPresence(el, el.dataset.presenceFor)));

  function actionsMenu(btn, item) {
    const isAction = item.kind === "action";
    const approved = item.status === "approved";
    menu(btn, [
      { label: "Edit", icon: "pencil", hint: "E", run: () => startEdit(item), disabled: !ctx.canEdit() },
      { label: approved ? (isAction ? "Mark pending" : "Unapprove") : "Approve", icon: "badge-check", disabled: !ctx.canEdit(),
        run: () => save(item, { status: approved ? (isAction ? "pending" : "draft") : "approved" }) },
      { label: "History", icon: "history", run: () => history(item) },
      "sep",
      { label: "Delete", icon: "delete", danger: true, disabled: !ctx.canEdit(), run: () => remove(item) },
    ], { label: "Item actions" });
  }

  function select(panel, id, { focus = true } = {}) {
    ctx.selected = { panel, id };
    ctx.emit("select");
    const el = document.querySelector(`[data-item="${CSS.escape(id)}"]`);
    if (el && focus) el.focus({ preventScroll: true });
  }

  function flashIfChanged(el, item) {
    const was = known.get(item.id);
    if (was === undefined) { if (known.size && initialDone) el.classList.add("is-new"); }
    else if (was !== item.version && item.edited_by !== ctx.myName()) el.classList.add("is-updated");
    known.set(item.id, item.version);
  }
  let initialDone = false;

  /** Re-render `list` but keep the element being edited (so typing isn't interrupted) and the focus. */
  function rebuild(list, build) {
    const editingId = ctx.editing && ctx.editing.id;
    const open = editingId ? list.querySelector(`[data-item="${CSS.escape(editingId)}"]`) : null;
    const keep = open && open.querySelector(".edit-form") ? open : null; // only an editor that is already open
    const active = document.activeElement;
    const focused = list.contains(active) ? active.closest("[data-item]") : null;
    const focusId = focused && focused !== keep ? focused.dataset.item : null;
    // taking the open editor out of the page (even for a moment) blurs it: remember the caret and put it back
    const caret = keep && keep.contains(active) ? { el: active, start: active.selectionStart, end: active.selectionEnd } : null;
    const scroller = list.closest(".panel-body");
    const top = scroller ? scroller.scrollTop : 0;
    clear(list);
    build((id, make) => (keep && id === editingId ? keep : make()));
    if (scroller) scroller.scrollTop = top;
    if (caret) {
      caret.el.focus({ preventScroll: true });
      try { if (caret.start !== null && caret.start !== undefined) caret.el.setSelectionRange(caret.start, caret.end); } catch { /* not a text field */ }
    } else if (focusId) list.querySelector(`[data-item="${CSS.escape(focusId)}"]`)?.focus({ preventScroll: true });
  }

  // ================= summary =================
  const sCount = h("span", { class: "count", text: "0" });
  const sLabel = h("span", { class: "chip chip--warning", hidden: true });
  const sUpdated = h("span", { class: "muted hide-phone", style: { fontSize: "12px", whiteSpace: "nowrap" } });
  const sNote = h("div");
  const sList = h("ul", { class: "items", attrs: { "aria-label": "Summary" } });
  const sBody = h("div", { class: "panel-body" }, sNote, skeletonLines(4), sList);
  const sMenu = iconButton("more", "Summary options", (e) => menu(e.currentTarget, [
    { label: "Export minutes (Markdown)", icon: "download", run: () => ctx.emit("export") },
    { label: "Print or save as PDF", icon: "printer", run: () => ctx.emit("print") },
  ], { label: "Summary options" }), { sm: true });
  const sStatus = notesStatus(ctx);   // the LLM's live notes: last update, countdown, delays
  const summaryPanel = h("section", { class: "panel panel--summary", attrs: { "aria-labelledby": "h-summary" }, dataset: { panel: "summary" } },
    h("div", { class: "panel-head" }, icon("sparkles", { size: 16, cls: "head-icon" }), h("h2", { id: "h-summary", text: "Summary" }), sCount, sLabel, h("span", { class: "spacer" }), sUpdated, sMenu),
    sStatus.el, sBody);

  function summaryItem(item) {
    const editing = ctx.editing && ctx.editing.id === item.id;
    const decision = item.type === "decision";
    const selected = ctx.selected.panel === "summary" && ctx.selected.id === item.id;
    const approved = item.status === "approved";
    const li = h("li", {
      class: `item ${decision ? "item--decision" : ""}`, dataset: { item: item.id },
      attrs: { tabindex: selected ? "0" : "-1", "aria-current": selected ? "true" : null, "aria-label": `${decision ? "Decision" : "Summary point"}: ${item.text}` },
      on: { click: (e) => { if (!e.target.closest("button, textarea, input, select")) select("summary", item.id); }, dblclick: () => startEdit(item) },
    },
    h("div", { class: "item-bullet" }, decision ? icon("flag", { size: 14 }) : h("span", { class: "bullet" })),
    editing ? null : h("div", { class: "item-text", text: item.text }),
    editing ? null : h("div", { class: "item-actions" },
      ctx.canEdit() ? iconButton("pencil", "Edit (E)", () => startEdit(item), { sm: true, size: 14 }) : null,
      iconButton("history", "History", () => history(item), { sm: true, size: 14 }),
      iconButton("more", "More", (e) => actionsMenu(e.currentTarget, item), { sm: true, size: 14 })),
    editing ? editForm(item, "summary") : h("div", { class: "item-meta" },
      decision ? h("span", { class: "chip chip--accent" }, icon("flag", { size: 11 }), "Decision") : null,
      approved ? h("span", { class: "chip chip--success" }, icon("badge-check", { size: 11 }), `Approved${item.approved_by ? " by " + firstName(item.approved_by) : ""}`) : null,
      sourceChips(item), editedNote(item), editingHint(item)));
    if (!editing) flashIfChanged(li, item);
    return li;
  }

  function renderSummary() {
    if (!ctx.snap) return;
    sStatus.render();
    const s = ctx.snap.summary;
    sBody.querySelectorAll(':scope > [aria-hidden="true"]').forEach((x) => x.remove());
    clear(sNote);
    if (s.sample) sNote.appendChild(h("div", { class: `callout ${s.label === "Demo" ? "callout--info" : "callout--warning"} panel-note` },
      icon(s.label === "Demo" ? "play" : "info", { size: 15 }), h("span", { text: s.note })));
    sLabel.hidden = !s.label;
    sLabel.className = `chip ${s.label === "Demo" ? "chip--accent" : "chip--warning"}`;
    sLabel.textContent = s.label || "";
    sCount.textContent = String(s.items.length);
    const latest = Math.max(0, ...s.items.map((i) => i.edited_ms || 0));
    sUpdated.textContent = latest ? `Edited ${ago(latest, ctx.now())}` : "";
    rebuild(sList, (reuse) => {
      if (!s.items.length) sList.appendChild(h("li", {}, empty({ iconName: "sparkles", kind: "accent", title: "No summary yet", text: "Points and decisions appear here after a few minutes of talk." })));
      s.items.forEach((item) => sList.appendChild(reuse(item.id, () => summaryItem(item))));
    });
  }

  // ================= action items =================
  const aCount = h("span", { class: "count", text: "0" });
  const aLabel = h("span", { class: "chip chip--warning", hidden: true });
  const filters = h("div", { class: "segmented", attrs: { role: "group", "aria-label": "Show" } });
  const addBtn = h("button", { class: "btn btn--sm", type: "button", on: { click: () => addNew() } }, icon("plus", { size: 14 }), "Add");
  const aList = h("div", { attrs: { role: "list", "aria-label": "Action items" } });
  const aBody = h("div", { class: "panel-body" }, skeletonLines(3), aList);
  const aStatus = notesStatus(ctx, { compact: true });
  const actionsPanel = h("section", { class: "panel panel--actions", attrs: { "aria-labelledby": "h-actions" }, dataset: { panel: "actions" } },
    h("div", { class: "panel-head" }, icon("list-checks", { size: 16, cls: "head-icon" }), h("h2", { id: "h-actions", text: "Action items" }), aCount, aLabel, h("span", { class: "spacer" }), addBtn),
    aStatus.el,
    h("div", { class: "panel-tools" }, filters),
    aBody);
  ctx.on("llm", () => { sStatus.render(); aStatus.render(); });

  function addNew() {
    if (!ctx.canEdit()) { ctx.emit("need-name"); return; }
    actionFilter = "all";
    startEdit({ id: "__new", kind: "action", text: "", owner: ctx.myName() && ctx.consented().includes(ctx.myName()) ? ctx.myName() : "", due: "", status: "pending", version: 0 });
  }

  function actionCard(item) {
    const editing = ctx.editing && ctx.editing.id === item.id;
    const selected = ctx.selected.panel === "actions" && ctx.selected.id === item.id;
    const done = item.status === "done";
    const due = dueLabel(item.due);
    const card = h("div", {
      class: `action-card ${done ? "is-done" : ""}`, dataset: { item: item.id },
      attrs: { role: "listitem", tabindex: selected ? "0" : "-1", "aria-current": selected ? "true" : null, "aria-label": `Action item: ${item.text || "new"}` },
      on: { click: (e) => { if (!e.target.closest("button, textarea, input, select")) select("actions", item.id); }, dblclick: () => item.id !== "__new" && startEdit(item) },
    },
    editing ? h("span", {}) : h("button", {
      class: "check", type: "button", disabled: !ctx.canEdit(),
      attrs: { role: "checkbox", "aria-checked": String(done), "aria-label": done ? "Mark as not done" : "Mark as done" },
      on: { click: (e) => { e.stopPropagation(); save(item, { status: done ? "pending" : "done" }, { quiet: true }); } },
    }, icon("check", { size: 12 })),
    editing ? null : h("div", { class: "item-text", text: item.text }),
    editing ? null : h("div", { class: "item-actions" },
      ctx.canEdit() ? iconButton("pencil", "Edit (E)", () => startEdit(item), { sm: true, size: 14 }) : null,
      iconButton("history", "History", () => history(item), { sm: true, size: 14 }),
      iconButton("more", "More", (e) => actionsMenu(e.currentTarget, item), { sm: true, size: 14 })),
    editing ? editForm(item, "action") : h("div", { class: "item-meta" },
      item.owner ? h("span", { class: "owner" }, avatar(item.owner, { size: "xs" }), displayName(item.owner)) : h("span", { class: "owner owner--none" }, icon("user", { size: 13 }), "Unassigned"),
      item.due ? h("span", { class: `due ${due.overdue && !done ? "due--overdue" : ""}` }, icon("calendar", { size: 13 }), due.overdue && !done ? `Overdue · ${due.text}` : due.text)
        : item.due_text ? h("span", { class: "due", title: "Due, as it was said (no date)" }, icon("calendar", { size: 13 }), item.due_text[0].toUpperCase() + item.due_text.slice(1)) : null,
      h("span", { class: `chip ${item.status === "approved" ? "chip--success" : item.status === "done" ? "chip--accent" : ""}`, text: STATUS_LABEL[item.status] || item.status }),
      sourceChips(item), editedNote(item), editingHint(item)));
    if (!editing && item.id !== "__new") flashIfChanged(card, item);
    return card;
  }

  function renderActions() {
    if (!ctx.snap) return;
    aStatus.render();
    const a = ctx.snap.actions;
    aBody.querySelectorAll(':scope > [aria-hidden="true"]').forEach((x) => x.remove());
    aLabel.hidden = !a.label;
    aLabel.className = `chip ${a.label === "Demo" ? "chip--accent" : "chip--warning"}`;
    aLabel.textContent = a.label || "";
    const me = ctx.myName();
    const counts = { all: a.items.length, mine: a.items.filter((i) => me && i.owner === me).length, pending: a.items.filter((i) => i.status === "pending").length };
    aCount.textContent = String(a.items.length);
    clear(filters);
    for (const [key, label] of [["all", "All"], ["mine", "Mine"], ["pending", "Pending"]]) {
      filters.appendChild(h("button", { type: "button", attrs: { "aria-pressed": String(actionFilter === key) }, on: { click: () => { actionFilter = key; renderActions(); } } },
        label, h("span", { class: "count", text: String(counts[key]) })));
    }
    addBtn.hidden = !ctx.canEdit();
    const shown = a.items.filter((i) => actionFilter === "all" || (actionFilter === "mine" ? me && i.owner === me : i.status === "pending"));
    rebuild(aList, (reuse) => {
    if (ctx.editing && ctx.editing.id === "__new") aList.appendChild(reuse("__new", () => actionCard(ctx.editing.base)));
    if (!shown.length && !(ctx.editing && ctx.editing.id === "__new")) {
      aList.appendChild(empty({
        iconName: "list-checks", kind: actionFilter === "all" ? "accent" : "",
        title: actionFilter === "all" ? "No action items yet" : actionFilter === "mine" ? (me ? "Nothing assigned to you" : "Pick your name to see yours") : "Nothing pending",
        text: actionFilter === "all" ? "Tasks with an owner and a due date show up here as people agree on them." : null,
        action: actionFilter === "all" && ctx.canEdit() ? h("button", { class: "btn btn--sm", type: "button", on: { click: addNew } }, icon("plus", { size: 14 }), "Add one") : null,
      }));
    }
    shown.forEach((item) => aList.appendChild(reuse(item.id, () => actionCard(item))));
    });
  }

  // ================= minutes bar =================
  const minutesBar = h("div", { class: "minutes-bar", attrs: { role: "region", "aria-label": "Minutes" } });
  function renderMinutes() {
    if (!ctx.snap) return;
    const m = ctx.snap.session.minutes || {};
    clear(minutesBar);
    const approved = m.approved;
    minutesBar.classList.toggle("is-approved", !!approved);
    append(minutesBar, [
      h("div", { class: "mb-icon" }, icon(approved ? "badge-check" : "scroll-text", { size: 16 })),
      h("div", { class: "grow" },
        h("h2", { text: approved ? "Minutes approved" : "Meeting minutes" }),
        h("div", { class: "mb-sub ellipsis" }, approved
          ? `By ${displayName(approved.by)} · ${clock(approved.ms)}${m.changed_since ? " · edited since" : ""}`
          : `Draft · ${ctx.snap.log.changes} edit${ctx.snap.log.changes === 1 ? "" : "s"} so far`)),
      approved && m.changed_since ? h("span", { class: "chip chip--warning hide-phone", text: "Changed after approval" }) : null,
      h("button", { class: "btn btn--sm hide-tablet", type: "button", on: { click: () => ctx.emit("export") } }, icon("download", { size: 14 }), "Export"),
      ctx.canEdit() && (!approved || m.changed_since) ? h("button", { class: "btn btn--primary btn--sm", type: "button", on: { click: approveMinutes } }, icon("badge-check", { size: 14 }), approved ? "Approve again" : "Approve minutes") : null]);
  }

  async function approveMinutes() {
    if (!ctx.canEdit()) { ctx.emit("need-name"); return; }
    const ok = await confirmDialog({
      title: "Approve the minutes?", iconName: "badge-check",
      description: `They'll be marked approved by ${displayName(ctx.myName())} at ${clock(ctx.now())}. People can still edit afterwards; the minutes then show "edited since".`,
      confirm: "Approve minutes",
    });
    if (!ok) return;
    try {
      const m = ctx.snap.session.minutes;
      const r = await ctx.api.post("/approve", { base_version: m.version });
      ctx.snap.session.minutes = r.minutes;
      renderMinutes();
      toast("Minutes approved", { kind: "success", action: { label: "Undo", run: () => undo({ id: "minutes", kind: "minutes" }, r.change) } });
    } catch (err) {
      toast(err.status === 409 ? "Someone approved or changed the minutes just now. Try again." : err.message, { kind: "error" });
    }
  }

  // ---------- keyboard helpers ----------
  function move(panel, dir) {
    const items = panel === "summary" ? ctx.snap.summary.items : [...aList.querySelectorAll("[data-item]")].map((el) => ctx.item(el.dataset.item)).filter(Boolean);
    if (!items.length) return;
    let i = items.findIndex((x) => x.id === ctx.selected.id);
    i = i < 0 ? (dir > 0 ? 0 : items.length - 1) : Math.max(0, Math.min(items.length - 1, i + dir));
    select(panel, items[i].id);
    document.querySelector(`[data-item="${CSS.escape(items[i].id)}"]`)?.scrollIntoView({ block: "nearest" });
  }

  function editSelected() {
    const it = ctx.item(ctx.selected.id);
    if (it) startEdit(it);
  }

  function gotoItem(id) {
    const it = ctx.item(id);
    if (!it) return;
    const panel = it.kind === "summary" ? "summary" : "actions";
    ctx.emit("show-panel", panel);
    setTimeout(() => {
      select(panel, id);
      const el = document.querySelector(`[data-item="${CSS.escape(id)}"]`);
      if (el) { el.scrollIntoView({ block: "center" }); el.classList.remove("is-updated"); void el.offsetWidth; el.classList.add("is-updated"); }
    }, 30);
  }

  function markSelected() {
    document.querySelectorAll("[data-item]").forEach((el) => {
      const on = ctx.selected.id === el.dataset.item && (ctx.selected.panel === "summary" || ctx.selected.panel === "actions");
      if (on) el.setAttribute("aria-current", "true"); else el.removeAttribute("aria-current");
      el.tabIndex = on ? 0 : -1;
    });
  }
  ctx.on("select", markSelected);
  ctx.on("summary", renderSummary);
  ctx.on("actions", renderActions);
  ctx.on("minutes", renderMinutes);
  ctx.on("goto-item", (d) => gotoItem(d.id));

  function render() { renderSummary(); renderActions(); renderMinutes(); initialDone = true; }

  return {
    summary: { id: "summary", label: "Summary", iconName: "sparkles", panel: summaryPanel, render: renderSummary },
    actions: { id: "actions", label: "Action items", iconName: "list-checks", panel: actionsPanel, render: renderActions },
    minutesBar, renderMinutes, render, move, editSelected, addNew, approveMinutes, history, stopEdit, gotoItem,
  };
}
