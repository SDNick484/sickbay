// Suggestions from an import, drawn the same way on the import screen and the event page. A suggestion changes
// nothing until a person accepts it, one at a time. Every label and file name here comes from an imported document,
// so it only ever goes in as a text node (el() does that); nothing here is ever parsed as markup.
import { get, send } from "./api.js";
import { dateLabel, el } from "./core.js";

const PREFIX = { visit: "", procedure: "Procedure: ", complication: "Complication: ", stone_type: "Stone type: ", doctor: "Doctor: " };

// One line for a suggestion: "ER visit, 1 Mar 2024", "Procedure: Ureteroscopy, 2 Mar 2024", with "to <date>" for a
// range and "no date" when the document gave none.
export function suggestionText(s) {
  const when = dateLabel(s.on_date);
  let date = "no date";
  if (when) {
    const end = s.end_date && s.end_date !== s.on_date ? dateLabel(s.end_date) : "";
    date = end ? `${when} to ${end}` : when;
  }
  return `${PREFIX[s.kind] ?? ""}${s.label ?? ""}, ${date}`;
}

// "1 Mar 2024" or "1 Mar 2024 (open)": how an event is named in a list a person picks from.
export const eventChoice = e => `${dateLabel(e.started_on) || `Event ${e.id}`}${e.open ? " (open)" : ""}`;

const NEW_EVENT = "new";

// Draw `list` (suggestion objects) into `host`. Options: `onChange(result?)` runs after something was written or
// moved (the screen reloads its data; `result` is the server's answer to an accept, which carries the event);
// `canAccept` (default true) is false for suggestions that have no event to go to yet.
// A suggestion with no target can only be moved (to an event, or split off into a new one) or dismissed.
export function renderSuggestions(host, list, { onChange = () => {}, canAccept = true } = {}) {
  host.replaceChildren(el("ul", { class: "plain sugg-list" }, list.map(s => row(s, { onChange, canAccept }))));
}

function row(s, { onChange, canAccept }) {
  const message = el("div", { class: "form-error", role: "alert" });
  const mover = el("div", { class: "sugg-move" });
  const fail = r => { message.textContent = r.error; return false; };

  // Run a request; on success tell the screen, otherwise show why beside this row (a 409 "Already decided." too).
  const run = (call, passResult = false) => async () => {
    message.textContent = "";
    const r = await call();
    if (r.error) return fail(r);
    onChange(passResult ? r : undefined);
    return true;
  };
  const btn = (label, fn, cls = "btn") => {
    const b = el("button", { class: cls, type: "button" }, label);
    b.addEventListener("click", async () => {
      b.disabled = true;
      const ok = await fn();
      if (ok === false) b.disabled = false;
    });
    return b;
  };

  async function openMove() {
    if (mover.childNodes.length) { mover.replaceChildren(); return; }
    const events = await get("/api/events");
    if (events.error) { message.textContent = events.error; return; }
    const pick = el("select", { "aria-label": "Move to" },
      events.filter(e => e.id !== s.target_event_id).map(e => el("option", { value: e.id }, eventChoice(e))),
      el("option", { value: NEW_EVENT }, "Split off into a new event"));
    const go = btn("Move", () => (pick.value === NEW_EVENT
      ? run(() => send("POST", "/api/suggestions/new-event", { suggestion_ids: [s.id] }))()
      : run(() => send("PATCH", `/api/suggestions/${s.id}`, { event_id: Number(pick.value) }))()), "btn primary");
    mover.replaceChildren(pick, go, el("button", { class: "btn", type: "button", on: { click: () => mover.replaceChildren() } }, "Cancel"));
    pick.focus();
  }

  const actions = el("div", { class: "actions small" },
    canAccept && s.target_event_id !== null ? btn("Accept", run(() => send("POST", `/api/suggestions/${s.id}/accept`, {}), true), "btn primary") : null,
    btn("Dismiss", run(() => send("POST", `/api/suggestions/${s.id}/dismiss`, {}))),
    el("button", { class: "btn", type: "button", on: { click: openMove } }, "Move"),
    s.moved ? btn("Send back", run(() => send("PATCH", `/api/suggestions/${s.id}`, { event_id: null }))) : null);

  return el("li", { class: "item sugg" },
    el("div", { class: "item-text" },
      el("span", {}, suggestionText(s), s.kind === "doctor" && s.doctor_id ? el("small", { class: "muted" }, " (already in the doctor list)") : null),
      s.file ? el("small", { class: "muted" }, "From ", el("a", { href: `/api/files/${s.file.id}/content`, target: "_blank", rel: "noopener" }, s.file.name)) : null),
    actions, mover, message);
}
