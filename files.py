"""Files: storage by random id, kind sniffing, links to events, the inbox, and the memory of deleted documents.
No routes here (they are in app.py's Task 5 section).

A file's bytes live at FILES_DIR/<storage_id>, a random 32-hex-digit name; the uploaded name is kept only in the
database, for display. The kind is taken from the content (sniff()), never from the name or the type the browser
claims, and only PDF, PNG, JPEG, XML and HTML are accepted.

Blobs follow the database: store() and delete() run inside `files.atomic(con)`, a db.transaction that also owns the
blobs written in it. On rollback the blobs it wrote are removed; on commit the blobs it replaced or deleted are. A
caller that stores several files in one transaction (the importer) wraps the whole run in `with files.atomic(con):`
instead of `db.transaction(con)`; store() inside a bare db.transaction is refused, since its blobs would outlive a
rollback."""
import contextvars
import hashlib
import os
import re
import secrets
import tempfile
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass

import common
import db
import settings
from common import ApiError

MAX_NAME = 200
INLINE = {"application/pdf", "image/png", "image/jpeg"}  # served inline; everything else (XML, HTML) is a download
_STORAGE_ID = re.compile(r"[0-9a-f]{32}")
_BOM = b"\xef\xbb\xbf"


class NotAccepted(ApiError):
    """The content is not one of the accepted kinds. An ApiError, so a route answers 415 by itself; the importer can
    catch it to count a document as failed."""

    def __init__(self):
        super().__init__(415, "That kind of file is not accepted.")


@dataclass(frozen=True)
class Stored:
    """What store() did: `new`, `same` (already held, by sha256, doc id or set version), `updated` (a newer version
    of a held set replaced its content in place) or `deleted` (a user deleted it before; nothing stored, id None)."""
    id: int | None
    status: str


# ---------------------------------------------------------------- kinds and names
def sniff(data: bytes) -> str | None:
    """The kind of `data` from its first bytes: application/pdf, image/png, image/jpeg, application/xml (after an
    optional BOM and whitespace, `<?xml` or `<ClinicalDocument`), text/html (likewise, `<!doctype html` or `<html`,
    any case). Anything else, SVG and GIF included, is None."""
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    head = data[:512].removeprefix(_BOM).lstrip()
    if head.startswith((b"<?xml", b"<ClinicalDocument")):
        return "application/xml"
    lower = head.lower()
    if lower.startswith(b"<!doctype html") or lower.startswith(b"<html"):
        return "text/html"
    return None


def safe_name(name) -> str:
    """A display name from an uploaded or archive member name: the last path segment (either slash), without control,
    format (bidi overrides, zero-width) or line-separator characters, trimmed, at most 200 characters; "file" when
    nothing is left, or only dots."""
    if not isinstance(name, str):
        return "file"
    name = re.split(r"[/\\]", name)[-1]
    name = "".join(c for c in name if unicodedata.category(c) not in {"Cc", "Cf", "Cs", "Zl", "Zp"}).strip()
    name = name[:MAX_NAME].strip()
    return name if name.strip(".") else "file"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- blobs and the transaction that owns them
class _Scope:
    def __init__(self, con):
        self.con = con
        self.written: list[str] = []     # removed if the transaction rolls back
        self.superseded: list[str] = []  # removed once it commits


_scope: contextvars.ContextVar[_Scope | None] = contextvars.ContextVar("files_scope", default=None)


def _path(storage_id: str) -> str:
    if not _STORAGE_ID.fullmatch(storage_id or ""):
        raise ValueError("not a storage id")  # never build a path from anything else
    return os.path.join(settings.FILES_DIR, storage_id)


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


@contextmanager
def atomic(con):
    """db.transaction(con) that also owns the blobs written in it: on rollback the new blobs are removed, on commit
    the replaced and deleted ones. Re-entrant on the same connection (an inner call joins the outer one)."""
    current = _scope.get()
    if current is not None and current.con is con:
        yield con
        return
    scope = _Scope(con)
    token = _scope.set(scope)
    try:
        with db.transaction(con):
            yield con
    except BaseException:
        for path in scope.written:
            _remove(path)
        raise
    else:
        for path in scope.superseded:
            _remove(path)
    finally:
        _scope.reset(token)


@contextmanager
def _own(con):
    """The current files.atomic scope for `con`, opening one when no transaction is open. Inside a bare
    db.transaction it refuses: blobs written there would outlive a rollback."""
    current = _scope.get()
    if current is not None and current.con is con:
        yield current
    elif con.in_transaction:
        raise RuntimeError("files.store and files.delete run inside files.atomic(con), not a bare db.transaction")
    else:
        with atomic(con):
            yield _scope.get()


def _write(scope: _Scope, data: bytes) -> str:
    """Write `data` to a new random blob (a temp file in FILES_DIR, then a rename) and return its storage id."""
    os.makedirs(settings.FILES_DIR, exist_ok=True)
    storage_id = secrets.token_hex(16)
    final = _path(storage_id)
    fd, tmp = tempfile.mkstemp(dir=settings.FILES_DIR, prefix=".upload-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, final)
    except BaseException:
        _remove(tmp)
        raise
    scope.written.append(final)
    return storage_id


@contextmanager
def _blob(scope: _Scope, data: bytes):
    """A new blob for the row written in the block. If that write fails, the blob goes at once: a caller that catches
    the error and carries on (the importer, one document at a time) would otherwise commit an orphan."""
    storage_id = _write(scope, data)
    try:
        yield storage_id
    except BaseException:
        _remove(_path(storage_id))
        raise


# ---------------------------------------------------------------- storing and deleting
def _deleted(con, keys: list[str]) -> bool:
    marks = ",".join("?" * len(keys))
    return con.execute(f"SELECT 1 FROM deleted_documents WHERE key IN ({marks})", keys).fetchone() is not None


def store(con, user: dict, name, data: bytes, *, origin="upload", doc_id=None, set_id=None, version=None) -> Stored:
    """Keep `data` as a file, or find the file that already holds it. In order:
    - a key in deleted_documents (`sha:<hex>`, `doc:<id>`, `set:<id>`) -> `deleted`, nothing stored. A file a person
      uploads by hand (origin "upload") is their choice: its `sha:` key is forgotten and it is stored as usual;
    - the same sha256, or the same doc_id -> `same`, the held file's id;
    - the same set_id with a higher version -> content, size, kind, sha256, doc_id and version replaced in place,
      keeping the id, name and links -> `updated`; with a lower or equal version (or none) -> `same`;
    - else a new blob and row -> `new`.
    The kind comes from sniff(); anything not accepted raises NotAccepted (415) and writes nothing."""
    mime = sniff(data)
    if mime is None:
        raise NotAccepted()
    digest = sha256(data)
    keys = [f"sha:{digest}"] + ([f"doc:{doc_id}"] if doc_id else []) + ([f"set:{set_id}"] if set_id else [])
    with _own(con) as scope:
        if origin == "upload":
            con.execute("DELETE FROM deleted_documents WHERE key = ?", (keys[0],))
        if _deleted(con, keys):
            return Stored(None, "deleted")
        row = con.execute("SELECT id FROM files WHERE sha256 = ? OR doc_id = ?", (digest, doc_id)).fetchone()
        if row:
            return Stored(row["id"], "same")
        if set_id:
            row = con.execute("SELECT id, storage_id, version FROM files WHERE set_id = ? "
                              "ORDER BY version DESC, id DESC LIMIT 1", (set_id,)).fetchone()
            if row:
                if version is None or (row["version"] is not None and version <= row["version"]):
                    return Stored(row["id"], "same")
                with _blob(scope, data) as storage_id:
                    con.execute("UPDATE files SET storage_id = ?, mime = ?, size = ?, sha256 = ?, doc_id = ?, "
                                "version = ? WHERE id = ?", (storage_id, mime, len(data), digest, doc_id, version, row["id"]))
                scope.superseded.append(_path(row["storage_id"]))
                return Stored(row["id"], "updated")
        with _blob(scope, data) as storage_id:
            file_id = con.execute(
                "INSERT INTO files (storage_id, name, mime, size, sha256, doc_id, set_id, version, origin, created_by, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (storage_id, safe_name(name), mime, len(data), digest, doc_id, set_id, version, origin, user["email"],
                 common.now())).lastrowid
        return Stored(file_id, "new")


def delete(con, file_id: int) -> None:
    """Delete a file and its links, and remember its identity (`sha:`, `doc:`, `set:` keys, no content) so an import
    does not bring it back. The blob goes once the transaction commits. 404 when there is no such file."""
    with _own(con) as scope:
        row = _row(con, file_id)
        t = common.now()
        keys = [f"sha:{row['sha256']}"] + ([f"doc:{row['doc_id']}"] if row["doc_id"] else []) + \
               ([f"set:{row['set_id']}"] if row["set_id"] else [])
        for key in keys:
            con.execute("INSERT INTO deleted_documents (key, deleted_at) VALUES (?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET deleted_at = excluded.deleted_at", (key, t))
        con.execute("DELETE FROM files WHERE id = ?", (file_id,))
        scope.superseded.append(_path(row["storage_id"]))


# ---------------------------------------------------------------- reading, links and the inbox
def _row(con, file_id):
    row = con.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
    if row is None:
        raise ApiError(404, "No such file.")
    return row


def need_event(con, event_id: int) -> None:
    """404 "No such event." unless the event exists."""
    if con.execute("SELECT 1 FROM events WHERE id = ?", (event_id,)).fetchone() is None:
        raise ApiError(404, "No such event.")


_META = ("SELECT f.id, f.name, f.mime, f.size, f.origin, f.created_at, "
         "(SELECT group_concat(event_id) FROM (SELECT event_id FROM event_files WHERE file_id = f.id ORDER BY event_id)) "
         "AS event_ids FROM files f ")


def _meta(row) -> dict:
    out = {k: row[k] for k in ("id", "name", "mime", "size", "origin", "created_at")}
    out["event_ids"] = [int(e) for e in row["event_ids"].split(",")] if row["event_ids"] else []
    return out


def meta(con, file_id: int) -> dict:
    """{id, name, mime, size, origin, created_at, event_ids}; 404 "No such file." when missing."""
    row = con.execute(_META + "WHERE f.id = ?", (file_id,)).fetchone()
    if row is None:
        raise ApiError(404, "No such file.")
    return _meta(row)


def for_event(con, event_id: int) -> list[dict]:
    """The files linked to an event, as meta() dicts, in the order they were added to the app."""
    rows = con.execute(_META + "JOIN event_files ef ON ef.file_id = f.id WHERE ef.event_id = ? "
                       "ORDER BY f.created_at, f.id", (event_id,))
    return [_meta(r) for r in rows]


def inbox(con) -> list[dict]:
    """Files linked to no event, newest first."""
    rows = con.execute(_META + "WHERE NOT EXISTS (SELECT 1 FROM event_files ef WHERE ef.file_id = f.id) "
                       "ORDER BY f.created_at DESC, f.id DESC")
    return [_meta(r) for r in rows]


def link(con, event_id: int, file_id: int) -> None:
    """Attach a file to an event; attaching it twice is a no-op. 404 for a missing event or file."""
    need_event(con, event_id)
    _row(con, file_id)
    con.execute("INSERT OR IGNORE INTO event_files (event_id, file_id) VALUES (?, ?)", (event_id, file_id))


def unlink(con, event_id: int, file_id: int) -> None:
    """Take a file off an event (it stays stored; with no event left it is back in the inbox). 404 for a missing
    event or file; a file that was not on the event is a no-op."""
    need_event(con, event_id)
    _row(con, file_id)
    con.execute("DELETE FROM event_files WHERE event_id = ? AND file_id = ?", (event_id, file_id))


def is_linked(con, file_id: int) -> bool:
    return con.execute("SELECT 1 FROM event_files WHERE file_id = ?", (file_id,)).fetchone() is not None


def content(con, file_id: int) -> tuple[dict, bytes]:
    """(meta, bytes) of a file; 404 "No such file." when the row or its blob is missing."""
    row = _row(con, file_id)
    try:
        with open(_path(row["storage_id"]), "rb") as f:
            data = f.read()
    except FileNotFoundError:
        raise ApiError(404, "No such file.") from None
    return meta(con, file_id), data
