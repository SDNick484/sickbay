// The event page: everything about one episode on one page. Each row edits in place; one form is open at a time
// (`st.open` names it). Every mutation answers with the whole event (or 204), so the page redraws from one state.
// Markup is built with el(): record text goes in as text nodes and never reaches innerHTML.
import { get, send, upload } from "./api.js";
import { dateLabel, daysLabel, el, errorNotice, fileKind, fileSize, mm, timeLabel, todayIso } from "./core.js";

const SIDES = [{ value: "unknown", label: "Not known" }, { value: "left", label: "Left" }, { value: "right", label: "Right" }, { value: "both", label: "Both" }];
const cap = s => (s ? s[0].toUpperCase() + s.slice(1) : s);
const joined = parts => parts.filter(Boolean).join(" · ");
const nightsText = n => (Number.isInteger(n) ? `${n} ${n === 1 ? "night" : "nights"}` : "");
const range = (from, to) => (from ? `${dateLabel(from)}${to ? ` to ${dateLabel(to)}` : ""}` : "");

// ---------------------------------------------------------------- what each part of the record says and edits
// describe(row) -> one line of text. fields(row, ctx) -> the inputs of its form; `row` is null when adding.
const PARTS = [
  {
    kind: "stones", title: "Stones", add: "Add a stone",
    describe: s => joined([mm(s.size_tenths) || "Size not known", s.side !== "unknown" && cap(s.side), s.location?.label,
      s.type ? `${s.type.label}${s.type_from_lab ? " (lab analysis)" : ""}` : "Type not known yet"]),
    fields: (s, c) => [
      { key: "size_mm", label: "Size in mm (like 7 or 7.5)", type: "text", inputmode: "decimal", value: s?.size_mm },
      { key: "side", label: "Side", type: "select", options: SIDES, blank: false, value: s?.side ?? "unknown" },
      { key: "location_id", label: "Location", type: "select", options: c.list("location", s?.location?.id), value: s?.location?.id },
      { key: "type_id", label: "Type", type: "select", options: c.list("stone_type", s?.type?.id), value: s?.type?.id },
      { key: "type_from_lab", label: "The type came from a lab analysis", type: "check", value: s?.type_from_lab },
    ],
  },
  {
    kind: "visits", title: "Hospital and care visits", add: "Add a visit",
    describe: v => joined([v.kind.label, range(v.from_date, v.to_date), nightsText(v.nights)]),
    fields: (v, c) => [
      { key: "kind_id", label: "Kind of visit", type: "select", options: c.list("visit_kind", v?.kind?.id), value: v?.kind?.id },
      { key: "from_date", label: "From", type: "date", value: v?.from_date },
      { key: "to_date", label: "To", type: "date", value: v?.to_date },
    ],
  },
  {
    kind: "procedures", title: "Procedures", add: "Add a procedure",
    describe: p => joined([p.kind.label, dateLabel(p.on_date), p.doctor?.name, p.note]),
    fields: (p, c) => [
      { key: "kind_id", label: "Procedure", type: "select", options: c.list("procedure_kind", p?.kind?.id), value: p?.kind?.id },
      { key: "on_date", label: "Date", type: "date", value: p?.on_date },
      { key: "doctor_id", label: "Doctor", type: "select", options: c.doctors(p?.doctor?.id), value: p?.doctor?.id },
      { key: "note", label: "Note", type: "text", value: p?.note },
    ],
  },
  {
    kind: "complications", title: "Complications and infections", add: "Add a complication",
    describe: x => joined([x.kind.label, dateLabel(x.on_date), x.note]),
    fields: (x, c) => [
      { key: "kind_id", label: "Kind", type: "select", options: c.list("complication_kind", x?.kind?.id), value: x?.kind?.id },
      { key: "on_date", label: "Date", type: "date", value: x?.on_date },
      { key: "note", label: "Note", type: "text", value: x?.note },
    ],
  },
  {
    kind: "followups", title: "Follow-up", add: "Add a follow-up",
    describe: f => joined([f.what, dateLabel(f.on_date), f.doctor?.name, f.done ? "Done" : null]),
    fields: (f, c) => [
      { key: "what", label: "What (like a scan or a stent removal)", type: "text", value: f?.what },
      { key: "on_date", label: "Date", type: "date", value: f?.on_date },
      { key: "doctor_id", label: "Doctor", type: "select", options: c.doctors(f?.doctor?.id), value: f?.doctor?.id },
      { key: "done", label: "Done", type: "check", value: f?.done },
    ],
  },
];

// ---------------------------------------------------------------- forms
let uid = 0;

// A form of the given fields; `onSubmit(values)` resolves to an error message, or null when it worked (the caller
// then redraws, so the form is gone).
export function form(fields, { submit = "Save", onSubmit, onCancel }) {
  const controls = fields.map(f => {
    const id = `f${++uid}`;
    let input;
    if (f.type === "select") {
      const opts = f.options.map(o => el("option", { value: o.value, selected: String(o.value) === String(f.value ?? "") }, o.label));
      input = el("select", { id }, f.blank === false ? null : el("option", { value: "" }, "Not set"), opts);
    } else if (f.type === "check") {
      input = el("input", { id, type: "checkbox", checked: Boolean(f.value) });
    } else if (f.type === "textarea") {
      input = el("textarea", { id, rows: 3, maxlength: 4000 });
      input.value = f.value ?? "";
    } else {
      input = el("input", { id, type: f.type, value: f.value ?? "", inputmode: f.inputmode, maxlength: f.type === "text" ? 500 : null });
    }
    const row = f.type === "check" ? el("div", { class: "field check" }, input, el("label", { for: id }, f.label))
      : el("div", { class: "field" }, el("label", { for: id }, f.label), input);
    return { f, input, row };
  });
  const message = el("div", { class: "form-error", role: "alert" });
  const save = el("button", { class: "btn primary", type: "submit" }, submit);
  const node = el("form", { class: "edit" }, controls.map(c => c.row), message,
    el("div", { class: "actions" }, save, el("button", { class: "btn", type: "button", on: { click: onCancel } }, "Cancel")));
  node.addEventListener("submit", async e => {
    e.preventDefault();
    const values = {};
    for (const { f, input } of controls) values[f.key] = valueOf(f, input);
    save.disabled = true;
    const error = await onSubmit(values);
    save.disabled = false;
    if (error) message.textContent = error;
  });
  return node;
}

// A control's value as the API takes it: blank is null, list and doctor ids are numbers.
export function valueOf(field, input) {
  if (field.type === "check") return input.checked;
  const text = input.value.trim();
  if (field.type === "select") return text === "" ? null : (/^\d+$/.test(text) ? Number(text) : text);
  return text === "" ? null : text;
}

// ---------------------------------------------------------------- the page
export async function render({ view, parts, alive }) {
  const id = Number(parts[1]);
  if (!Number.isInteger(id) || id < 1) {
    view.append(errorNotice("There is no such event."));
    return;
  }
  const [ev, lists, doctors, files] = await Promise.all([
    get(`/api/events/${id}`), get("/api/lists"), get("/api/doctors"), get(`/api/events/${id}/files`)]);
  if (!alive()) return;
  const failed = [ev, lists, doctors, files].find(r => r.error);
  if (failed) {
    view.append(errorNotice(failed.error), el("p", {}, el("a", { href: "#/" }, "Home")));
    return;
  }
  const st = { id, ev, lists, doctors, files, open: null, noteDraft: "", notice: "" };
  const ctx = {
    list: (kind, current) => lists[kind].filter(i => !i.retired || i.id === current).map(i => ({ value: i.id, label: i.label })),
    doctors: current => st.doctors.filter(d => !d.retired || d.id === current).map(d => ({ value: d.id, label: d.name })),
  };

  const setOpen = key => { st.open = key; draw(); };
  const toggle = key => () => setOpen(st.open === key ? null : key);
  const close = () => setOpen(null);

  // Run a request; on success take the event it answers with (or reload after a 204) and redraw. Resolves to an
  // error message for the form that asked, or null.
  async function act(call) {
    const r = await call;
    if (r.error) return r.error;
    st.open = null;
    st.notice = "";
    if (r.notes) st.ev = r;
    else await reload();
    draw();
    return null;
  }
  async function reload() {
    const [e, f] = await Promise.all([get(`/api/events/${id}`), get(`/api/events/${id}/files`)]);
    if (!e.error) st.ev = e;
    if (!f.error) st.files = f;
  }

  const button = (label, onClick, cls = "btn") => el("button", { class: cls, type: "button", on: { click: onClick } }, label);
  const section = (title, ...kids) => el("section", { class: "card" }, el("h2", {}, title), kids);

  // A line of the record with Edit and Remove; Remove asks first.
  function itemRow(key, text, { badge, edit, remove, formNode }) {
    if (st.open === key && formNode) return el("li", {}, formNode());
    const removing = st.open === `rm:${key}`;
    return el("li", { class: "item" },
      el("div", { class: "item-text" }, text, badge ? el("span", { class: "pill import" }, "From import") : null),
      removing
        ? el("div", { class: "confirm" }, "Remove this?", button("Yes, remove", async () => { st.notice = (await act(remove())) ?? ""; if (st.notice) draw(); }, "btn danger"), button("No", close))
        : el("div", { class: "actions small" }, formNode ? button("Edit", toggle(key)) : null, button("Remove", toggle(`rm:${key}`))));
  }

  function partSection(part) {
    const rows = st.ev[part.kind];
    const addKey = `add:${part.kind}`;
    return section(part.title,
      rows.length ? el("ul", { class: "plain" }, rows.map(row => itemRow(`${part.kind}:${row.id}`, part.describe(row), {
        badge: row.from_import,
        remove: () => send("DELETE", `/api/${part.kind}/${row.id}`),
        formNode: () => form(part.fields(row, ctx), {
          onSubmit: values => act(send("PATCH", `/api/${part.kind}/${row.id}`, values)), onCancel: close }),
      }))) : el("p", { class: "muted" }, "None recorded."),
      st.open === addKey
        ? form(part.fields(null, ctx), { submit: "Add", onSubmit: values => act(send("POST", `/api/events/${id}/${part.kind}`, values)), onCancel: close })
        : button(part.add, toggle(addKey)));
  }

  // ---- status: started and resolved
  function statusSection() {
    const e = st.ev;
    const startedForm = form([{ key: "started_on", label: "Started", type: "date", value: e.started_on }],
      { onSubmit: v => act(send("PATCH", `/api/events/${id}`, v)), onCancel: close });
    const resolveForm = form([
      { key: "resolved_on", label: "Resolved on", type: "date", value: e.resolved_on ?? todayIso() },
      { key: "outcome_id", label: "Outcome", type: "select", options: ctx.list("outcome", e.outcome?.id), value: e.outcome?.id },
    ], { submit: e.open ? "Mark resolved" : "Save", onSubmit: v => act(send("PATCH", `/api/events/${id}`, v)), onCancel: close });
    return el("section", { class: "card" },
      el("div", { class: "status" }, el("span", { class: e.open ? "pill open" : "pill done" }, e.open ? "Open" : "Resolved")),
      el("div", { class: "kv" },
        st.open === "started" ? startedForm : el("div", { class: "row" }, el("span", {}, "Started"), el("b", {}, dateLabel(e.started_on)), button("Edit", toggle("started"))),
        st.open === "resolve" ? resolveForm
          : el("div", { class: "row" }, el("span", {}, "Resolved"),
            el("b", {}, e.open ? "Not yet" : joined([dateLabel(e.resolved_on), e.outcome?.label])),
            el("span", { class: "actions small" }, button(e.open ? "Mark resolved" : "Edit", toggle("resolve")),
              e.open ? null : button("Reopen", async () => { const m = await act(send("PATCH", `/api/events/${id}`, { resolved_on: null })); if (m) { st.notice = m; draw(); } })))),
      e.resolved_on ? el("p", { class: "muted" }, `Lasted ${daysLabel(daysBetween(e.started_on, e.resolved_on))}.`) : null);
  }

  // ---- doctors on this event
  function doctorsSection() {
    const linked = new Set(st.ev.doctors.map(d => d.id));
    const rows = st.ev.doctors.map(d => itemRow(`doc:${d.id}`, joined([d.name, d.specialty, d.role]), {
      remove: () => send("DELETE", `/api/events/${id}/doctors/${d.id}`),
      formNode: () => form([{ key: "role", label: `Role of ${d.name} on this event`, type: "text", value: d.role }],
        { onSubmit: v => act(send("PUT", `/api/events/${id}/doctors/${d.id}`, v)), onCancel: close }),
    }));
    return section("Doctors",
      rows.length ? el("ul", { class: "plain" }, rows) : el("p", { class: "muted" }, "None recorded."),
      st.open === "add:doctor" ? addDoctorForm(linked) : button("Add a doctor", toggle("add:doctor")));
  }

  function addDoctorForm(linked) {
    const choices = st.doctors.filter(d => !d.retired && !linked.has(d.id)).map(d => el("option", { value: d.id }, d.name));
    const pick = el("select", { id: "doc-pick" }, el("option", { value: "" }, "Choose a doctor"), choices, el("option", { value: "new" }, "A new doctor"));
    const name = el("input", { id: "doc-name", type: "text", maxlength: 200 });
    const specialty = el("input", { id: "doc-specialty", type: "text", maxlength: 200 });
    const role = el("input", { id: "doc-role", type: "text", maxlength: 100 });
    const fresh = el("div", { class: "field-group", hidden: true },
      el("div", { class: "field" }, el("label", { for: "doc-name" }, "Name"), name),
      el("div", { class: "field" }, el("label", { for: "doc-specialty" }, "Specialty"), specialty));
    pick.addEventListener("change", () => { fresh.hidden = pick.value !== "new"; });
    const message = el("div", { class: "form-error", role: "alert" });
    const node = el("form", { class: "edit" },
      el("div", { class: "field" }, el("label", { for: "doc-pick" }, "Doctor"), pick), fresh,
      el("div", { class: "field" }, el("label", { for: "doc-role" }, "Role on this event (like urologist or ER)"), role),
      message, el("div", { class: "actions" }, el("button", { class: "btn primary", type: "submit" }, "Add"), button("Cancel", close)));
    node.addEventListener("submit", async e => {
      e.preventDefault();
      let doctorId = Number(pick.value);
      if (!pick.value) { message.textContent = "Choose a doctor, or add a new one."; return; }
      if (pick.value === "new") {
        const made = await send("POST", "/api/doctors", { name: name.value, specialty: specialty.value });
        if (made.error) { message.textContent = made.error; return; }
        doctorId = made.id;
        const all = await get("/api/doctors");
        if (!all.error) st.doctors = all;
      }
      const error = await act(send("PUT", `/api/events/${id}/doctors/${doctorId}`, { role: role.value }));
      if (error) message.textContent = error;
    });
    return node;
  }

  // ---- notes
  function notesSection() {
    const items = st.ev.notes.map(n => {
      const key = `note:${n.id}`;
      const edited = n.updated_at !== n.created_at ? " (edited)" : "";
      return itemRow(key, el("span", { class: "note" }, el("small", {}, `${n.author_name} · ${timeLabel(n.created_at)}${edited}`), el("span", { class: "note-body" }, n.body)), {
        remove: () => send("DELETE", `/api/notes/${n.id}`),
        formNode: () => form([{ key: "body", label: "Note", type: "textarea", value: n.body }],
          { onSubmit: v => act(send("PATCH", `/api/notes/${n.id}`, v)), onCancel: close }),
      });
    });
    const text = el("textarea", { id: "note-new", rows: 3, maxlength: 4000, placeholder: "Add a note" });
    text.value = st.noteDraft;
    text.addEventListener("input", () => { st.noteDraft = text.value; });
    const message = el("div", { class: "form-error", role: "alert" });
    const add = el("button", { class: "btn primary", type: "submit" }, "Add note");
    const noteForm = el("form", { class: "add-note" }, el("label", { for: "note-new" }, "Add a note"), text, message, add);
    noteForm.addEventListener("submit", async e => {
      e.preventDefault();
      if (!text.value.trim()) { message.textContent = "Write something first."; return; }
      add.disabled = true;
      const error = await act(send("POST", `/api/events/${id}/notes`, { body: text.value }));
      add.disabled = false;
      if (error) message.textContent = error;
      else { st.noteDraft = ""; draw(); }
    });
    return section("Notes", items.length ? el("ul", { class: "plain" }, items) : el("p", { class: "muted" }, "No notes yet."), noteForm);
  }

  // ---- files
  function filesSection() {
    const rows = st.files.map(f => el("li", { class: "item" },
      el("div", { class: "item-text" },
        el("a", { class: "file-link", href: `/api/files/${f.id}/content`, target: "_blank", rel: "noopener" }, f.name),
        el("small", { class: "muted" }, joined([fileKind(f.mime), fileSize(f.size)]))),
      el("div", { class: "actions small" }, button("Take off this event", async () => {
        const m = await act(send("DELETE", `/api/events/${id}/files/${f.id}`));
        if (m) { st.notice = m; draw(); }
      }))));
    const status = el("div", { class: "muted", role: "status" });
    const input = el("input", { id: "file-new", type: "file", accept: ".pdf,.png,.jpg,.jpeg,.xml,.htm,.html,application/pdf,image/png,image/jpeg,text/xml,text/html" });
    input.addEventListener("change", async () => {
      const file = input.files[0];
      if (!file) return;
      status.textContent = "Uploading…";
      const r = await upload(`/api/events/${id}/files`, file);
      if (r.error) { status.textContent = r.error; input.value = ""; return; }
      await reload();
      st.notice = r.status === "same" || r.status === "updated" ? "That document was already in the app. It is now on this event." : "";
      draw();
    });
    return section("Files",
      rows.length ? el("ul", { class: "plain" }, rows) : el("p", { class: "muted" }, "No files attached."),
      el("div", { class: "field" }, el("label", { for: "file-new" }, "Attach a file (PDF, image, or a portal document)"), input), status);
  }

  function deleteSection() {
    return el("section", { class: "card danger-zone" },
      st.open === "delete"
        ? el("div", { class: "confirm" },
          el("p", {}, "Delete this event? Its details and notes are deleted. Its files are not: they go back to the import inbox."),
          button("Yes, delete the event", async () => {
            const r = await send("DELETE", `/api/events/${id}`);
            if (r.error) { st.notice = r.error; draw(); } else location.hash = "#/";
          }, "btn danger"), button("Cancel", close))
        : button("Delete this event", toggle("delete"), "btn danger"));
  }

  function draw() {
    view.replaceChildren(
      el("p", {}, el("a", { href: "#/" }, "← All events")),
      st.notice ? errorNotice(st.notice) : el("span"),
      el("div", { class: "event-page" },
        el("div", { class: "event-main" }, statusSection(), PARTS.slice(0, 4).map(partSection), doctorsSection(), PARTS.slice(4).map(partSection)),
        el("div", { class: "event-side" },
          // Task 12 fills this with the pending import suggestions (renderSuggestions), apart from what was entered.
          el("section", { id: "event-suggestions", class: "card" }),
          notesSection(), filesSection(), deleteSection())));
  }
  draw();
}

// Whole days from one YYYY-MM-DD to another; UTC so a daylight-saving change cannot move it.
export function daysBetween(from, to) {
  const t = s => Date.UTC(...s.split("-").map((n, i) => (i === 1 ? Number(n) - 1 : Number(n))));
  return Math.round((t(to) - t(from)) / 86400000);
}
