"""Events (one stone episode each) and their notes log. Route-free: every function takes a sqlite3.Connection.

An event is open while `resolved_on` is empty. Details (stones, visits and so on) belong to `details.py`; a detail
row is removed with its event by the foreign keys' ON DELETE CASCADE, and a `files` row is never deleted here."""
from datetime import date, timedelta

import common
import db
import settings
from common import ApiError

NOTE_MAX = 10000


def check_dates(started_on, resolved_on) -> None:
    """Both are YYYY-MM-DD strings already parsed by common.parse_date (or None). The end may not be before the
    start, and the start may not be after tomorrow (a day of slack for a phone set to another time zone)."""
    if started_on and resolved_on and resolved_on < started_on:
        raise ApiError(400, "The end date is before the start date.")
    if started_on and date.fromisoformat(started_on) > date.fromisoformat(common.today()) + timedelta(days=1):
        raise ApiError(400, "The start date is in the future.")


def _outcome_id(con, value, current=None):
    """None, or the id of an `outcome` list item. A retired item is accepted only when it is already the value."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ApiError(400, "Outcome must be one of the listed outcomes.")
    row = con.execute("SELECT kind, retired FROM lists WHERE id = ?", (value,)).fetchone()
    # Task 2's lists.py is not a dependency here, so the list table is read directly.
    if row is None or row["kind"] != "outcome" or (row["retired"] and value != current):
        raise ApiError(400, "Outcome must be one of the listed outcomes.")
    return value


def _touch(con, user, event_id):
    con.execute("UPDATE events SET updated_by = ?, updated_at = ? WHERE id = ?",
                (user["email"], common.now(), event_id))


def _require(con, event_id):
    row = con.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    if row is None:
        raise ApiError(404, "No such event.")
    return row


def create(con, user: dict, data: dict) -> int:
    """A new open event. `started_on` defaults to today; an optional `note` becomes its first note."""
    started_on = common.parse_date(data.get("started_on"), field="Start date") or common.today()
    check_dates(started_on, None)
    note = common.text(data, "note", max_len=NOTE_MAX)
    t = common.now()
    with db.transaction(con):
        event_id = con.execute(
            "INSERT INTO events (started_on, created_by, created_at, updated_by, updated_at) VALUES (?,?,?,?,?)",
            (started_on, user["email"], t, user["email"], t)).lastrowid
        if note:
            add_note(con, user, event_id, note)
    return event_id


def update(con, user: dict, event_id: int, data: dict) -> None:
    """Change `started_on`, `resolved_on` and `outcome_id`, whichever are present in `data`. `resolved_on: null`
    reopens the event and clears its outcome. An outcome needs a resolved event."""
    with db.transaction(con):
        row = _require(con, event_id)
        started_on, resolved_on, outcome_id = row["started_on"], row["resolved_on"], row["outcome_id"]
        if "started_on" in data:
            started_on = common.parse_date(data["started_on"], field="Start date", required=True)
        if "resolved_on" in data:
            resolved_on = common.parse_date(data["resolved_on"], field="End date")
            if resolved_on is None:
                outcome_id = None
        if "outcome_id" in data:
            outcome_id = _outcome_id(con, data["outcome_id"], row["outcome_id"])
        # A start date that did not change is not re-checked against today: an old event stays editable.
        if started_on != row["started_on"]:
            check_dates(started_on, None)
        if resolved_on and resolved_on < started_on:
            raise ApiError(400, "The end date is before the start date.")
        if outcome_id is not None and resolved_on is None:
            raise ApiError(400, "Mark the event resolved before choosing an outcome.")
        con.execute("UPDATE events SET started_on = ?, resolved_on = ?, outcome_id = ? WHERE id = ?",
                    (started_on, resolved_on, outcome_id, event_id))
        _touch(con, user, event_id)


def delete(con, event_id: int) -> None:
    """The event, its details and notes, and its links to files. The files themselves stay."""
    with db.transaction(con):
        _require(con, event_id)
        con.execute("DELETE FROM events WHERE id = ?", (event_id,))


def _notes(con, event_id):
    rows = con.execute("SELECT * FROM notes WHERE event_id = ? ORDER BY created_at, id", (event_id,)).fetchall()
    return [_note(r) for r in rows]


def _note(r):
    return {"id": r["id"], "author": r["author"], "author_name": settings.USERS.get(r["author"], r["author"]),
            "created_at": r["created_at"], "updated_at": r["updated_at"], "body": r["body"]}


def get(con, event_id: int) -> dict:
    row = _require(con, event_id)
    outcome = None
    if row["outcome_id"] is not None:
        label = con.execute("SELECT label FROM lists WHERE id = ?", (row["outcome_id"],)).fetchone()["label"]
        outcome = {"id": row["outcome_id"], "label": label}
    return {"id": row["id"], "started_on": row["started_on"], "resolved_on": row["resolved_on"],
            "open": row["resolved_on"] is None, "outcome": outcome, "notes": _notes(con, event_id),
            "created_by": row["created_by"], "updated_by": row["updated_by"], "updated_at": row["updated_at"]}


def list_all(con) -> list[dict]:
    """Open events first, then resolved; each group newest start first."""
    rows = con.execute("SELECT id, started_on, resolved_on FROM events "
                       "ORDER BY resolved_on IS NOT NULL, started_on DESC, id DESC").fetchall()
    return [{"id": r["id"], "started_on": r["started_on"], "resolved_on": r["resolved_on"],
             "open": r["resolved_on"] is None} for r in rows]


def _body(body):
    return common.text({"body": body}, "body", required=True, max_len=NOTE_MAX)


def add_note(con, user: dict, event_id: int, body) -> int:
    body = _body(body)
    _require(con, event_id)
    t = common.now()
    return con.execute("INSERT INTO notes (event_id, author, created_at, updated_at, body) VALUES (?,?,?,?,?)",
                       (event_id, user["email"], t, t, body)).lastrowid


def update_note(con, user: dict, note_id: int, body) -> None:
    """Either user may edit any note; the author stays whoever wrote it."""
    body = _body(body)
    cur = con.execute("UPDATE notes SET body = ?, updated_at = ? WHERE id = ?", (body, common.now(), note_id))
    if cur.rowcount == 0:
        raise ApiError(404, "No such note.")


def delete_note(con, note_id: int) -> None:
    if con.execute("DELETE FROM notes WHERE id = ?", (note_id,)).rowcount == 0:
        raise ApiError(404, "No such note.")
