// Frontend unit tests: `node --test tests/*.test.mjs`. The modules under static/js/ are ES modules with no
// top-level DOM access (main.js aside), so they load in Node as they are; only the DOM-free helpers are tested here.
import assert from "node:assert/strict";
import { test } from "node:test";

import { get, send, upload } from "../static/js/api.js";
import { dateLabel, daysLabel, esc, fileKind, fileSize, mm, parseHash, timeLabel, todayIso } from "../static/js/core.js";
import { daysBetween, valueOf } from "../static/js/event.js";
import { reorder } from "../static/js/settings.js";
import { COLUMNS, doctorText, summaryLine, summaryRow } from "../static/js/summary.js";
import { gapText, headlineStats, hospitalText, sizeSide } from "../static/js/timeline.js";

test("esc escapes the five HTML characters", () => {
  assert.equal(esc(`<a href="x">'&`), "&lt;a href=&quot;x&quot;&gt;&#39;&amp;");
  assert.equal(esc(null), "");
  assert.equal(esc(undefined), "");
  assert.equal(esc(0), "0");
  assert.equal(esc("&amp;"), "&amp;amp;");
});

test("mm turns integer tenths into text", () => {
  assert.equal(mm(75), "7.5 mm");
  assert.equal(mm(70), "7 mm");
  assert.equal(mm(1), "0.1 mm");
  assert.equal(mm(2000), "200 mm");
  assert.equal(mm(null), "");
  assert.equal(mm(undefined), "");
  assert.equal(mm(7.5), "");
  assert.equal(mm("75"), "");
});

test("daysLabel", () => {
  assert.equal(daysLabel(1), "1 day");
  assert.equal(daysLabel(0), "0 days");
  assert.equal(daysLabel(43), "43 days");
  assert.equal(daysLabel(59), "59 days");
  assert.equal(daysLabel(60), "2 months");
  assert.equal(daysLabel(426), "14 months");
  assert.equal(daysLabel(null), "");
  assert.equal(daysLabel(1.5), "");
});

test("dateLabel", () => {
  assert.equal(dateLabel("2024-03-01"), "1 Mar 2024");
  assert.equal(dateLabel("2024-12-31"), "31 Dec 2024");
  assert.equal(dateLabel(null), "");
  assert.equal(dateLabel("2024-13-01"), "");
  assert.equal(dateLabel("1 March 2024"), "");
});

test("todayIso and timeLabel use the local calendar", () => {
  assert.equal(todayIso(new Date(2024, 0, 5, 23, 59)), "2024-01-05");
  assert.equal(timeLabel(new Date(2024, 2, 1, 14, 5).getTime() / 1000), "1 Mar 2024, 14:05");
  assert.equal(timeLabel(null), "");
});

test("fileKind and fileSize", () => {
  assert.equal(fileKind("application/pdf"), "PDF");
  assert.equal(fileKind("image/jpeg"), "JPEG");
  assert.equal(fileKind("text/html"), "HTML");
  assert.equal(fileKind("x/y"), "File");
  assert.equal(fileSize(500), "500 B");
  assert.equal(fileSize(2048), "2 KB");
  assert.equal(fileSize(5 * 1024 * 1024), "5.0 MB");
  assert.equal(fileSize(undefined), "");
});

test("parseHash", () => {
  assert.deepEqual(parseHash("#/event/3"), { parts: ["event", "3"], query: {} });
  assert.deepEqual(parseHash(""), { parts: [], query: {} });
  assert.deepEqual(parseHash("#/import?x=1"), { parts: ["import"], query: { x: "1" } });
});

test("daysBetween counts whole days across a month end", () => {
  assert.equal(daysBetween("2024-02-27", "2024-03-02"), 4);
  assert.equal(daysBetween("2024-03-01", "2024-03-01"), 0);
});

test("valueOf: blank is null, ids are numbers, checkboxes are booleans", () => {
  assert.equal(valueOf({ type: "text" }, { value: "  " }), null);
  assert.equal(valueOf({ type: "text" }, { value: " 7,5 " }), "7,5"); // the server decides what a size may look like
  assert.equal(valueOf({ type: "select" }, { value: "12" }), 12);
  assert.equal(valueOf({ type: "select" }, { value: "left" }), "left");
  assert.equal(valueOf({ type: "select" }, { value: "" }), null);
  assert.equal(valueOf({ type: "date" }, { value: "" }), null);
  assert.equal(valueOf({ type: "check" }, { checked: true }), true);
});

// ---------------------------------------------------------------- api.js, against a stubbed fetch
function stubFetch(reply) {
  const calls = [];
  globalThis.fetch = async (path, init) => {
    calls.push({ path, init });
    if (reply instanceof Error) throw reply;
    return reply;
  };
  return calls;
}
const json = (status, body) => ({ ok: status < 300, status, json: async () => body });

test("api: a mutation sends X-Sickbay and a JSON body; a GET sends neither", async () => {
  let calls = stubFetch(json(200, { id: 1 }));
  assert.deepEqual(await send("POST", "/api/events", { started_on: "2024-03-01" }), { id: 1 });
  assert.equal(calls[0].init.method, "POST");
  assert.equal(calls[0].init.headers["X-Sickbay"], "1");
  assert.equal(calls[0].init.headers["Content-Type"], "application/json");
  assert.equal(calls[0].init.body, '{"started_on":"2024-03-01"}');
  assert.equal(calls[0].init.credentials, "same-origin");
  calls = stubFetch(json(200, []));
  await get("/api/events");
  assert.equal(calls[0].init.method, "GET");
  assert.equal(calls[0].init.headers["X-Sickbay"], undefined);
  assert.equal(calls[0].init.body, undefined);
});

test("api: DELETE also sends the header and a body; 204 is {}", async () => {
  const calls = stubFetch({ ok: true, status: 204, json: async () => { throw new Error("empty"); } });
  assert.deepEqual(await send("DELETE", "/api/notes/4"), {});
  assert.equal(calls[0].init.headers["X-Sickbay"], "1");
  assert.equal(calls[0].init.body, "{}");
});

test("api: upload is multipart with X-Sickbay and no Content-Type of its own", async () => {
  const calls = stubFetch(json(201, { id: 3, status: "new" }));
  const r = await upload("/api/events/1/files", new Blob(["x"], { type: "application/pdf" }));
  assert.equal(r.status, "new");
  assert.ok(calls[0].init.body instanceof FormData);
  assert.ok(calls[0].init.body.has("file"));
  assert.equal(calls[0].init.headers["X-Sickbay"], "1");
  assert.equal(calls[0].init.headers["Content-Type"], undefined);
});

test("api: a refusal resolves to {error}, a dead network too", async () => {
  stubFetch(json(400, { error: "Size is in mm, like 7 or 7.5." }));
  assert.deepEqual(await send("POST", "/api/events/1/stones", { size_mm: "x" }), { error: "Size is in mm, like 7 or 7.5." });
  stubFetch({ ok: false, status: 502, json: async () => { throw new Error("html"); } });
  assert.deepEqual(await get("/api/me"), { error: "Request failed (502)." });
  stubFetch(new TypeError("fail"));
  assert.deepEqual(await get("/api/me"), { error: "Cannot reach the server." });
});

// ---------------------------------------------------------------- Task 11: timeline, summary, settings
const row = (over = {}) => ({
  id: 1, started_on: "2024-03-01", resolved_on: "2024-03-09", open: false, duration_days: 8, gap_days_before: 426,
  sizes_mm: ["7"], sides: ["left"], types: ["Calcium oxalate"], visit_kinds: ["ER visit"], nights: 2,
  procedures: ["Stent placement", "Ureteroscopy"], complications: ["Urinary tract infection"], ...over,
});

test("gapText: a dash for no gap, whole months from 60 days", () => {
  assert.equal(gapText(null), "\u2014");
  assert.equal(gapText(undefined), "\u2014");
  assert.equal(gapText(426), "14 months");
  assert.equal(gapText(19), "19 days");
  assert.equal(gapText(0), "0 days");
});

test("summaryRow is the seven cells in the table's order", () => {
  assert.equal(COLUMNS.length, 7);
  assert.deepEqual(summaryRow(row()), ["1 Mar 2024", "9 Mar 2024", "7 mm, left", "Calcium oxalate", "ER visit, 2 nights",
    "Stent placement, Ureteroscopy", "Urinary tract infection"]);
  assert.deepEqual(summaryRow(row({ open: true, resolved_on: null, sizes_mm: [], sides: [], types: [], visit_kinds: [], nights: 0,
    procedures: [], complications: [] })), ["1 Mar 2024", "open", "\u2014", "\u2014", "\u2014", "\u2014", "\u2014"]);
});

test("size, side and hospital text", () => {
  assert.equal(sizeSide(row({ sizes_mm: ["7", "4.5"], sides: ["both"] })), "7 mm + 4.5 mm, both");
  assert.equal(sizeSide(row({ sizes_mm: [], sides: ["right"] })), "right");
  assert.equal(hospitalText(row({ visit_kinds: [], nights: 1 })), "1 night");
  assert.equal(hospitalText(row({ visit_kinds: ["ER visit"], nights: 0 })), "ER visit");
});

test("headline stats and the summary line say only what happened", () => {
  const h = { episodes: 3, typical_gap_days: 426, shortest_gap_days: 300, longest_gap_days: 500, since_last_days: 12, hospital_nights: 5, first_started_on: "2020-01-15" };
  const stats = headlineStats(h);
  assert.deepEqual(stats.map(s => s.value), ["3", "14 months", "12 days", "5"]);
  assert.equal(stats[1].detail, "shortest 10 months, longest 16 months");
  assert.equal(summaryLine(h, [row(), row()]), "3 episodes from 15 Jan 2020 \u00b7 typical gap 14 months \u00b7 5 nights in hospital \u00b7 4 procedures");
  const one = { ...h, episodes: 1, typical_gap_days: null, shortest_gap_days: null, longest_gap_days: null, hospital_nights: 0 };
  assert.equal(summaryLine(one, []), "1 episode from 15 Jan 2020 \u00b7 no hospital nights \u00b7 0 procedures");
  assert.equal(headlineStats(one)[1].value, "\u2014");
  assert.equal(summaryLine({ ...one, episodes: 0, first_started_on: null }, []), "No episodes recorded.");
});

test("doctorText", () => {
  assert.equal(doctorText({ name: "Dr. Rivera", specialty: "urology" }), "Dr. Rivera (urology)");
  assert.equal(doctorText({ name: "Dr. Chen", specialty: null }), "Dr. Chen");
});

test("reorder renumbers the list and returns only what changed", () => {
  const items = [{ id: 5, sort_order: 1 }, { id: 6, sort_order: 2 }, { id: 7, sort_order: 3 }];
  assert.deepEqual(reorder(items, 1, -1), [{ id: 6, sort_order: 1 }, { id: 5, sort_order: 2 }]);
  assert.deepEqual(reorder(items, 0, 1), [{ id: 6, sort_order: 1 }, { id: 5, sort_order: 2 }]);
  assert.deepEqual(reorder(items, 0, -1), []);
  assert.deepEqual(reorder(items, 2, 1), []);
  // ties and gaps in the stored numbers are tidied as the list is renumbered
  assert.deepEqual(reorder([{ id: 1, sort_order: 4 }, { id: 2, sort_order: 4 }], 1, -1), [{ id: 2, sort_order: 1 }, { id: 1, sort_order: 2 }]);
});

// ---------------------------------------------------------------- Task 12: import, inbox and suggestions
import { runSummary } from "../static/js/importer.js";
import { eventChoice, renderSuggestions, suggestionText } from "../static/js/suggestions.js";

const sug = (over = {}) => ({ id: 1, kind: "visit", state: "pending", on_date: "2024-03-01", end_date: null, label: "ER visit", slug: "er",
  doctor_id: null, file: null, target_event_id: 7, moved: false, event_id: null, ...over });

test("suggestionText for each kind", () => {
  assert.equal(suggestionText(sug()), "ER visit, 1 Mar 2024");
  assert.equal(suggestionText(sug({ kind: "visit", label: "Hospital admission", end_date: "2024-03-04" })), "Hospital admission, 1 Mar 2024 to 4 Mar 2024");
  assert.equal(suggestionText(sug({ end_date: "2024-03-01" })), "ER visit, 1 Mar 2024");
  assert.equal(suggestionText(sug({ kind: "procedure", label: "Ureteroscopy", on_date: "2024-03-02" })), "Procedure: Ureteroscopy, 2 Mar 2024");
  assert.equal(suggestionText(sug({ kind: "complication", label: "Urinary tract infection" })), "Complication: Urinary tract infection, 1 Mar 2024");
  assert.equal(suggestionText(sug({ kind: "stone_type", label: "Calcium oxalate" })), "Stone type: Calcium oxalate, 1 Mar 2024");
  assert.equal(suggestionText(sug({ kind: "doctor", label: "Dr. Rivera" })), "Doctor: Dr. Rivera, 1 Mar 2024");
  assert.equal(suggestionText(sug({ kind: "procedure", label: "Stent", on_date: null })), "Procedure: Stent, no date");
});

test("eventChoice names an event by its start only", () => {
  assert.equal(eventChoice({ id: 3, started_on: "2024-03-01", open: true }), "1 Mar 2024 (open)");
  assert.equal(eventChoice({ id: 3, started_on: "2024-03-01", open: false }), "1 Mar 2024");
});

test("runSummary names every count and no advice", () => {
  const lines = runSummary({ documents: 3, new: 2, updated: 0, same: 1, skipped_deleted: 0, failed: 0, suggestions: 4 });
  assert.deepEqual(lines, ["3 documents in the download.", "2: new.", "1: already in the app.", "4 new suggestions to review below."]);
  assert.equal(runSummary({ documents: 1, new: 0, updated: 0, same: 1, skipped_deleted: 0, failed: 1, suggestions: 0 }).at(-1), "No new suggestions.");
});

// A just-enough DOM for el() and the suggestion rows, so the screens' own code runs without a browser.
class FNode {
  constructor(tag = "") { this.tag = tag; this.attrs = {}; this.listeners = {}; this.kids = []; this.value = ""; }
  setAttribute(k, v) { this.attrs[k] = v; }
  addEventListener(t, fn) { (this.listeners[t] ??= []).push(fn); }
  append(...n) { this.kids.push(...n); }
  replaceChildren(...n) { this.kids = n; }
  get childNodes() { return this.kids; }
  focus() {}
  get textContent() { return this.kids.map(k => (k instanceof FText ? k.data : k.textContent)).join(""); }
  set textContent(v) { this.kids = [new FText(v)]; }
  async click() { await Promise.all((this.listeners.click ?? []).map(fn => fn({ preventDefault() {} }))); }
}
class FText extends FNode { constructor(d) { super(); this.data = String(d); } get textContent() { return this.data; } }
globalThis.Node = FNode;
globalThis.document = { createElement: t => new FNode(t), createTextNode: d => new FText(d) };
const walk = (n, out = []) => { out.push(n); (n.kids ?? []).forEach(k => walk(k, out)); return out; };
const all = (n, tag, text) => walk(n).filter(x => x.tag === tag && (text === undefined || x.textContent === text));

function stubRoutes(handler) {
  const calls = [];
  globalThis.fetch = async (path, init) => {
    calls.push({ path, method: init.method, headers: init.headers, body: init.body });
    const [status, data] = handler(path, init);
    return { ok: status < 400, status, json: async () => data };
  };
  return calls;
}

test("suggestion rows: file names are text, Accept only with a target, Accept posts with X-Sickbay", async () => {
  const calls = stubRoutes(() => [200, { suggestion: sug(), event: { id: 7 } }]);
  const host = new FNode("div");
  const seen = [];
  const evil = "<img src=x onerror=alert(1)>.pdf";
  renderSuggestions(host, [sug({ file: { id: 9, name: evil } }), sug({ id: 2, target_event_id: null, kind: "doctor", label: "Dr. <b>Rivera</b>" })], { onChange: r => seen.push(r) });
  assert.equal(all(host, "img").length + all(host, "b").length, 0);
  assert.ok(host.textContent.includes(evil) && host.textContent.includes("Doctor: Dr. <b>Rivera</b>, 1 Mar 2024"));
  const link = all(host, "a")[0];
  assert.equal(link.attrs.href, "/api/files/9/content");
  assert.equal(link.attrs.target, "_blank");
  assert.equal(all(host, "button", "Accept").length, 1); // the one with no event yet can only be moved or dismissed
  assert.equal(all(host, "button", "Dismiss").length, 2);
  await all(host, "button", "Accept")[0].click();
  assert.deepEqual([calls[0].path, calls[0].method, calls[0].body], ["/api/suggestions/1/accept", "POST", "{}"]);
  assert.equal(calls[0].headers["X-Sickbay"], "1");
  assert.deepEqual(seen, [{ suggestion: sug(), event: { id: 7 } }]);
});

test("a refused accept shows the message beside the row and does not reload", async () => {
  stubRoutes(() => [409, { error: "Already decided." }]);
  const host = new FNode("div");
  let changed = 0;
  renderSuggestions(host, [sug()], { onChange: () => changed++ });
  await all(host, "button", "Accept")[0].click();
  assert.equal(changed, 0);
  assert.equal(walk(host).find(n => n.attrs?.role === "alert").textContent, "Already decided.");
});

test("Move offers the other events and Split off posts that one suggestion id", async () => {
  const calls = stubRoutes(p => (p === "/api/events" ? [200, [{ id: 7, started_on: "2024-03-01", open: true }, { id: 8, started_on: "2023-01-05", open: false }]]
    : [201, { id: 11 }]));
  const host = new FNode("div");
  let changed = 0;
  renderSuggestions(host, [sug({ id: 5 })], { onChange: () => changed++ });
  await all(host, "button", "Move")[0].click();
  const options = all(host, "option").map(o => o.textContent);
  assert.deepEqual(options, ["5 Jan 2023", "Split off into a new event"]); // its own target is not offered
  const pick = all(host, "select")[0];
  pick.value = "new";
  await all(host, "button", "Move")[1].click();
  assert.deepEqual([calls[1].path, calls[1].method, calls[1].body], ["/api/suggestions/new-event", "POST", '{"suggestion_ids":[5]}']);
  assert.equal(changed, 1);
  pick.value = "8";
  await all(host, "button", "Move")[1].click();
  assert.deepEqual([calls[2].path, calls[2].method, calls[2].body], ["/api/suggestions/5", "PATCH", '{"event_id":8}']);
});

test("Send back appears only for a moved suggestion and clears the move", async () => {
  const calls = stubRoutes(() => [200, { suggestion: sug() }]);
  const host = new FNode("div");
  renderSuggestions(host, [sug({ moved: true })]);
  await all(host, "button", "Send back")[0].click();
  assert.deepEqual([calls[0].path, calls[0].method, calls[0].body], ["/api/suggestions/1", "PATCH", '{"event_id":null}']);
  renderSuggestions(host, [sug()]);
  assert.equal(all(host, "button", "Send back").length, 0);
});

import { render as renderImport } from "../static/js/importer.js";

test("the import screen draws proposed episodes, undated suggestions and the inbox; creating an event posts the ids", async () => {
  const pending = { by_event: { 7: [sug()] }, proposed: [{ suggestion_ids: [3, 4], start: "2022-05-01", end: "2022-05-09",
    suggestions: [sug({ id: 3, target_event_id: null, on_date: "2022-05-01" }), sug({ id: 4, target_event_id: null, kind: "procedure", label: "Stent", on_date: "2022-05-09" })] }],
  undated: [sug({ id: 6, target_event_id: null, on_date: null })] };
  const files = [{ id: 21, name: "<b>summary</b>.pdf", mime: "application/pdf", size: 2048, event_ids: [] }];
  const calls = stubRoutes(p => (p === "/api/suggestions" ? [200, pending] : p === "/api/files/inbox" ? [200, files]
    : p === "/api/events" ? [200, [{ id: 7, started_on: "2024-03-01", open: true }]] : [201, { id: 12 }]));
  const view = new FNode("div");
  globalThis.location = { hash: "" };
  await renderImport({ view, alive: () => true, rerender: () => {} });
  const text = view.textContent;
  for (const want of ["Proposed episodes", "1 May 2022 to 9 May 2022", "Procedure: Stent, 9 May 2022", "Suggestions with no date", "Inbox", "<b>summary</b>.pdf", "2 KB",
    "1 Mar 2024 (open)", "1 suggestion"]) assert.ok(text.includes(want), want);
  assert.equal(all(view, "b").length, 0);
  assert.equal(all(view, "button", "Accept").length, 0); // none of these has an event to accept onto
  await all(view, "button", "Create this event")[0].click();
  const made = calls.at(-1);
  assert.deepEqual([made.path, made.method, made.body], ["/api/suggestions/new-event", "POST", '{"suggestion_ids":[3,4]}']);
  assert.equal(globalThis.location.hash, "#/event/12");
  await all(view, "button", "Delete")[0].click();
  assert.ok(view.textContent.includes("It will not come back on a later import."));
  await all(view, "button", "Yes, delete it")[0].click();
  assert.deepEqual([calls.at(-1).path, calls.at(-1).method], ["/api/files/21", "DELETE"]);
  all(view, "select").at(-1).value = "7"; // the fake select has no selected option of its own
  await all(view, "button", "Attach")[0].click();
  assert.deepEqual([calls.at(-1).path, calls.at(-1).method], ["/api/events/7/files/21", "PUT"]);
});
