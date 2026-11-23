// Reusable pieces: avatars, toasts, tooltips, menus, dialogs, drawers, the command palette, empty states.
// All keyboard-usable: dialogs trap focus and give it back, menus move with the arrow keys, Esc closes.

import { h, clear, initials, colorOf, displayName, $, $$, isTyping } from "./util.js";
import { icon } from "./icons.js";

// ---------- avatars ----------
export function avatar(name, { size = "", state = null, online = false, title = null } = {}) {
  if (name === "Organizer") {  // the organizer's own view: a neutral badge, not a person's color
    const el = h("span", { class: `avatar ${size ? "avatar--" + size : ""}`, attrs: { "aria-hidden": "true" } }, icon("shield-check", { size: 12 }));
    if (online) el.appendChild(h("span", { class: "presence-dot", attrs: { "aria-hidden": "true" } }));
    return el;
  }
  const known = name && !/^(Unknown speaker|Multiple speakers)/.test(name);
  const el = h("span", {
    class: `avatar ${size ? "avatar--" + size : ""}`,
    dataset: known ? { p: colorOf(name) } : {},
    attrs: { "aria-hidden": title ? null : "true", role: title ? "img" : null, "aria-label": title },
  }, known ? initials(displayName(name)) : "?");
  if (state) el.dataset.state = state;
  if (online) el.appendChild(h("span", { class: "presence-dot", attrs: { "aria-hidden": "true" } }));
  return el;
}

export function nameEl(name, cls = "name") {
  const known = name && !/^(Unknown speaker|Multiple speakers)/.test(name);
  return h("span", { class: cls, dataset: known ? { p: colorOf(name) } : {}, text: displayName(name) });
}

// ---------- toasts ----------
const KIND_ICON = { success: "circle-check", error: "alert-circle", warning: "alert", info: "info" };

export function toast(message, { kind = "info", action = null, timeout = 4200 } = {}) {
  const box = $("#toasts");
  if (!box) return;
  const close = () => {
    el.classList.add("is-leaving");
    setTimeout(() => el.remove(), 220);
  };
  const el = h("div", { class: `toast toast--${kind}` },
    icon(KIND_ICON[kind] || "info", { size: 16 }),
    h("span", { class: "grow", text: message }),
    action ? h("button", { class: "btn btn--sm", type: "button", text: action.label, on: { click: () => { action.run(); close(); } } }) : null);
  box.appendChild(el);
  while (box.children.length > 4) box.firstElementChild.remove();
  if (timeout) setTimeout(close, timeout);
  return close;
}

// ---------- tooltips ----------
let tipEl = null;
export function tooltip(target, content) {
  const show = () => {
    hideTip();
    tipEl = h("div", { class: "tooltip", attrs: { role: "tooltip" } });
    if (typeof content === "function") content = content();
    if (Array.isArray(content)) content.forEach((c) => tipEl.appendChild(c instanceof Node ? c : document.createTextNode(c)));
    else tipEl.textContent = content;
    document.body.appendChild(tipEl);
    const r = target.getBoundingClientRect();
    const t = tipEl.getBoundingClientRect();
    let left = r.left + r.width / 2 - t.width / 2;
    left = Math.max(8, Math.min(window.innerWidth - t.width - 8, left));
    let top = r.bottom + 8;
    if (top + t.height > window.innerHeight - 8) top = r.top - t.height - 8;
    tipEl.style.left = `${left}px`;
    tipEl.style.top = `${top}px`;
  };
  target.addEventListener("mouseenter", show);
  target.addEventListener("focus", show);
  target.addEventListener("mouseleave", hideTip);
  target.addEventListener("blur", hideTip);
}
export function hideTip() { if (tipEl) { tipEl.remove(); tipEl = null; } }

// ---------- popovers and menus ----------
let openPopover = null;
export function closePopover() {
  if (openPopover) { const p = openPopover; openPopover = null; p.close(); }
}

/** A popover anchored to `anchor`. items: [{label, icon, run, hint, danger, checked}] | "sep" | {heading}. */
export function menu(anchor, items, { align = "end", label = "Menu" } = {}) {
  const content = h("div", { attrs: { role: "menu", "aria-label": label } });
  for (const it of items) {
    if (it === "sep") { content.appendChild(h("div", { class: "menu-sep", attrs: { role: "separator" } })); continue; }
    if (it.heading) { content.appendChild(h("div", { class: "menu-label", text: it.heading })); continue; }
    content.appendChild(h("button", {
      class: `menu-item ${it.danger ? "menu-item--danger" : ""}`, type: "button",
      attrs: { role: it.checked !== undefined ? "menuitemradio" : "menuitem", "aria-checked": it.checked === undefined ? null : String(!!it.checked), disabled: it.disabled || null },
      on: { click: () => { closePopover(); it.run && it.run(); } },
    }, it.icon ? icon(it.icon, { size: 15 }) : null, h("span", { text: it.label }), it.hint ? h("span", { class: "hint", text: it.hint }) : null));
  }
  return popover(anchor, content, { align, focusFirst: true });
}

export function popover(anchor, content, { align = "end", focusFirst = false, onClose = null, cls = "" } = {}) {
  closePopover();
  const el = h("div", { class: `popover ${cls}` }, content);
  document.body.appendChild(el);
  const place = () => {
    const r = anchor.getBoundingClientRect();
    const p = el.getBoundingClientRect();
    let left = align === "start" ? r.left : r.right - p.width;
    left = Math.max(8, Math.min(window.innerWidth - p.width - 8, left));
    let top = r.bottom + 6;
    if (top + p.height > window.innerHeight - 8 && r.top - p.height - 6 > 8) top = r.top - p.height - 6;
    el.style.left = `${left}px`;
    el.style.top = `${Math.max(8, top)}px`;
  };
  place();
  anchor.setAttribute("aria-expanded", "true");
  const items = () => $$(".menu-item:not([disabled]), button, [href], input, select, textarea", el).filter((x) => !x.closest("[hidden]"));
  const onKey = (e) => {
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closePopover(); anchor.focus(); }
    else if ((e.key === "ArrowDown" || e.key === "ArrowUp") && el.querySelector('[role="menu"]')) {
      e.preventDefault();
      const list = $$(".menu-item:not([disabled])", el);
      const i = list.indexOf(document.activeElement);
      const next = e.key === "ArrowDown" ? (i + 1) % list.length : (i - 1 + list.length) % list.length;
      list[next]?.focus();
    } else if (e.key === "Tab" && el.querySelector('[role="menu"]')) { closePopover(); }
  };
  const onDown = (e) => { if (!el.contains(e.target) && !anchor.contains(e.target)) closePopover(); };
  document.addEventListener("keydown", onKey, true);
  setTimeout(() => document.addEventListener("pointerdown", onDown, true), 0);
  window.addEventListener("resize", place);
  openPopover = {
    el,
    close() {
      document.removeEventListener("keydown", onKey, true);
      document.removeEventListener("pointerdown", onDown, true);
      window.removeEventListener("resize", place);
      anchor.setAttribute("aria-expanded", "false");
      el.remove();
      onClose && onClose();
    },
  };
  if (focusFirst) setTimeout(() => items()[0]?.focus(), 0);
  return openPopover;
}

// ---------- dialogs ----------
const dialogs = [];
export function dialog({ title, description = null, body = null, actions = [], size = "", iconName = null, iconKind = "", onClose = null, label = null, cls = "" }) {
  const previous = document.activeElement;
  const titleId = "dlg-" + Math.random().toString(36).slice(2, 8);
  const close = (value) => {
    backdrop.classList.add("is-closing");
    document.removeEventListener("keydown", onKey, true);
    setTimeout(() => backdrop.remove(), 150);
    dialogs.splice(dialogs.indexOf(api), 1);
    if (previous && previous.focus) previous.focus();
    onClose && onClose(value);
  };
  const foot = h("div", { class: "dialog-foot" }, actions.map((a) => h("button", {
    class: `btn ${a.primary ? "btn--primary" : ""} ${a.danger ? "btn--danger" : ""}`, type: "button",
    attrs: { "data-autofocus": a.autofocus ? "" : null },
    on: { click: () => (a.run ? a.run(close) : close(a.value)) },
  }, a.icon ? icon(a.icon, { size: 15 }) : null, a.label)));
  const box = h("div", {
    class: `dialog ${size ? "dialog--" + size : ""} ${cls}`,
    attrs: { role: "dialog", "aria-modal": "true", "aria-labelledby": title ? titleId : null, "aria-label": title ? null : label },
  },
  title ? h("div", { class: "dialog-head" },
    iconName ? h("div", { class: `dialog-icon ${iconKind ? "dialog-icon--" + iconKind : ""}` }, icon(iconName, { size: 18 })) : null,
    h("div", { class: "grow" }, h("h2", { id: titleId, text: title }), description ? h("p", { text: description }) : null),
    h("button", { class: "btn btn--ghost btn--icon btn--sm", type: "button", attrs: { "aria-label": "Close" }, on: { click: () => close() } }, icon("x", { size: 16 }))) : null,
  body ? h("div", { class: "dialog-body" }, body) : null,
  actions.length ? foot : null);
  const backdrop = h("div", { class: "backdrop", on: { pointerdown: (e) => { if (e.target === backdrop) close(); } } }, box);
  const onKey = (e) => {
    if (dialogs[dialogs.length - 1] !== api) return;
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
    else if (e.key === "Tab") trapTab(e, box);
  };
  document.addEventListener("keydown", onKey, true);
  document.body.appendChild(backdrop);
  const api = { close, box };
  dialogs.push(api);
  (box.querySelector("[data-autofocus]") || box.querySelector("input, textarea, select") || box.querySelector(".dialog-foot .btn--primary") || box.querySelector("button"))?.focus();
  return api;
}

export function dialogOpen() { return dialogs.length > 0 || !!document.querySelector(".drawer, .palette"); }

function trapTab(e, box) {
  const f = $$('button:not([disabled]), [href], input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])', box).filter((x) => x.offsetParent !== null);
  if (!f.length) return;
  const first = f[0], last = f[f.length - 1];
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
}

export function confirmDialog({ title, description, confirm = "Confirm", danger = false, iconName = "info", iconKind = "" }) {
  return new Promise((resolve) => {
    dialog({ title, description, size: "sm", iconName, iconKind, onClose: (v) => resolve(v === true), actions: [
      { label: "Cancel", value: false },
      { label: confirm, primary: !danger, danger, value: true, autofocus: true },
    ] });
  });
}

// ---------- drawers ----------
export function drawer({ title, body, onClose = null, iconName = null }) {
  const previous = document.activeElement;
  const titleId = "drw-" + Math.random().toString(36).slice(2, 8);
  const close = () => {
    document.removeEventListener("keydown", onKey, true);
    panel.remove();
    shade.remove();
    if (previous && previous.focus && document.contains(previous)) previous.focus();
    onClose && onClose();
  };
  const bodyEl = h("div", { class: "drawer-body" }, body);
  const panel = h("aside", { class: "drawer", attrs: { role: "dialog", "aria-modal": "true", "aria-labelledby": titleId } },
    h("div", { class: "drawer-head" }, iconName ? icon(iconName, { size: 16, cls: "head-icon" }) : null,
      h("h2", { id: titleId, class: "grow", text: title }),
      h("button", { class: "btn btn--ghost btn--icon btn--sm", type: "button", attrs: { "aria-label": "Close" }, on: { click: close } }, icon("x", { size: 16 }))),
    bodyEl);
  const shade = h("div", { class: "drawer-backdrop", on: { pointerdown: close } });
  const onKey = (e) => {
    if (e.key === "Escape" && !document.querySelector(".backdrop")) { e.preventDefault(); close(); }
    else if (e.key === "Tab" && !document.querySelector(".backdrop")) trapTab(e, panel);
  };
  document.addEventListener("keydown", onKey, true);
  document.body.append(shade, panel);
  panel.querySelector(".drawer-head button")?.focus();
  return { close, body: bodyEl, panel };
}

// ---------- command palette ----------
export function palette(commands, { placeholder = "Type a command or search…" } = {}) {
  let selected = 0;
  let shown = commands;
  const list = h("div", { class: "palette-list", attrs: { role: "listbox", id: "palette-list", "aria-label": "Commands" } });
  const input = h("input", {
    type: "text", placeholder, attrs: { "aria-label": "Search commands", role: "combobox", "aria-expanded": "true", "aria-controls": "palette-list", autocomplete: "off", spellcheck: "false" },
  });
  const render = () => {
    clear(list);
    let group = null;
    shown.forEach((c, i) => {
      if (c.group !== group) { group = c.group; list.appendChild(h("div", { class: "menu-label", text: group })); }
      const id = `pal-${i}`;
      list.appendChild(h("button", {
        class: "menu-item", type: "button", id,
        attrs: { role: "option", "aria-selected": String(i === selected), tabindex: "-1" },
        on: { click: () => run(c), mousemove: () => { if (selected !== i) { selected = i; mark(); } } },
      }, icon(c.icon || "command", { size: 15 }), h("span", { text: c.label }), c.hint ? h("span", { class: "hint", text: c.hint }) : null));
    });
    if (!shown.length) list.appendChild(h("div", { class: "empty", style: { minHeight: "120px", padding: "24px" } }, h("p", { text: "No matching commands" })));
    mark();
  };
  const mark = () => {
    $$(".menu-item", list).forEach((el, i) => el.setAttribute("aria-selected", String(i === selected)));
    const el = $(`#pal-${selected}`, list);
    if (el) { el.scrollIntoView({ block: "nearest" }); input.setAttribute("aria-activedescendant", el.id); }
  };
  const run = (c) => { dlg.close(); setTimeout(() => c.run(), 10); };
  input.addEventListener("input", () => {
    const q = input.value.trim().toLowerCase();
    shown = q ? commands.filter((c) => (c.label + " " + (c.keywords || "") + " " + c.group).toLowerCase().includes(q)) : commands;
    selected = 0;
    render();
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); selected = Math.min(shown.length - 1, selected + 1); mark(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); selected = Math.max(0, selected - 1); mark(); }
    else if (e.key === "Enter") { e.preventDefault(); if (shown[selected]) run(shown[selected]); }
  });
  const body = h("div", {}, h("div", { class: "palette-input" }, icon("search", { size: 18 }), input), list,
    h("div", { class: "palette-foot" },
      h("span", {}, h("span", { class: "kbd", text: "↑" }), h("span", { class: "kbd", text: "↓" }), " to move"),
      h("span", {}, h("span", { class: "kbd", text: "↵" }), " to run"),
      h("span", {}, h("span", { class: "kbd", text: "esc" }), " to close")));
  const dlg = dialog({ body, cls: "palette", label: "Command palette" });
  dlg.box.querySelector(".dialog-body").style.padding = "0";
  render();
  input.focus();
  return dlg;
}

// ---------- empty states, skeletons ----------
export function empty({ iconName = "inbox", title, text = null, kind = "", action = null }) {
  return h("div", { class: "empty" },
    h("div", { class: `empty-art ${kind ? "empty-art--" + kind : ""}` }, icon(iconName, { size: 22 })),
    h("h3", { text: title }),
    text ? h("p", { text }) : null,
    action);
}

export function skeletonLines(n = 6) {
  const out = h("div", { attrs: { "aria-hidden": "true" } });
  for (let i = 0; i < n; i++) {
    out.appendChild(h("div", { class: "skel-row" }, h("div", { class: "skel skel-avatar" }),
      h("div", { class: "grow" }, h("div", { class: "skel skel-line", style: { width: `${28 + ((i * 37) % 30)}%` } }),
        h("div", { class: "skel skel-line", style: { width: `${60 + ((i * 53) % 35)}%` } }))));
  }
  return out;
}

export function kbd(...keys) { return keys.map((k) => h("span", { class: "kbd", text: k })); }

export function iconButton(name, label, onClick, { cls = "", size = 16, sm = false } = {}) {
  const b = h("button", { class: `btn btn--ghost btn--icon ${sm ? "btn--sm" : ""} ${cls}`, type: "button", attrs: { "aria-label": label }, on: { click: onClick } }, icon(name, { size }));
  tooltip(b, label);
  return b;
}

export function isOverlayOpen() { return !!document.querySelector(".backdrop, .drawer, .popover"); }
export { isTyping };
