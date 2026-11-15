// Shared helpers and the little state every screen reads. No top-level DOM access, so Node can load it.
// Screens build their markup with el() (text goes in as text nodes), so nothing typed or imported reaches innerHTML;
// esc() is for the rare template literal that does.
import { get } from "./api.js";

// ---------------------------------------------------------------- escaping and formatting
// Hex escapes for the quote characters so tests/test_frontend.py can scan this file without mistaking a quote
// inside a regex for the start of a string.
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export function esc(s) {
  if (s === null || s === undefined) return "";
  return String(s).replace(/[&<>\x22\x27]/g, c => ESC[c]);
}

// Integer tenths of a millimetre -> "7.5 mm"; "" for anything that is not a number of tenths.
export function mm(tenths) {
  if (!Number.isInteger(tenths)) return "";
  const whole = Math.floor(tenths / 10);
  const tenth = tenths % 10;
  return `${whole}${tenth ? `.${tenth}` : ""} mm`;
}

// A count of days -> "1 day", "43 days", and from 60 days on whole months of 30.44 days ("14 months").
export function daysLabel(n) {
  if (!Number.isInteger(n)) return "";
  const d = Math.abs(n);
  const sign = n < 0 ? "-" : "";
  if (d >= 60) return `${sign}${Math.round(d / 30.44)} months`;
  return `${sign}${d} ${d === 1 ? "day" : "days"}`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DATE = /^(\d{4})-(\d{2})-(\d{2})$/;
// "2024-03-01" -> "1 Mar 2024"; "" for null or anything else.
export function dateLabel(iso) {
  const m = DATE.exec(iso ?? "");
  if (!m) return "";
  const month = Number(m[2]);
  if (month < 1 || month > 12) return "";
  return `${Number(m[3])} ${MONTHS[month - 1]} ${m[1]}`;
}

const pad = n => String(n).padStart(2, "0");
// Today in the browser's own time zone as YYYY-MM-DD (the server's `common.today()` is the server's).
export function todayIso(now = new Date()) {
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

// Epoch seconds -> "1 Mar 2024, 14:05" in the browser's time zone.
export function timeLabel(seconds) {
  if (!Number.isFinite(seconds)) return "";
  const d = new Date(seconds * 1000);
  return `${dateLabel(todayIso(d))}, ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const KINDS = { "application/pdf": "PDF", "image/png": "PNG", "image/jpeg": "JPEG", "application/xml": "XML", "text/xml": "XML", "text/html": "HTML" };
export const fileKind = mime => KINDS[mime] ?? "File";

export function fileSize(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

// "#/event/3?x=1" -> { parts: ["event", "3"], query: { x: "1" } }
export function parseHash(hash) {
  const [path, qs = ""] = (hash || "").replace(/^#\/?/, "").split("?");
  const query = {};
  for (const [k, v] of new URLSearchParams(qs)) query[k] = v;
  return { parts: path.split("/").filter(Boolean), query };
}

// ---------------------------------------------------------------- building markup
// el("button", { class: "btn", type: "button", on: { click: fn } }, "Save", childNode, null)
// Strings and numbers become text nodes; null, undefined and false are skipped; arrays are flattened.
// `style` and `on*` attributes are refused (the CSP forbids them): use classes and `on`.
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs ?? {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "on") {
      for (const [type, fn] of Object.entries(value)) node.addEventListener(type, fn);
    } else if (key === "style" || key.startsWith("on")) {
      throw new Error(`el: ${key} is not allowed`);
    } else if (key === "text") {
      node.textContent = value;
    } else if (value === true) {
      node.setAttribute(key, "");
    } else {
      node.setAttribute(key, String(value));
    }
  }
  const add = child => {
    if (Array.isArray(child)) child.forEach(add);
    else if (child === null || child === undefined || child === false) return;
    else node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  };
  children.forEach(add);
  return node;
}

// ---------------------------------------------------------------- state
// `me` is /api/me once main.js has loaded it; `lists` is /api/lists when a screen has asked for it.
export const state = { me: null, lists: null };

export async function loadMe() {
  const me = await get("/api/me");
  if (!me.error) state.me = me;
  return me;
}

export async function loadLists() {
  const lists = await get("/api/lists");
  if (!lists.error) state.lists = lists;
  return lists;
}

// An error message in a form a screen can put on the page.
export const errorNotice = message => el("div", { class: "notice error", role: "alert" }, message);
