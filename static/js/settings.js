// Settings: the editable pick lists (rename, add, retire or restore, reorder, the "counts hospital nights" flag on
// visit kinds) and the shared doctor list (add, edit, retire, delete). Lists are data: an item is retired, never
// deleted, so an old event keeps its value. After every change the screen reads both lists again and redraws.
import { get, send } from "./api.js";
import { el, errorNotice } from "./core.js";

export const LISTS = [
  { kind: "outcome", title: "Outcomes", noun: "outcome" },
  { kind: "location", title: "Stone locations", noun: "location" },
  { kind: "stone_type", title: "Stone types", noun: "stone type" },
  { kind: "visit_kind", title: "Kinds of care visit", noun: "kind of visit" },
  { kind: "procedure_kind", title: "Kinds of procedure", noun: "kind of procedure" },
  { kind: "complication_kind", title: "Kinds of complication", noun: "kind of complication" },
];

// The sort_order changes that move `items[index]` by `delta` (-1 up, +1 down): the whole list is renumbered 1..n in
// its new order and only the items whose number differs are returned, as {id, sort_order}. [] at either end.
export function reorder(items, index, delta) {
  const to = index + delta;
  if (index < 0 || index >= items.length || to < 0 || to >= items.length) return [];
  const order = items.map(i => i.id);
  [order[index], order[to]] = [order[to], order[index]];
  const old = new Map(items.map(i => [i.id, i.sort_order]));
  return order.map((id, n) => ({ id, sort_order: n + 1 })).filter(c => old.get(c.id) !== c.sort_order);
}

// ---------------------------------------------------------------- pieces
const btn = (label, onClick, cls = "btn", extra = {}) => el("button", { class: cls, type: "button", on: { click: onClick }, ...extra }, label);

function textField(id, label, value, attrs = {}) {
  const input = el("input", { id, type: "text", maxlength: 200, value: value ?? "", ...attrs });
  return { input, node: el("div", { class: "field" }, el("label", { for: id }, label), input) };
}

// A small form: fields, a message line, Save and Cancel. `submit` gets nothing and returns an {error} or a result.
function inlineForm({ fields, saveLabel, submit, cancel }) {
  const message = el("div", { class: "form-error", role: "alert" });
  const save = el("button", { class: "btn primary", type: "submit" }, saveLabel);
  const form = el("form", { class: "edit" }, fields, message,
    el("div", { class: "actions" }, save, cancel ? btn("Cancel", cancel) : null));
  form.addEventListener("submit", async e => {
    e.preventDefault();
    save.disabled = true;
    const r = await submit();
    save.disabled = false;
    if (r?.error) message.textContent = r.error;
  });
  return form;
}

// ---------------------------------------------------------------- the screen
export async function render({ view, alive, rerender }) {
  const [lists, doctors] = await Promise.all([get("/api/lists"), get("/api/doctors?all=1")]);
  if (!alive()) return;
  const failed = lists.error ?? doctors.error;
  if (failed) {
    view.append(errorNotice(failed));
    return;
  }
  const st = { editing: null }; // "item:<id>" or "doctor:<id>" or "doctor:new": the one open form
  const confirming = { id: null };
  const draw = () => view.replaceChildren(
    el("h2", {}, "Settings"),
    el("p", { class: "muted" }, "A retired item stays on the events that already use it; it is just no longer offered for new entries."),
    ...LISTS.map(l => listCard(l)),
    doctorCard());

  // Run a change, then read everything again; an error stays on the page where it happened.
  async function change(call) {
    const r = await call();
    if (r.error) return r;
    st.editing = null;
    confirming.id = null;
    await rerender();
    return r;
  }
  const open = key => () => { st.editing = key; draw(); };
  const close = () => { st.editing = null; confirming.id = null; draw(); };

  // ---- lists
  function listCard({ kind, title, noun }) {
    const items = lists[kind];
    const note = el("div", { class: "form-error", role: "alert" });
    const run = async call => {
      const r = await change(call);
      note.textContent = r.error ?? "";
      return r;
    };
    const rows = items.map((item, i) => {
      const key = `item:${item.id}`;
      if (st.editing === key) {
        const f = textField(`rename-${item.id}`, `Name of this ${noun}`, item.label, { required: true });
        return el("li", { class: "item" }, inlineForm({
          fields: f.node, saveLabel: "Save", cancel: close,
          submit: () => change(() => send("PATCH", `/api/lists/items/${item.id}`, { label: f.input.value })),
        }));
      }
      const move = delta => () => run(async () => {
        for (const c of reorder(items, i, delta)) {
          const r = await send("PATCH", `/api/lists/items/${c.id}`, { sort_order: c.sort_order });
          if (r.error) return r;
        }
        return {};
      });
      const nights = kind === "visit_kind"
        ? el("label", { class: "check-inline" },
          el("input", { type: "checkbox", checked: item.counts_nights, on: { change: async e => { const r = await run(() => send("PATCH", `/api/lists/items/${item.id}`, { counts_nights: e.target.checked })); if (r.error) e.target.checked = !e.target.checked; } } }),
          " counts hospital nights")
        : null;
      return el("li", { class: item.retired ? "item retired" : "item" },
        el("span", { class: "item-text" }, el("span", {}, item.label, item.retired ? el("span", { class: "pill" }, "Retired") : null), nights),
        el("span", { class: "actions small" },
          btn("Up", move(-1), "btn", { disabled: i === 0, "aria-label": `Move ${item.label} up` }),
          btn("Down", move(1), "btn", { disabled: i === items.length - 1, "aria-label": `Move ${item.label} down` }),
          btn("Rename", open(key), "btn", { "aria-label": `Rename ${item.label}` }),
          btn(item.retired ? "Restore" : "Retire", () => run(() => send("PATCH", `/api/lists/items/${item.id}`, { retired: !item.retired })), "btn", { "aria-label": `${item.retired ? "Restore" : "Retire"} ${item.label}` })));
    });
    const added = textField(`add-${kind}`, `Add a ${noun}`, "", { required: true });
    return el("section", { class: "card" }, el("h2", {}, title),
      rows.length ? el("ul", { class: "plain" }, rows) : el("p", { class: "muted" }, "Nothing here yet."), note,
      inlineForm({ fields: added.node, saveLabel: "Add", submit: () => change(() => send("POST", `/api/lists/${kind}`, { label: added.input.value })) }));
  }

  // ---- doctors
  function doctorForm(d) {
    const id = d ? d.id : "new";
    const name = textField(`doc-name-${id}`, "Name", d?.name, { required: true });
    const spec = textField(`doc-spec-${id}`, "Specialty", d?.specialty);
    const practice = textField(`doc-practice-${id}`, "Practice", d?.practice);
    const body = () => ({ name: name.input.value, specialty: spec.input.value, practice: practice.input.value });
    return inlineForm({
      fields: [name.node, spec.node, practice.node], saveLabel: d ? "Save" : "Add doctor", cancel: d ? close : null,
      submit: () => change(() => (d ? send("PATCH", `/api/doctors/${d.id}`, body()) : send("POST", "/api/doctors", body()))),
    });
  }

  function doctorRow(d) {
    if (st.editing === `doctor:${d.id}`) return el("li", { class: "item" }, doctorForm(d));
    const message = el("div", { class: "form-error", role: "alert" });
    const remove = confirming.id === d.id
      ? el("div", { class: "confirm" }, el("p", {}, `Delete ${d.name}? This cannot be undone.`),
        btn("Delete", async () => { const r = await change(() => send("DELETE", `/api/doctors/${d.id}`)); if (r.error) message.textContent = r.error; }, "btn danger"),
        btn("Keep", close))
      : null;
    return el("li", { class: d.retired ? "item retired" : "item" },
      el("span", { class: "item-text" },
        el("span", {}, d.name, d.retired ? el("span", { class: "pill" }, "Retired") : null),
        el("span", { class: "muted" }, [d.specialty, d.practice].filter(Boolean).join(" · ")), remove, message),
      el("span", { class: "actions small" },
        btn("Edit", open(`doctor:${d.id}`), "btn", { "aria-label": `Edit ${d.name}` }),
        btn(d.retired ? "Restore" : "Retire", () => change(() => send("PATCH", `/api/doctors/${d.id}`, { retired: !d.retired })), "btn", { "aria-label": `${d.retired ? "Restore" : "Retire"} ${d.name}` }),
        btn("Delete", () => { confirming.id = d.id; draw(); }, "btn danger", { "aria-label": `Delete ${d.name}` })));
  }

  const doctorCard = () => el("section", { class: "card" }, el("h2", {}, "Doctors"),
    el("p", { class: "muted" }, "One shared list, picked from on any event. A doctor who is on an event can be retired but not deleted."),
    doctors.length ? el("ul", { class: "plain" }, doctors.map(doctorRow)) : el("p", { class: "muted" }, "No doctors yet."),
    doctorForm(null));

  draw();
}
