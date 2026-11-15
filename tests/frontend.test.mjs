// Frontend unit tests: `node --test tests/*.test.mjs`. The modules under static/js/ are ES modules with no
// top-level DOM access (main.js aside), so they load in Node as they are; only the DOM-free helpers are tested here.
import assert from "node:assert/strict";
import { test } from "node:test";

import { get, send, upload } from "../static/js/api.js";
import { dateLabel, daysLabel, esc, fileKind, fileSize, mm, parseHash, timeLabel, todayIso } from "../static/js/core.js";
import { daysBetween, valueOf } from "../static/js/event.js";

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
