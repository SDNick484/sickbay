// Import: upload a portal download (a ZIP, or one XML or PDF document), see what the run found, review what it
// suggests (proposed episodes, then suggestions with no date), and the inbox of documents on no event. Nothing is
// written to an event here except by a person's own action: a suggestion is accepted one at a time, on its event.
import { get, send, upload } from "./api.js";
import { dateLabel, el, errorNotice, fileKind, fileSize } from "./core.js";
import { eventChoice, renderSuggestions } from "./suggestions.js";

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

// The run's counts as lines of text: nothing is hidden (documents you deleted earlier and failed ones are named).
export function runSummary(r) {
  const lines = [`${plural(r.documents, "document", "documents")} in the download.`];
  const parts = [[r.new, "new"], [r.updated, "a newer version of one already held"], [r.same, "already in the app"],
    [r.skipped_deleted, "deleted earlier, so not brought back"], [r.failed, "could not be read"]];
  for (const [n, what] of parts) if (n) lines.push(`${n}: ${what}.`);
  lines.push(r.suggestions ? `${plural(r.suggestions, "new suggestion", "new suggestions")} to review below.` : "No new suggestions.");
  return lines;
}

// The counts of the run just made, shown once on the page that rerender() draws next.
let lastRun = null;

const runCard = r => el("div", { class: "card" }, el("h2", {}, "What was found"), el("ul", { class: "plain" }, runSummary(r).map(t => el("li", {}, t))));

export async function render({ view, alive, rerender }) {
  view.append(el("h2", { class: "page-title" }, "Import"));
  const result = el("div", { class: "import-result", role: "status" }, lastRun ? runCard(lastRun) : null);
  lastRun = null;
  view.append(uploadCard(result, rerender), result);
  const body = el("div", { class: "import-body" });
  view.append(body);

  const [pending, inbox, events] = await Promise.all([get("/api/suggestions"), get("/api/files/inbox"), get("/api/events")]);
  if (!alive()) return;
  const failed = [pending, inbox, events].find(r => r.error);
  if (failed) {
    body.append(errorNotice(failed.error));
    return;
  }
  body.append(waiting(pending.by_event, events), proposed(pending.proposed, rerender), undated(pending.undated, rerender),
    inboxCard(inbox, events, rerender));
}

function uploadCard(result, rerender) {
  const input = el("input", { id: "import-file", type: "file", accept: ".zip,.xml,.pdf,application/zip,application/pdf,text/xml,application/xml" });
  const go = el("button", { class: "btn primary", type: "submit" }, "Import");
  const form = el("form", { class: "card import-form" },
    el("h2", {}, "Import a download"),
    el("p", { class: "muted" }, "Upload the record download from the patient portal (a ZIP), or a single document (XML or PDF). The documents are kept in the inbox below; what they suggest changes nothing until you accept it."),
    el("div", { class: "field" }, el("label", { for: "import-file" }, "File"), input), go);
  form.addEventListener("submit", async e => {
    e.preventDefault();
    const file = input.files[0];
    if (!file) { result.replaceChildren(errorNotice("Choose a file first.")); return; }
    go.disabled = true;
    result.replaceChildren(el("p", { class: "muted" }, "Reading the download…"));
    const r = await upload("/api/imports", file);
    go.disabled = false;
    if (r.error) { result.replaceChildren(errorNotice(r.error)); return; }
    lastRun = r;
    rerender();
  });
  return form;
}

// Suggestions that already have an event: reviewed on that event's page, so only a count and a link here.
function waiting(byEvent, events) {
  const rows = Object.entries(byEvent).map(([id, list]) => {
    const ev = events.find(e => e.id === Number(id));
    return el("li", { class: "item" }, el("a", { href: `#/event/${id}` }, ev ? eventChoice(ev) : `Event ${id}`),
      el("span", { class: "muted" }, plural(list.length, "suggestion", "suggestions")));
  });
  if (!rows.length) return el("span");
  return el("section", { class: "card" }, el("h2", {}, "Waiting on an event"),
    el("p", { class: "muted" }, "These match an event you already have. Open it to accept or dismiss them one by one."),
    el("ul", { class: "plain" }, rows));
}

function proposed(groups, rerender) {
  if (!groups.length) return el("span");
  return el("section", {}, el("h2", {}, "Proposed episodes"),
    el("p", { class: "muted" }, "These fall outside every event you have. Creating an event starts it on the earliest date and accepts nothing; you then accept its suggestions one at a time."),
    groups.map(g => {
      const host = el("div", {});
      renderSuggestions(host, g.suggestions, { onChange: rerender, canAccept: false });
      const message = el("div", { class: "form-error", role: "alert" });
      const create = el("button", { class: "btn primary", type: "button" }, "Create this event");
      create.addEventListener("click", async () => {
        create.disabled = true;
        const r = await send("POST", "/api/suggestions/new-event", { suggestion_ids: g.suggestion_ids });
        if (r.error) { message.textContent = r.error; create.disabled = false; return; }
        location.hash = `#/event/${r.id}`;
      });
      const span = g.end && g.end !== g.start ? `${dateLabel(g.start)} to ${dateLabel(g.end)}` : dateLabel(g.start);
      return el("div", { class: "card proposed" }, el("h3", {}, span), host, message, el("div", { class: "actions" }, create));
    }));
}

function undated(list, rerender) {
  if (!list.length) return el("span");
  const host = el("div", {});
  renderSuggestions(host, list, { onChange: rerender, canAccept: false });
  return el("section", { class: "card" }, el("h2", {}, "Suggestions with no date"),
    el("p", { class: "muted" }, "The document gave no date. Move one to an event to accept it there, or dismiss it."), host);
}

// ---------------------------------------------------------------- the inbox
function inboxCard(files, events, rerender) {
  const rows = files.map(f => inboxRow(f, events, rerender));
  return el("section", { class: "card" }, el("h2", {}, "Inbox"),
    el("p", { class: "muted" }, "Documents on no event. Attach one to an event, or delete it."),
    rows.length ? el("ul", { class: "plain" }, rows) : el("p", { class: "muted" }, "The inbox is empty."));
}

function inboxRow(f, events, rerender) {
  const message = el("div", { class: "form-error", role: "alert" });
  const tail = el("div", { class: "inbox-tail" });
  const pick = el("select", { "aria-label": `Event to attach ${f.name} to` }, events.map(e => el("option", { value: e.id }, eventChoice(e))));
  const attach = el("button", { class: "btn primary", type: "button" }, "Attach");
  attach.disabled = !events.length;
  attach.addEventListener("click", async () => {
    attach.disabled = true;
    const r = await send("PUT", `/api/events/${pick.value}/files/${f.id}`);
    if (r.error) { message.textContent = r.error; attach.disabled = false; return; }
    rerender();
  });
  const ask = () => tail.replaceChildren(el("div", { class: "confirm" },
    el("p", {}, "Delete this document? It will not come back on a later import."),
    el("button", { class: "btn danger", type: "button", on: { click: async () => {
      const r = await send("DELETE", `/api/files/${f.id}`);
      if (r.error) message.textContent = r.error; else rerender();
    } } }, "Yes, delete it"),
    el("button", { class: "btn", type: "button", on: { click: () => tail.replaceChildren() } }, "No")));
  return el("li", { class: "item sugg" },
    el("div", { class: "item-text" },
      el("a", { class: "file-link", href: `/api/files/${f.id}/content`, target: "_blank", rel: "noopener" }, f.name),
      el("small", { class: "muted" }, [fileKind(f.mime), fileSize(f.size)].filter(Boolean).join(" · "))),
    el("div", { class: "actions small" }, events.length ? [pick, attach] : null,
      el("button", { class: "btn danger", type: "button", on: { click: ask } }, "Delete")),
    tail, message);
}
