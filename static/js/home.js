// Home: open events first, a one-step "New event" (a date defaulting to today and an optional note), then the
// timeline.
import { get, send } from "./api.js";
import { el, errorNotice, todayIso } from "./core.js";
import { eventLink, renderTimeline } from "./timeline.js";

function newEventForm(view) {
  const date = el("input", { type: "date", id: "new-date", value: todayIso(), required: true });
  const note = el("textarea", { id: "new-note", rows: 2, maxlength: 2000, placeholder: "Optional note" });
  const message = el("div", { class: "form-error", role: "alert" });
  const save = el("button", { class: "btn primary", type: "submit" }, "Save");
  const form = el("form", { class: "card new-event", hidden: true },
    el("label", { for: "new-date" }, "Started"), date,
    el("label", { for: "new-note" }, "Note"), note,
    message,
    el("div", { class: "actions" }, save, el("button", { class: "btn", type: "button", on: { click: () => close() } }, "Cancel")),
  );
  const open = el("button", { class: "btn primary big", type: "button", on: { click: () => { form.hidden = false; open.hidden = true; date.focus(); } } }, "New event");
  function close() {
    form.hidden = true;
    open.hidden = false;
    message.textContent = "";
  }
  form.addEventListener("submit", async e => {
    e.preventDefault();
    save.disabled = true;
    const body = { started_on: date.value };
    if (note.value.trim()) body.note = note.value;
    const created = await send("POST", "/api/events", body);
    save.disabled = false;
    if (created.error) {
      message.textContent = created.error;
      return;
    }
    location.hash = `#/event/${created.id}`;
  });
  view.append(open, form);
}

export async function render({ view, alive }) {
  const open = el("section", { class: "open-events" });
  const timeline = el("section", { class: "timeline" });
  view.append(open);
  newEventForm(view);
  view.append(timeline);
  const events = await get("/api/events");
  if (!alive()) return;
  if (events.error) {
    open.replaceChildren(errorNotice(events.error));
  } else {
    const current = events.filter(ev => ev.open);
    open.replaceChildren(
      el("h2", {}, "Open now"),
      current.length ? el("ul", { class: "plain" }, current.map(ev => el("li", {}, eventLink(ev)))) : el("p", { class: "muted" }, "Nothing open."));
  }
  await renderTimeline(timeline, events.error ? undefined : events);
}
