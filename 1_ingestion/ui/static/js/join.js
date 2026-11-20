// Participant entry: the access code (6 boxes, paste or QR auto-fill), then pick your name,
// plus the screens for expired / deleted / not-yet-shared meetings.

import { h, clear, displayName, setPeopleOrder } from "./util.js";
import { icon } from "./icons.js";
import { avatar } from "./ui.js";

const ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789";
const NEVER = "0O1IL"; // look-alike characters that never appear in codes

function brand() {
  return h("div", { class: "card-brand" }, h("img", { class: "logo", src: "/static/img/logo.svg", alt: "" }), h("span", { text: "LiveMinutes" }));
}

function screen(card) {
  const root = document.getElementById("root");
  clear(root);
  root.removeAttribute("aria-busy");
  root.appendChild(h("main", { class: "center-screen", id: "main" }, card));
  return card;
}

/** The code step. onSubmit(code) -> Promise that throws {message, retryAfter} to show an error. */
export function codeScreen({ onSubmit, prefill = "" }) {
  const boxes = [];
  const msg = h("p", { class: "form-msg", attrs: { role: "status", "aria-live": "polite" } });
  const otp = h("div", { class: "otp", attrs: { role: "group", "aria-label": "Access code, 6 characters" } });
  const submit = h("button", { class: "btn btn--primary btn--lg btn--block", type: "submit" }, "Continue", icon("arrow-up-right", { size: 16 }));
  for (let i = 0; i < 6; i++) {
    if (i === 3) otp.appendChild(h("span", { class: "otp-sep", attrs: { "aria-hidden": "true" }, text: "–" }));
    const input = h("input", {
      type: "text", inputMode: "text", maxLength: 1,
      attrs: { "aria-label": `Character ${i + 1} of 6`, autocomplete: i === 0 ? "one-time-code" : "off", autocapitalize: "characters", spellcheck: "false" },
    });
    boxes.push(input);
    otp.appendChild(input);
  }
  const value = () => boxes.map((b) => b.value).join("");
  const fill = (text, from = 0) => {
    const chars = String(text).toUpperCase().replace(/[^A-Z0-9]/g, "").split("");
    let i = from;
    for (const ch of chars) { if (i >= 6) break; boxes[i].value = ch; i++; }
    boxes[Math.min(i, 5)].focus();
    if (value().length === 6) form.requestSubmit();
  };
  boxes.forEach((box, i) => {
    box.addEventListener("input", () => {
      otp.classList.remove("is-error");
      const v = box.value.toUpperCase().replace(/[^A-Z0-9]/g, "");
      if (v.length > 1) { fill(v, i); return; }
      box.value = v;
      msg.classList.remove("is-error");
      msg.textContent = v && NEVER.includes(v) ? `Codes never contain ${v}. Check the code in the meeting chat.` : "";
      if (v && i < 5) boxes[i + 1].focus();
      if (value().length === 6) form.requestSubmit();
    });
    box.addEventListener("keydown", (e) => {
      if (e.key === "Backspace" && !box.value && i > 0) { boxes[i - 1].focus(); boxes[i - 1].value = ""; e.preventDefault(); }
      if (e.key === "ArrowLeft" && i > 0) { boxes[i - 1].focus(); e.preventDefault(); }
      if (e.key === "ArrowRight" && i < 5) { boxes[i + 1].focus(); e.preventDefault(); }
    });
    box.addEventListener("paste", (e) => { e.preventDefault(); fill((e.clipboardData || window.clipboardData).getData("text"), i); });
    box.addEventListener("focus", () => box.select());
  });
  let busy = false;
  const form = h("form", {
    on: {
      submit: async (e) => {
        e.preventDefault();
        if (busy) return;
        const code = value();
        if (code.length !== 6) { msg.textContent = "The code has 6 characters."; msg.classList.add("is-error"); boxes[code.length]?.focus(); return; }
        busy = true;
        submit.disabled = true;
        msg.classList.remove("is-error");
        msg.textContent = "Checking…";
        try {
          await onSubmit(code);
        } catch (err) {
          busy = false;
          submit.disabled = false;
          msg.textContent = err.message || "That didn't work.";
          msg.classList.add("is-error");
          otp.classList.add("is-error", "is-shake");
          setTimeout(() => otp.classList.remove("is-shake"), 400);
          boxes.forEach((b) => { b.value = ""; });
          boxes[0].focus();
        }
      },
    },
  }, otp, msg, submit);
  const card = h("section", { class: "card", attrs: { "aria-labelledby": "join-title" } },
    brand(),
    h("h1", { id: "join-title", text: "Join the live notes" }),
    h("p", { class: "lead", text: "Enter the 6-character code the LiveMinutes bot posted in the meeting chat." }),
    form,
    h("p", { class: "fine", style: { marginTop: "16px" } }, "Codes look like ", h("span", { class: "code-inline", text: "K7Q-M4X" }), ". They have no 0, O, 1, I or L."));
  screen(card);
  boxes[0].focus();
  if (prefill && prefill.length >= 6) fill(prefill);
  return card;
}

/** The name step. onPick(name|null): null = continue read-only. */
export function nameScreen({ people, onPick }) {
  setPeopleOrder(people);
  const list = h("div", { class: "names", attrs: { role: "list" } });
  people.forEach((name) => list.appendChild(h("button", {
    class: "name-option", type: "button", attrs: { role: "listitem" },
    on: { click: async (e) => { e.currentTarget.disabled = true; await onPick(name); } },
  }, avatar(name, { size: "lg" }), h("span", { text: displayName(name) }), icon("chevron-right", { size: 16 }))));
  if (!people.length) list.appendChild(h("p", { class: "muted", text: "Nobody has agreed to recording yet." }));
  const card = h("section", { class: "card", attrs: { "aria-labelledby": "name-title" } },
    brand(),
    h("h1", { id: "name-title", text: "Who are you?" }),
    h("p", { class: "lead", text: "Pick your name. Your edits to the summary and action items are recorded under it." }),
    list,
    h("div", { class: "callout", style: { marginBottom: "14px" } }, icon("info", { size: 16 }),
      h("span", {}, h("strong", { text: "This is name selection, not a login. " }), "Anyone with the code can pick any name, so please pick your own.")),
    h("p", { class: "fine" }, "Just looking? ", h("button", { class: "link-btn", type: "button", text: "Continue without a name", on: { click: () => onPick(null) } }), " (view only)."));
  screen(card);
  list.querySelector("button")?.focus();
  return card;
}

const ERRORS = {
  expired: { icon: "hourglass", kind: "warning", title: "These live notes have expired", text: "The access code stops working some days after the meeting ends. Ask the organizer for the minutes." },
  declined: { icon: "ban", kind: "danger", title: "Recording was stopped", text: "Someone in the meeting said no, so LiveMinutes left and deleted everything it had. Nothing is kept." },
  not_shared: { icon: "lock", kind: "", title: "Not shared yet", text: "The link and code are posted in the meeting chat once everyone has agreed to recording. Try again in a moment." },
  not_found: { icon: "alert-circle", kind: "", title: "Meeting not found", text: "This link doesn't match a meeting. Check you copied the whole link from the chat." },
  offline: { icon: "wifi-off", kind: "warning", title: "Can't reach LiveMinutes", text: "Check your connection. If the organizer's computer is off or the tunnel stopped, the page can't load." },
};

export function errorScreen(state, message, { retry = null } = {}) {
  const e = ERRORS[state] || { icon: "alert-circle", kind: "", title: "Something went wrong", text: message || "Please reload the page." };
  const card = h("section", { class: "card", attrs: { "aria-labelledby": "err-title", role: "alert" } },
    brand(),
    h("div", { class: `empty-art ${e.kind ? "empty-art--" + e.kind : ""}`, style: { marginBottom: "18px" } }, icon(e.icon, { size: 22 })),
    h("h1", { id: "err-title", text: e.title }),
    h("p", { class: "lead", text: e.text }),
    retry ? h("button", { class: "btn btn--lg btn--block", type: "button", style: { marginTop: "22px" }, on: { click: retry } }, icon("refresh-cw", { size: 15 }), "Try again") : null);
  screen(card);
  return card;
}

export const CODE_ALPHABET = ALPHABET;
