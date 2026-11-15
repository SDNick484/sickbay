"""The rows that hang off an event: stones, care visits, procedures, complications, follow-ups and the event's
doctors. Route-free: every function takes a sqlite3.Connection.

Lists (location, stone type, the kinds) are read with SQL here, not through lists.py. A list reference must be an
item of the right kind; a retired item is accepted only when it is already the row's value. Dates inside an event
are free (a follow-up may be months later) except that an end may not be before its start. Writes do not open a
transaction of their own (the importer already runs inside one) but are atomic through a savepoint."""
import math
import re
from contextlib import contextmanager
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import common
from common import ApiError

KINDS = ("stones", "visits", "procedures", "complications", "followups")
SIDES = ("left", "right", "both", "unknown")
SIZE_MIN, SIZE_MAX = 1, 2000  # tenths of a mm: 0.1 to 200.0
SIZE_MESSAGE = "Size is in mm, like 7 or 7.5."
_SIZE = re.compile(r"[0-9]+(\.[0-9]*)?|\.[0-9]+")
_TENTH = Decimal("0.1")

_TABLE = {"stones": "stones", "visits": "visits", "procedures": "procedures", "complications": "complications",
          "followups": "followups"}
_NAME = {"stones": "stone", "visits": "visit", "procedures": "procedure", "complications": "complication",
         "followups": "follow-up"}


def parse_size(value) -> int | None:
    """Millimetres as a number or as text with a dot decimal ("7", 7.5, "7.5") to integer tenths of a mm.
    None and blank text are None. Below 0.1 or above 200.0, a comma decimal, an exponent, NaN, inf, a unit, a
    boolean or anything else is 400 "Size is in mm, like 7 or 7.5."; a third decimal rounds half up to a tenth."""
    bad = ApiError(400, SIZE_MESSAGE)
    if value is None:
        return None
    if isinstance(value, bool):
        raise bad
    if isinstance(value, str):
        value = value.strip()
        if value == "":
            return None
        if len(value) > 20 or not _SIZE.fullmatch(value):
            raise bad
        mm = Decimal(value)
    elif isinstance(value, int):
        if not SIZE_MIN <= value * 10 <= SIZE_MAX:  # also keeps a huge int away from Decimal
            raise bad
        mm = Decimal(value)
    elif isinstance(value, float):
        if not math.isfinite(value) or not 0 < value <= SIZE_MAX / 10 + 1:
            raise bad
        mm = Decimal(repr(value))
    else:
        raise bad
    tenths = int((mm / _TENTH).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if not SIZE_MIN <= tenths <= SIZE_MAX:
        raise bad
    return tenths


def _size_mm(tenths):
    """Integer tenths as text: 70 is "7", 75 is "7.5" (the same as figures.py's rows)."""
    if tenths is None:
        return None
    whole, tenth = divmod(tenths, 10)
    return f"{whole}.{tenth}" if tenth else str(whole)


@contextmanager
def _atomic(con):
    con.execute("SAVEPOINT details")
    try:
        yield
    except BaseException:
        con.execute("ROLLBACK TO details")
        con.execute("RELEASE details")
        raise
    con.execute("RELEASE details")


def _touch(con, user, event_id):
    """Every write moves the event's updated_at, and updated_by when we know who (importer calls may not say)."""
    if user is None:
        con.execute("UPDATE events SET updated_at = ? WHERE id = ?", (common.now(), event_id))
    else:
        con.execute("UPDATE events SET updated_by = ?, updated_at = ? WHERE id = ?",
                    (user["email"], common.now(), event_id))


def _require_event(con, event_id):
    if con.execute("SELECT 1 FROM events WHERE id = ?", (event_id,)).fetchone() is None:
        raise ApiError(404, "No such event.")


# ---------------------------------------------------------------- field parsers
# Each takes (con, value, current) and returns the value to store; `current` is the row's present value (None when
# adding). Missing keys never reach a parser on update.
def _list_ref(list_kind, what, *, required=False):
    def parse(con, value, current):
        if value is None:
            if required:
                raise ApiError(400, f"Choose {what}.")
            return None
        bad = ApiError(400, f"Choose {what} from the list.")
        if isinstance(value, bool) or not isinstance(value, int):
            raise bad
        row = con.execute("SELECT kind, retired FROM lists WHERE id = ?", (value,)).fetchone()
        if row is None or row["kind"] != list_kind or (row["retired"] and value != current):
            raise bad
        return value
    return parse


def _doctor_ref(con, value, current):
    if value is None:
        return None
    bad = ApiError(400, "Choose a doctor from the list.")
    if isinstance(value, bool) or not isinstance(value, int):
        raise bad
    row = con.execute("SELECT retired FROM doctors WHERE id = ?", (value,)).fetchone()
    if row is None or (row["retired"] and value != current):
        raise bad
    return value


def _date(field, *, required=False):
    return lambda con, value, current: common.parse_date(value, field=field, required=required)


def _text(key, *, required=False, max_len=2000):
    return lambda con, value, current: common.text({key: value}, key, required=required, max_len=max_len)


def _flag(key):
    def parse(con, value, current):
        if not isinstance(value, bool):
            raise ApiError(400, f"{key} must be true or false.")
        return int(value)
    return parse


def _side(con, value, current):
    if value not in SIDES:
        raise ApiError(400, "Side must be left, right, both or unknown.")
    return value


def _size(con, value, current):
    return parse_size(value)


# kind -> {input key: (column, parser)}; `required` columns must be given on add and may not be cleared.
_FIELDS = {
    "stones": {
        "size_mm": ("size_tenths", _size),
        "side": ("side", _side),
        "location_id": ("location_id", _list_ref("location", "a location")),
        "type_id": ("type_id", _list_ref("stone_type", "a stone type")),
        "type_from_lab": ("type_from_lab", _flag("type_from_lab")),
    },
    "visits": {
        "kind_id": ("kind_id", _list_ref("visit_kind", "a kind of visit", required=True)),
        "from_date": ("from_date", _date("From date", required=True)),
        "to_date": ("to_date", _date("To date")),
    },
    "procedures": {
        "kind_id": ("kind_id", _list_ref("procedure_kind", "a kind of procedure", required=True)),
        "on_date": ("on_date", _date("Date")),
        "doctor_id": ("doctor_id", _doctor_ref),
        "note": ("note", _text("note")),
    },
    "complications": {
        "kind_id": ("kind_id", _list_ref("complication_kind", "a kind of complication", required=True)),
        "on_date": ("on_date", _date("Date")),
        "note": ("note", _text("note")),
    },
    "followups": {
        "on_date": ("on_date", _date("Date")),
        "what": ("what", _text("what", required=True, max_len=500)),
        "doctor_id": ("doctor_id", _doctor_ref),
        "done": ("done", _flag("done")),
    },
}
_REQUIRED = {"visits": {"kind_id", "from_date"}, "procedures": {"kind_id"}, "complications": {"kind_id"},
             "followups": {"what"}, "stones": set()}


def _check_kind(kind):
    if kind not in KINDS:
        raise ApiError(404, "Not Found")


def _check_range(kind, values):
    if kind == "visits" and values["to_date"] and values["to_date"] < values["from_date"]:
        raise ApiError(400, "The end date is before the start date.")


def _row(con, kind, row_id):
    row = con.execute(f"SELECT * FROM {_TABLE[kind]} WHERE id = ?", (row_id,)).fetchone()
    if row is None:
        raise ApiError(404, f"No such {_NAME[kind]}.")
    return row


def event_id_of(con, kind, row_id) -> int:
    """The event a detail row belongs to; 404 for a missing row or an unknown kind."""
    _check_kind(kind)
    return _row(con, kind, row_id)["event_id"]


def add(con, user: dict, event_id: int, kind: str, data: dict, *, suggestion_id=None) -> int:
    """A new row on an event; returns its id. 404 for an unknown kind or event; 400 for a bad or missing value."""
    _check_kind(kind)
    if not isinstance(data, dict):
        raise ApiError(400, "Expected a JSON object.")
    _require_event(con, event_id)
    values = {}
    for key, (column, parse) in _FIELDS[kind].items():
        if key in data or column in _REQUIRED[kind]:
            values[column] = parse(con, data.get(key), None)
            if values[column] is None and column in _REQUIRED[kind]:
                raise ApiError(400, f"{_label(kind, column)} is required.")
    _check_range(kind, {"to_date": None, **values})
    if suggestion_id is not None:
        values["suggestion_id"] = suggestion_id
    cols = ["event_id", *values]
    with _atomic(con):
        row_id = con.execute(
            f"INSERT INTO {_TABLE[kind]} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            (event_id, *values.values())).lastrowid
        _touch(con, user, event_id)
    return row_id


def _label(kind, column):
    return {"kind_id": "Kind", "from_date": "From date", "what": "What"}.get(column, column)


def update(con, user: dict, kind: str, row_id: int, data: dict) -> None:
    """Change the keys present in `data`. A required value cannot be cleared; a retired list item stays valid only
    as the row's current value."""
    _check_kind(kind)
    if not isinstance(data, dict):
        raise ApiError(400, "Expected a JSON object.")
    row = _row(con, kind, row_id)
    values = {}
    for key, (column, parse) in _FIELDS[kind].items():
        if key in data:
            values[column] = parse(con, data[key], row[column])
            if values[column] is None and column in _REQUIRED[kind]:
                raise ApiError(400, f"{_label(kind, column)} is required.")
    _check_range(kind, {c: values.get(c, row[c]) for c in ("from_date", "to_date")} if kind == "visits" else {})
    with _atomic(con):
        if values:
            con.execute(f"UPDATE {_TABLE[kind]} SET {', '.join(f'{c} = ?' for c in values)} WHERE id = ?",
                        (*values.values(), row_id))
        _touch(con, user, row["event_id"])


def delete(con, kind: str, row_id: int, user: dict | None = None) -> None:
    """Remove a row. The event's updated_at moves too, and updated_by when `user` is given (routes always give it)."""
    _check_kind(kind)
    row = _row(con, kind, row_id)
    with _atomic(con):
        con.execute(f"DELETE FROM {_TABLE[kind]} WHERE id = ?", (row_id,))
        _touch(con, user, row["event_id"])


# ---------------------------------------------------------------- doctors on an event
def link_doctor(con, event_id: int, doctor_id: int, role=None, user: dict | None = None) -> None:
    """Link a doctor to an event, or change the role of an existing link. A retired doctor can still be linked (the
    importer matches them by name). 404 for a missing event or doctor."""
    role = common.text({"role": role}, "role", max_len=100)
    _require_event(con, event_id)
    if con.execute("SELECT 1 FROM doctors WHERE id = ?", (doctor_id,)).fetchone() is None:
        raise ApiError(404, "No such doctor.")
    with _atomic(con):
        con.execute("INSERT INTO event_doctors (event_id, doctor_id, role) VALUES (?,?,?) "
                    "ON CONFLICT (event_id, doctor_id) DO UPDATE SET role = excluded.role",
                    (event_id, doctor_id, role))
        _touch(con, user, event_id)


def unlink_doctor(con, event_id: int, doctor_id: int, user: dict | None = None) -> None:
    with _atomic(con):
        if con.execute("DELETE FROM event_doctors WHERE event_id = ? AND doctor_id = ?",
                       (event_id, doctor_id)).rowcount == 0:
            raise ApiError(404, "That doctor is not on this event.")
        _touch(con, user, event_id)


# ---------------------------------------------------------------- reading
def _ref(row_id, label):
    return None if row_id is None else {"id": row_id, "label": label}


def _nights(from_date, to_date, counts):
    if not counts or not to_date:
        return None
    return (date.fromisoformat(to_date) - date.fromisoformat(from_date)).days


def for_event(con, event_id: int) -> dict:
    """Everything that hangs off the event, in date order, as plain dicts:
    stones [{id, size_tenths, size_mm, side, location, type, type_from_lab, from_import}],
    visits [{id, kind, from_date, to_date, nights, from_import}],
    procedures [{id, kind, on_date, doctor, note, from_import}],
    complications [{id, kind, on_date, note, from_import}],
    followups [{id, on_date, what, doctor, done, from_import}],
    doctors [{id, name, specialty, practice, retired, role}].
    `location`, `type` and `kind` are {id, label}; `doctor` is {id, name} or None; `nights` is to - from for a visit
    kind that counts nights and has an end date, else None."""
    def doctor(row):
        return None if row["doctor_id"] is None else {"id": row["doctor_id"], "name": row["doctor_name"]}

    stones = [{"id": r["id"], "size_tenths": r["size_tenths"], "size_mm": _size_mm(r["size_tenths"]),
               "side": r["side"], "location": _ref(r["location_id"], r["location_label"]),
               "type": _ref(r["type_id"], r["type_label"]), "type_from_lab": bool(r["type_from_lab"]),
               "from_import": r["suggestion_id"] is not None}
              for r in con.execute(
                  "SELECT s.*, l.label AS location_label, t.label AS type_label FROM stones s "
                  "LEFT JOIN lists l ON l.id = s.location_id LEFT JOIN lists t ON t.id = s.type_id "
                  "WHERE s.event_id = ? ORDER BY s.id", (event_id,))]
    visits = [{"id": r["id"], "kind": _ref(r["kind_id"], r["kind_label"]), "from_date": r["from_date"],
               "to_date": r["to_date"], "nights": _nights(r["from_date"], r["to_date"], r["counts_nights"]),
               "from_import": r["suggestion_id"] is not None}
              for r in con.execute(
                  "SELECT v.*, k.label AS kind_label, k.counts_nights FROM visits v JOIN lists k ON k.id = v.kind_id "
                  "WHERE v.event_id = ? ORDER BY v.from_date, v.id", (event_id,))]
    procedures = [{"id": r["id"], "kind": _ref(r["kind_id"], r["kind_label"]), "on_date": r["on_date"],
                   "doctor": doctor(r), "note": r["note"], "from_import": r["suggestion_id"] is not None}
                  for r in con.execute(
                      "SELECT p.*, k.label AS kind_label, d.name AS doctor_name FROM procedures p "
                      "JOIN lists k ON k.id = p.kind_id LEFT JOIN doctors d ON d.id = p.doctor_id "
                      "WHERE p.event_id = ? ORDER BY p.on_date IS NULL, p.on_date, p.id", (event_id,))]
    complications = [{"id": r["id"], "kind": _ref(r["kind_id"], r["kind_label"]), "on_date": r["on_date"],
                      "note": r["note"], "from_import": r["suggestion_id"] is not None}
                     for r in con.execute(
                         "SELECT c.*, k.label AS kind_label FROM complications c JOIN lists k ON k.id = c.kind_id "
                         "WHERE c.event_id = ? ORDER BY c.on_date IS NULL, c.on_date, c.id", (event_id,))]
    followups = [{"id": r["id"], "on_date": r["on_date"], "what": r["what"], "doctor": doctor(r),
                  "done": bool(r["done"]), "from_import": r["suggestion_id"] is not None}
                 for r in con.execute(
                     "SELECT f.*, d.name AS doctor_name FROM followups f "
                     "LEFT JOIN doctors d ON d.id = f.doctor_id "
                     "WHERE f.event_id = ? ORDER BY f.on_date IS NULL, f.on_date, f.id", (event_id,))]
    doctors = [{"id": r["id"], "name": r["name"], "specialty": r["specialty"], "practice": r["practice"],
                "retired": bool(r["retired"]), "role": r["role"]}
               for r in con.execute(
                   "SELECT d.*, ed.role FROM event_doctors ed JOIN doctors d ON d.id = ed.doctor_id "
                   "WHERE ed.event_id = ? ORDER BY d.name COLLATE NOCASE, d.id", (event_id,))]
    return {"stones": stones, "visits": visits, "procedures": procedures, "complications": complications,
            "followups": followups, "doctors": doctors}
