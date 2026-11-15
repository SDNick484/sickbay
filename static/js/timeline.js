// The timeline: the headline figures from GET /api/timeline, then every event newest first with the gap between each
// pair of starts, and a marker on an episode that had a complication. It only reports what happened (counts, a
// median, spans); nothing here looks forward. `eventLink` is also what the home screen's "Open now" list uses.
import { get } from "./api.js";
import { dateLabel, daysLabel, el, errorNotice } from "./core.js";

export const eventLink = ev =>
  el("a", { class: "event-link", href: `#/event/${ev.id}` },
    el("span", { class: "event-date" }, dateLabel(ev.started_on)),
    el("span", { class: ev.open ? "pill open" : "pill done" }, ev.open ? "Open" : `Resolved ${dateLabel(ev.resolved_on)}`));

// A gap in days as text: "—" when there is none (fewer than two events, or the oldest event's "before").
export const gapText = days => (Number.isInteger(days) ? daysLabel(days) : "—");

export const nightsText = n => (Number.isInteger(n) && n > 0 ? `${n} ${n === 1 ? "night" : "nights"}` : "");
const cap = s => (s ? s[0].toUpperCase() + s.slice(1) : s);

// "7 mm" / "7 mm, left" / "left" / "" from a figures row (sizes_mm are text like "7.5"; sides leave out unknown).
export function sizeSide(ev) {
  const size = ev.sizes_mm.map(s => `${s} mm`).join(" + ");
  return [size, ev.sides.join(" + ")].filter(Boolean).join(", ");
}

// "ER, 2 nights" / "2 nights" / "ER" / "" from a figures row.
export const hospitalText = ev => [ev.visit_kinds.join(", "), nightsText(ev.nights)].filter(Boolean).join(", ");

// The figures a headline holds, as {value, label, detail} for the stat blocks.
export function headlineStats(h) {
  const shortLong = Number.isInteger(h.shortest_gap_days) && Number.isInteger(h.longest_gap_days)
    ? `shortest ${gapText(h.shortest_gap_days)}, longest ${gapText(h.longest_gap_days)}` : "";
  return [
    { value: String(h.episodes), label: h.episodes === 1 ? "episode" : "episodes" },
    { value: gapText(h.typical_gap_days), label: "typical gap (median)", detail: shortLong },
    { value: Number.isInteger(h.since_last_days) ? daysLabel(h.since_last_days) : "—", label: "since the last one started" },
    { value: String(h.hospital_nights), label: "nights in hospital, total" },
  ];
}

const stat = s => el("div", { class: "stat" },
  el("span", { class: "n" }, s.value), el("span", { class: "l" }, s.label), s.detail ? el("span", { class: "d muted" }, s.detail) : null);

export const headlineBlock = h => el("div", { class: "stats" }, headlineStats(h).map(stat));

function eventRow(ev) {
  const hurt = ev.complications.length > 0;
  const facts = [sizeSide(ev), hospitalText(ev), ev.procedures.join(", "), ev.types.join(", ")].filter(Boolean);
  return el("li", { class: "tl-event" },
    el("span", { class: hurt ? "dot h" : "dot", "aria-hidden": "true" }),
    el("a", { class: "tl-link", href: `#/event/${ev.id}` },
      el("span", { class: "tl-main" },
        el("span", { class: "event-date" }, dateLabel(ev.started_on)),
        el("span", { class: ev.open ? "pill open" : "pill done" }, ev.open ? "Open" : `Resolved ${dateLabel(ev.resolved_on)}`),
        hurt ? el("span", { class: "pill warn" }, `Complication: ${ev.complications.map(cap).join(", ")}`) : null),
      facts.length ? el("span", { class: "tl-facts muted" }, facts.join(" · ")) : null));
}

const gapRow = ev => el("li", { class: "tl-gap muted" }, `${gapText(ev.gap_days_before)} between starts`);

// Events are newest first; each row's `gap_days_before` is the gap to the next (older) one down the page.
export function eventList(events) {
  const items = [];
  events.forEach((ev, i) => {
    items.push(eventRow(ev));
    if (i < events.length - 1) items.push(gapRow(ev));
  });
  return el("ol", { class: "plain tl" }, items);
}

// Fetches /api/timeline and draws it into `host`. A second argument (the home screen passes its event list) is not
// needed any more: the timeline has its own route.
export async function renderTimeline(host) {
  const data = await get("/api/timeline");
  if (data.error) {
    host.replaceChildren(errorNotice(data.error));
    return;
  }
  host.replaceChildren(
    el("h2", {}, "Timeline"),
    ...(data.events.length
      ? [headlineBlock(data.headline), eventList(data.events)]
      : [el("p", { class: "muted" }, "No events yet.")]));
}
