// The doctor summary: a print-styled page with the headline line, a table of every event, the doctors seen, and a
// footer line. The page title is never touched (it stays APP_NAME); print.css hides the navigation and branding.
import { get } from "./api.js";
import { dateLabel, el, errorNotice, todayIso } from "./core.js";
import { gapText, hospitalText, nightsText, sizeSide } from "./timeline.js";

const DASH = "—";
const or = text => text || DASH;

// One table row, as the seven cells in the table's column order: started, resolved, size and side, type, hospital,
// procedures, complications.
export const summaryRow = ev => [
  dateLabel(ev.started_on),
  ev.open ? "open" : dateLabel(ev.resolved_on),
  or(sizeSide(ev)),
  or(ev.types.join(", ")),
  or(hospitalText(ev)),
  or(ev.procedures.join(", ")),
  or(ev.complications.join(", ")),
];

export const COLUMNS = ["Started", "Resolved", "Size / side", "Type", "Hospital", "Procedures", "Complications"];

// "6 episodes from 3 Jan 2003 · typical gap 14 months · 5 hospital nights · 3 procedures"; counts only.
export function summaryLine(h, events) {
  if (!h.episodes) return "No episodes recorded.";
  const procedures = events.reduce((n, ev) => n + ev.procedures.length, 0);
  const parts = [`${h.episodes} ${h.episodes === 1 ? "episode" : "episodes"} from ${dateLabel(h.first_started_on)}`];
  if (Number.isInteger(h.typical_gap_days)) parts.push(`typical gap ${gapText(h.typical_gap_days)}`);
  parts.push(nightsText(h.hospital_nights) ? `${nightsText(h.hospital_nights)} in hospital` : "no hospital nights");
  parts.push(`${procedures} ${procedures === 1 ? "procedure" : "procedures"}`);
  return parts.join(" · ");
}

export const doctorText = d => (d.specialty ? `${d.name} (${d.specialty})` : d.name);

export async function render({ view, alive }) {
  const data = await get("/api/summary");
  if (!alive()) return;
  if (data.error) {
    view.append(errorNotice(data.error));
    return;
  }
  view.append(el("article", { class: "summary card" },
    el("div", { class: "summary-head" },
      el("div", {}, el("h2", {}, "Stone history summary"), el("p", { class: "muted" }, `Printed ${dateLabel(todayIso())}`)),
      el("button", { class: "btn primary no-print", type: "button", on: { click: () => window.print() } }, "Print or save as PDF")),
    el("p", { class: "summary-line" }, summaryLine(data.headline, data.events)),
    data.events.length ? el("table", { class: "sum" },
      el("thead", {}, el("tr", {}, COLUMNS.map(c => el("th", { scope: "col" }, c)))),
      el("tbody", {}, data.events.map(ev => el("tr", {}, summaryRow(ev).map(c => el("td", {}, c)))))) : null,
    el("p", { class: "summary-doctors" }, "Doctors seen: ", data.doctors.length ? data.doctors.map(doctorText).join(", ") : DASH),
    el("p", { class: "summary-foot muted" }, "A record of history, not an interpretation.")));
}
