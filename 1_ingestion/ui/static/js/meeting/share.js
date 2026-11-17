// Share (link, access code, QR), export to Markdown, and the print layout ("Save as PDF" = real minutes).

import { h, clear, append, copyText, download, displayName, dateLong, clock, mmss, dueLabel } from "../util.js";
import { icon } from "../icons.js";
import { dialog, toast } from "../ui.js";
import { qrSvg } from "../qr.js";

export function shareLink(ctx) {
  const s = ctx.snap.session;
  return s.share.url;
}

/** The QR opens the page with the code in the #fragment (never sent to the server), so it fills itself in. */
export function joinUrl(ctx) {
  const s = ctx.snap.session;
  return s.share.code ? `${s.share.url}#code=${s.share.code.replace("-", "")}` : s.share.url;
}

export async function copyLink(ctx) {
  toast((await copyText(shareLink(ctx))) ? "Link copied" : "Couldn't copy the link", { kind: "success" });
}

export async function copyCode(ctx) {
  const code = ctx.snap.session.share.code;
  if (!code) { toast("The code was posted in the meeting chat. The organizer can make a new one.", { kind: "warning" }); return; }
  toast((await copyText(code)) ? "Code copied" : "Couldn't copy the code", { kind: "success" });
}

export function shareDialog(ctx) {
  const s = ctx.snap.session;
  if (!s.shared) {
    dialog({ title: "Not shared yet", iconName: "lock", size: "sm",
      description: "The link and access code are made, and posted in the meeting chat, once everyone has agreed to recording.",
      actions: [{ label: "OK", primary: true }] });
    return;
  }
  const body = h("div", { class: "stack", style: { gap: "16px" } });
  const render = () => {
    clear(body);
    const share = ctx.snap.session.share;
    const link = h("input", { class: "input", readOnly: true, value: share.url, attrs: { "aria-label": "Link for participants" } });
    link.value = share.url;
    const left = h("div", { class: "stack", style: { gap: "14px", minWidth: "0" } },
      h("div", { class: "field" }, h("label", { text: "Link" }),
        h("div", { class: "copy-field" }, link, h("button", { class: "btn", type: "button", attrs: { "data-autofocus": "", "aria-label": "Copy link" }, on: { click: () => copyLink(ctx) } }, icon("copy", { size: 14 }), "Copy"))),
      h("div", { class: "field" }, h("label", { text: "Access code" }),
        h("div", { class: "code-display" },
          h("span", { class: "code", text: share.code || "•••-•••", attrs: { "aria-label": share.code ? `Access code ${share.code.split("").join(" ")}` : "Code hidden" } }),
          share.code ? h("button", { class: "btn btn--sm", type: "button", on: { click: () => copyCode(ctx) } }, icon("copy", { size: 13 }), "Copy") : null),
        share.code ? null : h("p", { class: "fine", text: "The code is only kept in memory while the bridge runs. It's in the meeting chat; the organizer can make a new one." })),
      h("p", { class: "fine" }, "Participants open the link, type the code and pick their name. ", h("strong", { text: "Picking a name is not a login" }), `: anyone with the code can choose any name. The code stops working ${ctx.cfg.viewDays || 7} days after the meeting, or if anyone says no.`));
    const qrBox = h("div", { class: "stack", style: { alignItems: "center", gap: "8px" } },
      h("div", { class: "qr" }, qrSvg(joinUrl(ctx), { label: "QR code to join the live notes" })),
      h("span", { class: "fine", text: share.code ? "Scan to join (code included)" : "Scan to open the link" }));
    body.appendChild(h("div", { class: "share-grid" }, left, qrBox));
    if (!share.public) {
      body.appendChild(h("div", { class: "callout callout--warning" }, icon("alert", { size: 16 }),
        h("div", {}, h("strong", { text: "This link only opens on the organizer's computer. " }),
          ctx.isOrg ? h("span", {}, "To let others join, run ", h("code", { text: "cloudflared tunnel --url http://localhost:8776" }), " (or ", h("code", { text: "bash 1_ingestion/tunnel.sh" }), ") and paste its https address below.") : "Ask the organizer to start the tunnel.")));
    }
    if (ctx.isOrg) body.appendChild(organizerShare(ctx, render));
  };
  render();
  dialog({ title: "Invite people to the live notes", description: "Everyone who agreed to recording can follow along and fix the notes.", iconName: "share-2", size: "wide", body,
    actions: [{ label: "Done", primary: true }] });
}

function organizerShare(ctx, rerender) {
  const url = h("input", { class: "input", type: "url", placeholder: "https://something.trycloudflare.com", attrs: { "aria-label": "Public address for participants" } });
  const box = h("div", { class: "stack", style: { gap: "12px", paddingTop: "4px", borderTop: "1px solid var(--divider)" } });
  fetch("/live/api/ui-config", { cache: "no-store" }).then((r) => r.json()).then((c) => { url.value = c.public_base_url || ""; ctx.cfg.viewDays = c.view_access_days; }).catch(() => {});
  const saveUrl = async () => {
    try {
      const r = await fetch("/live/api/public-url", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": ctx.cfg.csrf }, body: JSON.stringify({ url: url.value }) });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error);
      toast(d.public_base_url ? "Public address saved" : "Public address cleared", { kind: "success" });
      await ctx.refresh();
      rerender();
    } catch (err) { toast(err.message || "Couldn't save", { kind: "error" }); }
  };
  const post = h("input", { type: "checkbox", id: "post-code", checked: ctx.snap.session.status !== "ended" && !ctx.snap.session.demo });
  const rotate = async () => {
    try {
      const r = await ctx.api.post("/rotate-code", { post: post.checked });
      ctx.snap.session.share = { ...ctx.snap.session.share, code: r.code, url: r.url, public: r.public };
      toast(r.posted ? "New code posted in the meeting chat" : "New code made. Old links ask for it.", { kind: "success" });
      rerender();
    } catch (err) { toast(err.message, { kind: "error" }); }
  };
  box.append(
    h("div", { class: "field" }, h("label", { text: "Public address (organizer only)" }),
      h("div", { class: "copy-field" }, url, h("button", { class: "btn", type: "button", on: { click: saveUrl } }, icon("check", { size: 14 }), "Save")),
      h("span", { class: "fine", text: "Quick tunnels get a new address every start. tunnel.sh fills this in for you." })),
    h("div", { class: "stack", style: { gap: "6px" } },
      h("div", { class: "row", style: { flexWrap: "wrap", gap: "12px" } },
        h("button", { class: "btn", type: "button", on: { click: rotate } }, icon("refresh-cw", { size: 14 }), "Make a new code"),
        h("label", { class: "row", style: { fontSize: "13px", gap: "6px" }, attrs: { for: "post-code" } }, post, "Post it in the meeting chat")),
      h("span", { class: "fine", text: "Everyone using the old code has to enter the new one." })));
  return box;
}

// ---------- export ----------
export async function exportMarkdown(ctx) {
  try {
    const r = await fetch(`${ctx.cfg.api}/export.md?tz=${new Date().getTimezoneOffset()}`, { credentials: "same-origin", headers: ctx.api.headers() });
    if (!r.ok) throw new Error("Export failed");
    const text = await r.text();
    const name = (r.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/);
    download(name ? name[1] : "minutes.md", text);
    toast("Minutes exported as Markdown", { kind: "success" });
  } catch (err) { toast(err.message, { kind: "error" }); }
}

/** Fill #print-minutes (shown only by print.css) and open the print dialog. */
export function printMinutes(ctx) {
  buildPrint(ctx);
  window.print();
}

export function buildPrint(ctx) {
  const root = document.getElementById("print-minutes");
  clear(root);
  const s = ctx.snap.session;
  const m = s.minutes || {};
  const lines = ctx.lineIndex;
  const attendees = ctx.people().filter((p) => p.decision === "yes").map((p) => displayName(p.name));
  const when = s.started_ms ? `${dateLong(s.started_ms)}, ${clock(s.started_ms)}${s.ended_ms ? ` – ${clock(s.ended_ms)}` : ""}` : "";
  const src = (it) => {
    const t = [...new Set((it.source_line_ids || []).map((id) => lines.get(id)).filter(Boolean).map((l) => mmss(l.start)))];
    return t.length ? h("span", { class: "pm-src", text: ` (at ${t.join(", ")})` }) : null;
  };
  const points = ctx.snap.summary.items.filter((i) => i.type !== "decision");
  const decisions = ctx.snap.summary.items.filter((i) => i.type === "decision");
  const sample = s.demo ? "Demo meeting: the people and the conversation are invented (LiveMinutes Demo mode)."
    : ctx.snap.summary.sample ? "Summary and action items are sample data (the summarizer isn't connected yet), not from this meeting." : null;
  append(root, [
    h("header", { class: "pm-head" },
      h("div", {}, h("div", { class: "pm-kicker", text: "Meeting minutes" }), h("h1", { class: "pm-title", text: s.title })),
      h("div", { class: "pm-brand" }, "LiveMinutes", h("br"), m.approved ? h("span", { class: "pm-approved", text: "Approved" }) : h("span", { class: "pm-draft", text: "Draft" }))),
    h("dl", { class: "pm-meta" },
      h("dt", { text: "Date" }), h("dd", { text: when || "—" }),
      h("dt", { text: "Platform" }), h("dd", { text: s.platform === "teams" ? "Microsoft Teams" : "Google Meet" }),
      h("dt", { text: "Attendees" }), h("dd", { text: attendees.join(", ") || "—" }),
      h("dt", { text: "Status" }), h("dd", { text: m.approved ? `Approved by ${displayName(m.approved.by)} on ${dateLong(m.approved.ms)} at ${clock(m.approved.ms)}${m.changed_since ? " (edited after approval)" : ""}` : "Draft, not approved yet" })),
    sample ? h("div", { class: "pm-note", text: sample }) : null,
    h("section", { class: "pm-section" }, h("h2", { text: "Summary" }),
      points.length ? h("ul", {}, points.map((i) => h("li", {}, i.text, src(i)))) : h("p", { text: "Nothing yet." })),
    h("section", { class: "pm-section" }, h("h2", { text: "Decisions" }),
      decisions.length ? h("ul", {}, decisions.map((i) => h("li", {}, i.text, src(i)))) : h("p", { text: "None recorded." })),
    h("section", { class: "pm-section" }, h("h2", { text: "Action items" }),
      ctx.snap.actions.items.length ? h("table", { class: "pm-table" },
        h("thead", {}, h("tr", {}, ["#", "Task", "Owner", "Due", "Status"].map((t) => h("th", { text: t })))),
        h("tbody", {}, ctx.snap.actions.items.map((a, n) => h("tr", {},
          h("td", { text: String(n + 1) }), h("td", { text: a.text }), h("td", { text: a.owner ? displayName(a.owner) : "Unassigned" }),
          h("td", { text: a.due ? dueLabel(a.due).text : "—" }), h("td", { text: { pending: "Pending", approved: "Approved", done: "Done" }[a.status] || a.status }))))) : h("p", { text: "None." })),
    h("section", { class: "pm-section" }, h("h2", { text: "Edit history" }),
      h("p", { text: ctx.snap.log.changes ? `${ctx.snap.log.changes} change${ctx.snap.log.changes === 1 ? "" : "s"} by ${ctx.snap.log.people.map(displayName).join(", ")}. Every change (who, when, before and after) is kept in the meeting's edit log.` : "No edits: these are the notes as generated." })),
    h("footer", { class: "pm-foot" }, h("span", { text: `Printed ${dateLong(Date.now())} at ${clock(Date.now())}` }), h("span", { text: "LiveMinutes" }))]);
}
