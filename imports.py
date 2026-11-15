"""Importing a patient portal download, and the suggestions it makes. Route-free: every function takes a
sqlite3.Connection (app.py's Task 8 section holds the routes).

An import keeps the download's documents (through files.store, which de-duplicates them and remembers deleted ones)
and turns its coded entries into suggestions. A suggestion changes nothing until a person accepts it: accepting
writes through the same details.py code a manual edit uses, records the suggestion's id on the row, and links the
source document to the event.

Which entries make suggestions, their identity, and where they go are the spec's ("Importing a patient portal
download", step 4):

- An entry is relevant when it is stone-related itself (a stone diagnosis, a stone procedure, a stone analysis), or
  it is an encounter that carries a stone diagnosis, or a procedure or diagnosis dated within such an encounter, or
  its date falls within an existing event's window. Everything else (a flu visit in a summary's history) makes
  nothing.
- A suggestion's key is its entry's own id where it has a trustworthy one, else a hash of its document's identity,
  kind, date and code (suggestion_key). A key seen before is never suggested again, whatever became of it, so a
  re-import repeats nothing and a dismissed suggestion stays dismissed.
- Its target is computed when it is shown, not stored (windows, target, group, pending); only a person's move is
  stored, in `target_event_id`, and wins.

Sizes are never suggested."""
import hashlib
import json
from dataclasses import dataclass
from datetime import date, timedelta

import ccda
import codes
import common
import db
import details
import events
import files
import lists
from common import ApiError

COUNTS = ("documents", "new", "updated", "same", "skipped_deleted", "failed", "suggestions")
BEFORE_START = 7       # days a window opens before an event's start
AFTER_END = 30         # days it stays open after the event resolved
OPEN_DAYS = 90         # an open event's window ends this long after its start, so a forgotten one collects nothing more
GROUP_DAYS = 14        # untargeted suggestions within this many days of the previous one form one proposed episode
MAX_IDS = 1000         # suggestion ids in one "create this event"
LIST_KIND = {"visit": "visit_kind", "procedure": "procedure_kind", "complication": "complication_kind",
             "stone_type": "stone_type"}
DECIDED = "Already decided."


# ---------------------------------------------------------------- the run
def run(con, user: dict, filename, data: bytes) -> dict:
    """Import one download (a ZIP, or one XML or PDF document). Returns, and stores as an `imports` row,
    {documents, new, updated, same, skipped_deleted, failed, suggestions}: each document is counted once, under what
    files.store did with it or `failed` when it could not be read; `suggestions` is how many new ones it made.

    Raises ccda.Refused for the whole upload (nothing is written), and ApiError 400 when the download holds no
    document at all. The XDM packaging (METADATA.XML, INDEX.HTM, stylesheets and the like: anything the reader calls
    `other` without an error) is neither kept nor counted. The run is one files.atomic transaction: on any error the
    rows and the blobs it wrote are gone."""
    docs = [d for d in ccda.read_download(data, filename) if d.kind in ("ccda", "pdf") or d.error]
    if not docs:
        raise ApiError(400, ccda.EMPTY)
    counts = dict.fromkeys(COUNTS, 0)
    with files.atomic(con):
        import_id = con.execute(
            "INSERT INTO imports (created_by, created_at, documents, new, updated, same, skipped_deleted, failed, "
            "suggestions) VALUES (?, ?, 0, 0, 0, 0, 0, 0, 0)", (user["email"], common.now())).lastrowid
        ws = windows(con, common.today())
        for doc in docs:
            status, made = _document(con, user, import_id, doc, ws)
            counts["documents"] += 1
            counts[status] += 1
            counts["suggestions"] += made
        con.execute("UPDATE imports SET documents = ?, new = ?, updated = ?, same = ?, skipped_deleted = ?, failed = ?, "
                    "suggestions = ? WHERE id = ?", (*counts.values(), import_id))
    return counts


def _document(con, user, import_id, doc: ccda.Document, ws) -> tuple[str, int]:
    """(count it falls under, suggestions made) for one document."""
    if doc.error:  # a nested ZIP or an unknown compression: not opened, not kept
        return "failed", 0
    parsed = None
    if doc.kind == "ccda":
        try:
            parsed = ccda.parse(doc.data)
        except ccda.Unsafe:  # refused outright, not kept
            return "failed", 0
        except ccda.Unreadable:  # kept as a file, so a person can still open it, and reported
            try:
                stored = files.store(con, user, doc.name, doc.data, origin="import")
            except files.NotAccepted:
                return "failed", 0
            return ("skipped_deleted" if stored.status == "deleted" else "failed"), 0
    try:
        stored = files.store(con, user, doc.name, doc.data, origin="import", doc_id=parsed and parsed.doc_id,
                             set_id=parsed and parsed.set_id, version=parsed and parsed.version)
    except files.NotAccepted:
        return "failed", 0
    if stored.status == "deleted":
        return "skipped_deleted", 0
    if parsed is None:
        return stored.status, 0
    # Only a newer version of a document we hold refreshes its pending suggestions (a later admission end date, say);
    # another document that shares an entry id, or an older copy, must not overwrite them.
    refresh = stored.status == "updated"
    made = 0
    for key, kind, on_date, end_date, payload in proposals(parsed, ws, sha=files.sha256(doc.data)):
        made += _save(con, import_id, stored.id, key, kind, on_date, end_date, payload, refresh)
    return stored.status, made


def _save(con, import_id, file_id, key, kind, on_date, end_date, payload, refresh) -> int:
    """Insert one suggestion unless its key, or the same fact (kind, dates and value) from another document, is
    already known in any state. Returns 1 when it inserted."""
    text = json.dumps(payload, sort_keys=True)
    row = con.execute("SELECT id, state FROM suggestions WHERE key = ?", (key,)).fetchone()
    if row is not None:
        if refresh and row["state"] == "pending":
            con.execute("UPDATE suggestions SET on_date = ?, end_date = ?, payload = ?, file_id = COALESCE(file_id, ?) "
                        "WHERE id = ?", (on_date, end_date, text, file_id, row["id"]))
        return 0
    if con.execute("SELECT 1 FROM suggestions WHERE kind = ? AND on_date IS ? AND end_date IS ? AND payload = ?",
                   (kind, on_date, end_date, text)).fetchone():
        return 0
    con.execute("INSERT INTO suggestions (key, file_id, import_id, kind, on_date, end_date, payload) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)", (key, file_id, import_id, kind, on_date, end_date, text))
    return 1


# ---------------------------------------------------------------- which entries, and what they suggest
def _norm(name: str) -> str:
    return " ".join(name.split()).casefold()


def _any(entry_codes, table) -> bool:
    return any(pair in table for pair in entry_codes)


def _span(entry: ccda.Entry) -> tuple[str, str]:
    """An entry's dates as a closed range; an end before the start (or none) is the start."""
    return entry.start, entry.end if entry.end and entry.end >= entry.start else entry.start


def suggestion_key(parsed: ccda.Parsed, entry: ccda.Entry, kind: str, *, sha: str | None = None,
                   trust_id: bool = True) -> str:
    """"e:<entry id>|<kind>" when the entry has an id (and `trust_id`), else "h:" + sha256 of
    "<set id, doc id or the file's sha>|<kind>|<date>|<system>|<code>". `kind` names the suggestion within its entry
    ("visit", "procedure:ureteroscopy", "doctor:robin rivera"), since one entry can make several."""
    if entry.entry_id and trust_id:
        return f"e:{entry.entry_id}|{kind}"
    source = parsed.set_id or parsed.doc_id or sha or ""
    raw = f"{source}|{kind}|{entry.start or ''}|{entry.system or ''}|{entry.code or ''}"
    return "h:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _shared_ids(entries) -> set[str]:
    """Entry ids that one document gives to entries that differ (kind, dates or code): a system that repeats one id
    on many entries. Those entries are keyed by hash instead, or all but the first would be lost."""
    seen: dict[str, tuple] = {}
    shared = set()
    for e in entries:
        if not e.entry_id:
            continue
        sig = (e.kind, e.start, e.end, e.system, e.code)
        if seen.setdefault(e.entry_id, sig) != sig:
            shared.add(e.entry_id)
    return shared


def proposals(parsed: ccda.Parsed, ws, *, sha: str | None = None) -> list[tuple]:
    """The suggestions one parsed document makes: [(key, kind, on_date, end_date, payload)], in entry order.

    An encounter carries a stone diagnosis when one of its own diagnoses is a stone diagnosis, or when a stone
    diagnosis entry of the same document is dated within the encounter's dates (a visit document that keeps its
    diagnoses in the problems section)."""
    entries = parsed.entries
    stone_dx_days = [e.start for e in entries if e.kind == "diagnosis" and e.start and _any(e.codes, codes.STONE_DIAGNOSES)]
    stone_spans = []
    stone_encounters = set()
    for i, e in enumerate(entries):
        if e.kind != "encounter":
            continue
        carries = _any(e.diagnoses, codes.STONE_DIAGNOSES)
        if not carries and e.start:
            lo, hi = _span(e)
            carries = any(lo <= d <= hi for d in stone_dx_days)
        if carries:
            stone_encounters.add(i)
            if e.start:
                stone_spans.append(_span(e))
    shared = _shared_ids(entries)
    out = []
    for i, e in enumerate(entries):
        if e.kind == "encounter":
            relevant = i in stone_encounters
        elif e.kind == "procedure":
            relevant = _any(e.codes, codes.PROCEDURES)
        elif e.kind == "diagnosis":
            relevant = _any(e.codes, codes.STONE_DIAGNOSES)
        else:
            relevant = _any(e.codes, codes.STONE_ANALYSIS)
        if not relevant and e.start and e.kind in ("procedure", "diagnosis"):
            relevant = any(lo <= e.start <= hi for lo, hi in stone_spans)
        if not relevant and e.start:
            relevant = target(ws, e.start) is not None
        if not relevant:
            continue
        for sub, kind, on_date, end_date, payload in _made_by(e):
            key = suggestion_key(parsed, e, sub, sha=sha, trust_id=e.entry_id not in shared)
            out.append((key, kind, on_date, end_date, payload))
    return out


def _made_by(e: ccda.Entry):
    """(sub-kind, kind, on_date, end_date, payload) for each suggestion one relevant entry makes."""
    out = []
    if e.kind == "encounter":
        slug = codes.VISIT_CLASS.get(e.encounter_class or "")
        if slug and e.start:
            end = e.end if e.end and e.end >= e.start else None
            out.append(("visit", "visit", e.start, end, {"slug": slug}))
        out += _complications(e.diagnoses, e.start)
    elif e.kind == "procedure":
        slugs = []
        for pair in e.codes:
            slugs += [s for s in codes.PROCEDURES.get(pair, ()) if s not in slugs]
        out += [(f"procedure:{s}", "procedure", e.start, None, {"slug": s}) for s in slugs]
    elif e.kind == "diagnosis":
        out += _complications(e.codes, e.start)
    elif e.kind == "result" and _any(e.codes, codes.STONE_ANALYSIS):
        slug = codes.STONE_TYPE_VALUES.get(e.value_code) if e.value_code else None
        if slug:
            out.append((f"stone_type:{slug}", "stone_type", e.start, None, {"slug": slug}))
    if e.kind in ("encounter", "procedure"):
        out += [(f"doctor:{_norm(name)}", "doctor", e.start, None, {"name": name}) for name in e.performers]
    return out


def _complications(pairs, on_date):
    slugs = []
    for pair in pairs:
        slug = codes.COMPLICATIONS.get(pair)
        if slug and slug not in slugs:
            slugs.append(slug)
    return [(f"complication:{s}", "complication", on_date, None, {"slug": s}) for s in slugs]


# ---------------------------------------------------------------- targets
@dataclass(frozen=True)
class Window:
    event_id: int
    start: str       # started_on - 7 days
    end: str         # resolved_on + 30 days, or started_on + 90 days while open
    started_on: str


def _shift(day: str, days: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=days)).isoformat()


def windows(con, today: str) -> list[Window]:
    """Every event's suggestion window. `today` is part of the plan's interface; the windows as the spec states them
    do not depend on it (an open event's ends 90 days after its start, past or future)."""
    return [Window(r["id"], _shift(r["started_on"], -BEFORE_START),
                   _shift(r["resolved_on"], AFTER_END) if r["resolved_on"] else _shift(r["started_on"], OPEN_DAYS),
                   r["started_on"])
            for r in con.execute("SELECT id, started_on, resolved_on FROM events ORDER BY started_on, id")]


def target(ws: list[Window], day: str | None) -> int | None:
    """The event whose window holds `day`; in two or more, the one whose start is nearest, and on a tie the later
    event (later start, then the later one created). None for no date or no window."""
    if not day:
        return None
    inside = [w for w in ws if w.start <= day <= w.end]
    if not inside:
        return None
    when = date.fromisoformat(day)
    best = min(inside, key=lambda w: (abs((date.fromisoformat(w.started_on) - when).days),
                                      -date.fromisoformat(w.started_on).toordinal(), -w.event_id))
    return best.event_id


def group(pending: list[dict], days: int = GROUP_DAYS) -> list[list[dict]]:
    """Untargeted suggestions as proposed episodes: sorted by on_date (then id), each joins the previous one's group
    when it is within `days` of the previous suggestion's date. Undated ones form no group (they are left out)."""
    dated = sorted((s for s in pending if s.get("on_date")), key=lambda s: (s["on_date"], s.get("id") or 0))
    groups: list[list[dict]] = []
    for s in dated:
        if groups and (date.fromisoformat(s["on_date"]) - date.fromisoformat(groups[-1][-1]["on_date"])).days <= days:
            groups[-1].append(s)
        else:
            groups.append([s])
    return groups


# ---------------------------------------------------------------- reading
_SELECT = ("SELECT s.*, f.name AS file_name FROM suggestions s LEFT JOIN files f ON f.id = s.file_id ")


def _labels(con) -> dict:
    return {(r["kind"], r["slug"]): r["label"] for r in con.execute("SELECT kind, slug, label FROM lists WHERE slug IS NOT NULL")}


def _dict(con, row, ws, labels) -> dict:
    """{id, kind, state, on_date, end_date, label, slug, doctor_id, file, target_event_id, moved, event_id}.
    `label` is the list item's current label (a doctor's name for a doctor); `doctor_id` the doctor the name already
    matches, if any; `target_event_id` the person's move, else the computed target (pending only)."""
    payload = json.loads(row["payload"])
    slug = payload.get("slug")
    if row["kind"] == "doctor":
        label, doctor_id = payload.get("name"), lists.find_doctor(con, payload.get("name"))
    else:
        label, doctor_id = labels.get((LIST_KIND[row["kind"]], slug), slug), None
    moved = row["target_event_id"] is not None
    if row["state"] != "pending":
        tgt = None
    else:
        tgt = row["target_event_id"] if moved else target(ws, row["on_date"])
    return {"id": row["id"], "kind": row["kind"], "state": row["state"], "on_date": row["on_date"],
            "end_date": row["end_date"], "label": label, "slug": slug, "doctor_id": doctor_id,
            "file": {"id": row["file_id"], "name": row["file_name"]} if row["file_id"] is not None else None,
            "target_event_id": tgt, "moved": moved, "event_id": row["event_id"]}


def get(con, suggestion_id: int) -> dict:
    row = con.execute(_SELECT + "WHERE s.id = ?", (suggestion_id,)).fetchone()
    if row is None:
        raise ApiError(404, "No such suggestion.")
    return _dict(con, row, windows(con, common.today()), _labels(con))


def pending(con, today: str) -> dict:
    """Pending suggestions, by where they go:
    {"by_event": {event_id: [s, ...]}, "proposed": [{"suggestion_ids", "start", "end", "suggestions"}],
     "undated": [s, ...]}. Each list is in date order (undated last), then by id. `start` is a proposed episode's
    earliest date and `end` its latest date or end date."""
    ws = windows(con, today)
    labels = _labels(con)
    rows = con.execute(_SELECT + "WHERE s.state = 'pending' ORDER BY s.on_date IS NULL, s.on_date, s.id").fetchall()
    by_event: dict[int, list] = {}
    untargeted, undated = [], []
    for row in rows:
        s = _dict(con, row, ws, labels)
        if s["target_event_id"] is not None:
            by_event.setdefault(s["target_event_id"], []).append(s)
        elif s["on_date"]:
            untargeted.append(s)
        else:
            undated.append(s)
    proposed = [{"suggestion_ids": [s["id"] for s in g], "start": g[0]["on_date"],
                 "end": max(max(s["on_date"], s["end_date"] or "") for s in g), "suggestions": g}
                for g in group(untargeted)]
    return {"by_event": by_event, "proposed": proposed, "undated": undated}


def for_event(con, event_id: int, today: str) -> list[dict]:
    """The pending suggestions that go to one event (404 for a missing event)."""
    files.need_event(con, event_id)
    return pending(con, today)["by_event"].get(event_id, [])


# ---------------------------------------------------------------- deciding
def parse_event_id(value, *, required: bool = False) -> int | None:
    """An event id from a JSON body: an integer, or null/missing (400 when `required`)."""
    if value is None:
        if required:
            raise ApiError(400, "Choose an event.")
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ApiError(400, "Choose an event.")
    return value


def _pending_row(con, suggestion_id):
    row = con.execute("SELECT * FROM suggestions WHERE id = ?", (suggestion_id,)).fetchone()
    if row is None:
        raise ApiError(404, "No such suggestion.")
    if row["state"] != "pending":
        raise ApiError(409, DECIDED)
    return row


def _item(con, kind, slug) -> int:
    row = con.execute("SELECT id FROM lists WHERE kind = ? AND slug = ?", (LIST_KIND[kind], slug)).fetchone()
    if row is None:  # seeded items are never deleted, so only a hand-edited database gets here
        raise ApiError(400, "That list item no longer exists.")
    return row["id"]


def accept(con, user: dict, suggestion_id: int, event_id: int | None = None) -> dict:
    """Write a pending suggestion onto `event_id`, or onto its target (a person's move, else the computed one): 400
    when it has neither, 404 for a missing suggestion or event, 409 "Already decided." unless pending. The value goes
    through details.add (with suggestion_id) or details.link_doctor, as a manual edit would; the source document is
    linked to the event. A stone type sets the event's only untyped stone, else adds a stone with that type. A
    doctor is found by name (retired ones too) or added; an existing link and its role are left as they are."""
    with db.transaction(con):
        row = _pending_row(con, suggestion_id)
        if event_id is None:
            event_id = row["target_event_id"] or target(windows(con, common.today()), row["on_date"])
            if event_id is None:
                raise ApiError(400, "Choose an event for this suggestion.")
        files.need_event(con, event_id)
        payload = json.loads(row["payload"])
        kind = row["kind"]
        if kind == "visit":
            details.add(con, user, event_id, "visits", {"kind_id": _item(con, kind, payload["slug"]),
                                                        "from_date": row["on_date"], "to_date": row["end_date"]},
                        suggestion_id=suggestion_id)
        elif kind in ("procedure", "complication"):
            details.add(con, user, event_id, kind + "s", {"kind_id": _item(con, kind, payload["slug"]),
                                                         "on_date": row["on_date"]}, suggestion_id=suggestion_id)
        elif kind == "stone_type":
            type_id = _item(con, kind, payload["slug"])
            untyped = con.execute("SELECT id FROM stones WHERE event_id = ? AND type_id IS NULL", (event_id,)).fetchall()
            if len(untyped) == 1:
                details.update(con, user, "stones", untyped[0]["id"], {"type_id": type_id, "type_from_lab": True})
            else:
                details.add(con, user, event_id, "stones", {"type_id": type_id, "type_from_lab": True},
                            suggestion_id=suggestion_id)
        else:
            doctor_id = lists.find_doctor(con, payload["name"]) or lists.add_doctor(con, {"name": payload["name"]})
            if con.execute("SELECT 1 FROM event_doctors WHERE event_id = ? AND doctor_id = ?",
                           (event_id, doctor_id)).fetchone() is None:
                details.link_doctor(con, event_id, doctor_id, None, user)
        if row["file_id"] is not None:
            files.link(con, event_id, row["file_id"])
        _decide(con, user, suggestion_id, "accepted", event_id)
    return get(con, suggestion_id)


def _decide(con, user, suggestion_id, state, event_id=None):
    con.execute("UPDATE suggestions SET state = ?, event_id = ?, decided_by = ?, decided_at = ? WHERE id = ?",
                (state, event_id, user["email"], common.now(), suggestion_id))


def dismiss(con, user: dict, suggestion_id: int) -> dict:
    """Mark a pending suggestion dismissed; nothing else changes, and its key keeps it from coming back."""
    with db.transaction(con):
        _pending_row(con, suggestion_id)
        _decide(con, user, suggestion_id, "dismissed")
    return get(con, suggestion_id)


def move(con, suggestion_id: int, event_id: int | None) -> dict:
    """Send a pending suggestion to `event_id` whatever its dates say, or (None) back to its computed target."""
    with db.transaction(con):
        _pending_row(con, suggestion_id)
        if event_id is not None:
            files.need_event(con, event_id)
        con.execute("UPDATE suggestions SET target_event_id = ? WHERE id = ?", (event_id, suggestion_id))
    return get(con, suggestion_id)


def create_event(con, user: dict, suggestion_ids) -> int:
    """One new open event, started on the earliest date among the given pending suggestions, which then go to it
    (stored like a move, so a long episode stays together past the new event's window). Accepts nothing. 400 for a
    bad list or when none of them has a date, 404 for a missing suggestion, 409 for a decided one."""
    if (not isinstance(suggestion_ids, list) or not 0 < len(suggestion_ids) <= MAX_IDS
            or any(isinstance(i, bool) or not isinstance(i, int) for i in suggestion_ids)):
        raise ApiError(400, "Choose the suggestions to start the event from.")
    ids = list(dict.fromkeys(suggestion_ids))
    days = [d for d in (_pending_row(con, i)["on_date"] for i in ids) if d]
    if not days:
        raise ApiError(400, "None of these suggestions has a date. Start the event by hand.")
    event_id = events.create(con, user, {"started_on": min(days)})
    marks = ",".join("?" * len(ids))
    con.execute(f"UPDATE suggestions SET target_event_id = ? WHERE state = 'pending' AND id IN ({marks})", (event_id, *ids))
    return event_id
