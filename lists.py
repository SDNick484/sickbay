"""The editable pick lists (outcome, location, stone type and the kinds) and the shared doctor list.

Lists are data, not code: seeded items carry a fixed `slug` that code finds them by; the API never sets or changes
a slug. An item is retired, never deleted, so an old event keeps its value. No routes here: app.py calls these with
a connection."""
import sqlite3

from common import ApiError, text

KINDS = ("outcome", "location", "stone_type", "visit_kind", "procedure_kind", "complication_kind")
LABEL_MAX = 100
SORT_MAX = 100_000
# What a doctor row may be deleted over: anything that refers to it. Retire the doctor instead.
_DOCTOR_REFS = (("event_doctors", "doctor_id"), ("procedures", "doctor_id"), ("followups", "doctor_id"))


def _item(row) -> dict:
    return {"id": row["id"], "slug": row["slug"], "label": row["label"], "sort_order": row["sort_order"],
            "retired": bool(row["retired"]), "counts_nights": bool(row["counts_nights"])}


def all(con) -> dict[str, list[dict]]:
    """Every item of every kind, retired ones included (flagged), by sort_order then id."""
    out = {kind: [] for kind in KINDS}
    for row in con.execute("SELECT * FROM lists ORDER BY sort_order, id"):
        out[row["kind"]].append(_item(row))
    return out


def _flag(data: dict, key: str) -> int:
    value = data[key]
    if not isinstance(value, bool):
        raise ApiError(400, f"{key} must be true or false.")
    return int(value)


def add(con, kind: str, label) -> int:
    """A new item at the end of its kind. Unknown kind is 404; a blank label is 400; a label already in the kind
    (retired ones too, since the pair is unique) is 409."""
    if kind not in KINDS:
        raise ApiError(404, "No such list.")
    label = text({"label": label}, "label", required=True, max_len=LABEL_MAX)
    try:
        cur = con.execute(
            "INSERT INTO lists (kind, label, sort_order) "
            "VALUES (?, ?, (SELECT COALESCE(MAX(sort_order), 0) + 1 FROM lists WHERE kind = ?))",
            (kind, label, kind))
    except sqlite3.IntegrityError:
        raise ApiError(409, "That is already on the list.") from None
    return cur.lastrowid


def update(con, item_id: int, data: dict) -> None:
    """Change label, retired, sort_order, and counts_nights (visit kinds only). Keys not given stay as they are;
    slug and kind are never changed here."""
    row = con.execute("SELECT kind FROM lists WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        raise ApiError(404, "No such list item.")
    sets: dict[str, object] = {}
    if "label" in data:
        sets["label"] = text(data, "label", required=True, max_len=LABEL_MAX)
    if "retired" in data:
        sets["retired"] = _flag(data, "retired")
    if "sort_order" in data:
        order = data["sort_order"]
        if isinstance(order, bool) or not isinstance(order, int) or not -SORT_MAX <= order <= SORT_MAX:
            raise ApiError(400, "sort_order must be a whole number.")
        sets["sort_order"] = order
    if "counts_nights" in data:
        if row["kind"] != "visit_kind":
            raise ApiError(400, "Only a kind of care visit can count hospital nights.")
        sets["counts_nights"] = _flag(data, "counts_nights")
    if not sets:
        return
    try:
        con.execute(f"UPDATE lists SET {', '.join(f'{c} = ?' for c in sets)} WHERE id = ?",
                    (*sets.values(), item_id))
    except sqlite3.IntegrityError:
        raise ApiError(409, "That is already on the list.") from None


# ---------------------------------------------------------------- doctors
def _doctor(row) -> dict:
    return {"id": row["id"], "name": row["name"], "specialty": row["specialty"], "practice": row["practice"],
            "retired": bool(row["retired"])}


def doctors(con, include_retired: bool = False) -> list[dict]:
    sql = "SELECT * FROM doctors" + ("" if include_retired else " WHERE retired = 0") + " ORDER BY name COLLATE NOCASE, id"
    return [_doctor(r) for r in con.execute(sql)]


def _fields(data: dict, *, creating: bool) -> dict:
    out: dict[str, object] = {}
    if creating or "name" in data:
        out["name"] = text(data, "name", required=True, max_len=200)
    for key in ("specialty", "practice"):
        if key in data:
            out[key] = text(data, key, max_len=200)
    if "retired" in data:
        out["retired"] = _flag(data, "retired")
    return out


def add_doctor(con, data: dict) -> int:
    fields = _fields(data, creating=True)
    cols = list(fields)
    cur = con.execute(f"INSERT INTO doctors ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                      tuple(fields.values()))
    return cur.lastrowid


def _require_doctor(con, doctor_id: int) -> None:
    if con.execute("SELECT 1 FROM doctors WHERE id = ?", (doctor_id,)).fetchone() is None:
        raise ApiError(404, "No such doctor.")


def update_doctor(con, doctor_id: int, data: dict) -> None:
    _require_doctor(con, doctor_id)
    fields = _fields(data, creating=False)
    if fields:
        con.execute(f"UPDATE doctors SET {', '.join(f'{c} = ?' for c in fields)} WHERE id = ?",
                    (*fields.values(), doctor_id))


def delete_doctor(con, doctor_id: int) -> None:
    _require_doctor(con, doctor_id)
    for table, column in _DOCTOR_REFS:
        if con.execute(f"SELECT 1 FROM {table} WHERE {column} = ? LIMIT 1", (doctor_id,)).fetchone():
            raise ApiError(409, "This doctor is on an event. Retire them instead.")
    con.execute("DELETE FROM doctors WHERE id = ?", (doctor_id,))


def _norm(name: str) -> str:
    return " ".join(name.split()).casefold()


def find_doctor(con, name) -> int | None:
    """The id of the doctor whose name equals `name` ignoring case and runs of spaces; retired doctors count (a
    retired doctor must not be created a second time). None when nothing matches."""
    if not isinstance(name, str) or not _norm(name):
        return None
    wanted = _norm(name)
    for row in con.execute("SELECT id, name FROM doctors ORDER BY id"):
        if _norm(row["name"]) == wanted:
            return row["id"]
    return None
