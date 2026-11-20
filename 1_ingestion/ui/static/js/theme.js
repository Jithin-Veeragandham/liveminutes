// Light / dark / system theme. The page's <head> sets data-theme before anything paints (no flash);
// this module changes it later and remembers the choice in localStorage.

const KEY = "lm-theme";
const media = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
const listeners = new Set();

export function choice() {
  try { return localStorage.getItem(KEY) || "system"; } catch { return "system"; }
}

export function current() {
  return document.documentElement.getAttribute("data-theme") || "light";
}

function apply(c) {
  const dark = c === "dark" || (c === "system" && media && media.matches);
  const root = document.documentElement;
  root.setAttribute("data-theme", dark ? "dark" : "light");
  root.setAttribute("data-theme-choice", c);
  listeners.forEach((fn) => fn(c));
}

export function set(c) {
  try { localStorage.setItem(KEY, c); } catch { /* private mode: still switch for this page */ }
  apply(c);
}

/** Light -> dark -> system -> light. */
export function cycle() {
  const order = ["light", "dark", "system"];
  set(order[(order.indexOf(choice()) + 1) % order.length]);
}

export function toggle() {
  set(current() === "dark" ? "light" : "dark");
}

export function onChange(fn) { listeners.add(fn); return () => listeners.delete(fn); }

if (media) media.addEventListener("change", () => { if (choice() === "system") apply("system"); });
