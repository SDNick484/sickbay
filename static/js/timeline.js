// The list of every event under the home screen. Task 11 replaces this with the real timeline (headline figures,
// gaps); until then it is a plain list of GET /api/events, open ones first as the server orders them.
import { get } from "./api.js";
import { dateLabel, el, errorNotice } from "./core.js";

export const eventLink = ev =>
  el("a", { class: "event-link", href: `#/event/${ev.id}` },
    el("span", { class: "event-date" }, dateLabel(ev.started_on)),
    el("span", { class: ev.open ? "pill open" : "pill done" }, ev.open ? "Open" : `Resolved ${dateLabel(ev.resolved_on)}`));

// `events` is the list the caller already has (the home screen), or omitted to fetch it.
export async function renderTimeline(host, events) {
  events = events ?? await get("/api/events");
  if (events.error) {
    host.replaceChildren(errorNotice(events.error));
    return;
  }
  host.replaceChildren(
    el("h2", {}, "Timeline"),
    events.length ? el("ul", { class: "plain" }, events.map(ev => el("li", {}, eventLink(ev)))) : el("p", { class: "muted" }, "No events yet."));
}
