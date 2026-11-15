"""Sickbay: Flask + SQLite. This module holds the Flask object, the guards, the security headers and every route,
in one section per task; route-free logic lives in sibling modules that take a sqlite3.Connection.

Each task adds its routes under its own `# --- Task N` heading below and never edits another task's section."""
import logging
import os
from urllib.parse import urlparse

from flask import Flask, g, jsonify, request
from werkzeug.exceptions import HTTPException

import common
import db as database
import identity
import settings
from common import ApiError

log = logging.getLogger("sickbay")


class Sickbay(Flask):
    def log_exception(self, exc_info):
        """An unhandled exception is logged as its route and class only: its message or traceback could hold a
        value someone typed or a line of a document."""
        log.error("%s %s failed: %s", request.method, _rule(), exc_info[0].__name__ if exc_info[0] else "error")


app = Sickbay(__name__)
app.json.sort_keys = False
app.config["MAX_CONTENT_LENGTH"] = settings.MAX_UPLOAD_MB * 1024 * 1024  # uploads are the largest bodies

OPEN_PATHS = {"/healthz"}       # the only path that answers without an identity
SAFE_METHODS = {"GET", "HEAD"}  # every other method is a mutation and needs the CSRF checks
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; "
       "manifest-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


def init_db():
    """Create DATA_DIR and FILES_DIR and bring the database up to the current schema. Runs at import, so at every
    start."""
    os.makedirs(settings.FILES_DIR, exist_ok=True)
    con = database.connect(settings.DB_PATH)
    try:
        database.migrate(con)
    finally:
        con.close()


def db():
    """This request's connection, opened on first use and closed at teardown."""
    if "db" not in g:
        g.db = database.connect(settings.DB_PATH)
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    con = g.pop("db", None)
    if con is not None:
        con.close()


def _rule():
    """The matched route's rule (`/api/events/<int:event_id>`), never the path or query a client sent; "-" when no
    route matched."""
    return request.url_rule.rule if request.url_rule is not None else "-"


# ---------------------------------------------------------------- guards
@app.before_request
def guard():
    """Identity on every path but /healthz (static files included, and paths that match no route), then the CSRF
    checks on every mutation: X-Sickbay: 1 (a cross-site page cannot send it without a preflight we never allow), an
    Origin naming this host, and a JSON body, or multipart/form-data for a view marked with @common.multipart."""
    if request.path in OPEN_PATHS:
        return
    user = identity.resolve_user(request.headers, request.remote_addr or "")
    if user is None:
        raise ApiError(403, "Forbidden")
    g.user = user
    if request.method in SAFE_METHODS:
        return
    view = app.view_functions.get(request.endpoint) if request.endpoint else None
    body_ok = request.mimetype == "multipart/form-data" if getattr(view, "multipart", False) else request.is_json
    if request.headers.get("X-Sickbay") != "1" or not body_ok:
        raise ApiError(400, "Bad request.")
    origin = request.headers.get("Origin") or ""
    # netloc only: behind the tunnel the app sees http while the browser's Origin says https.
    if urlparse(origin).netloc.lower() != request.host.lower():
        raise ApiError(403, "Cross-origin request blocked.")


@app.after_request
def security_headers(resp):
    """On every response, refusals included. A view may set a stricter Content-Security-Policy of its own (the file
    content route's sandbox); everything else is fixed here."""
    resp.headers.setdefault("Content-Security-Policy", CSP)
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    return resp


@app.after_request
def log_request(resp):
    """One line per request: method, route rule and status. Never the path, the query, a body or a user."""
    log.info("%s %s %s", request.method, _rule(), resp.status_code)
    return resp


@app.errorhandler(ApiError)
def api_error(e):
    return jsonify(error=e.message, **e.extra), e.status


@app.errorhandler(413)
def too_large(_e):
    return jsonify(error="That file is too large."), 413


@app.errorhandler(HTTPException)
def http_error(e):
    return jsonify(error=e.name), e.code


# ---------------------------------------------------------------- routes
# --- Task 1: scaffold
@app.get("/healthz")
def healthz():
    return app.response_class("ok", mimetype="text/plain")


@app.get("/api/me")
def me():
    return jsonify(email=g.user["email"], name=g.user["name"], app_name=settings.APP_NAME)


# --- Task 2: lists and doctors
import lists
from common import json_body


@app.get("/api/lists")
def get_lists():
    return jsonify(lists.all(db()))


@app.post("/api/lists/<kind>")
def add_list_item(kind):
    item_id = lists.add(db(), kind, json_body().get("label"))
    return jsonify(id=item_id), 201


@app.patch("/api/lists/items/<int:item_id>")
def update_list_item(item_id):
    lists.update(db(), item_id, json_body())
    return jsonify(ok=True)


@app.get("/api/doctors")
def get_doctors():
    return jsonify(lists.doctors(db(), include_retired=request.args.get("all") == "1"))


@app.post("/api/doctors")
def add_doctor():
    return jsonify(id=lists.add_doctor(db(), json_body())), 201


@app.patch("/api/doctors/<int:doctor_id>")
def update_doctor(doctor_id):
    lists.update_doctor(db(), doctor_id, json_body())
    return jsonify(ok=True)


@app.delete("/api/doctors/<int:doctor_id>")
def delete_doctor(doctor_id):
    lists.delete_doctor(db(), doctor_id)
    return jsonify(ok=True)


# --- Task 3: events and notes
import events  # noqa: E402


@app.get("/api/events")
def events_list():
    return jsonify(events.list_all(db()))


@app.post("/api/events")
def events_create():
    con = db()
    event_id = events.create(con, g.user, common.json_body())
    return jsonify(events.get(con, event_id)), 201


@app.get("/api/events/<int:event_id>")
def events_get(event_id):
    return jsonify(events.get(db(), event_id))


@app.patch("/api/events/<int:event_id>")
def events_update(event_id):
    con = db()
    events.update(con, g.user, event_id, common.json_body())
    return jsonify(events.get(con, event_id))


@app.delete("/api/events/<int:event_id>")
def events_delete(event_id):
    events.delete(db(), event_id)
    return "", 204


@app.post("/api/events/<int:event_id>/notes")
def notes_add(event_id):
    con = db()
    events.add_note(con, g.user, event_id, common.json_body().get("body"))
    return jsonify(events.get(con, event_id)), 201


def _note_event_id(con, note_id):
    row = con.execute("SELECT event_id FROM notes WHERE id = ?", (note_id,)).fetchone()
    if row is None:
        raise ApiError(404, "No such note.")
    return row["event_id"]


@app.patch("/api/notes/<int:note_id>")
def notes_update(note_id):
    con = db()
    event_id = _note_event_id(con, note_id)
    events.update_note(con, g.user, note_id, common.json_body().get("body"))
    return jsonify(events.get(con, event_id))


@app.delete("/api/notes/<int:note_id>")
def notes_delete(note_id):
    events.delete_note(db(), note_id)
    return "", 204


# --- Task 4: event details
import details  # noqa: E402


@app.post("/api/events/<int:event_id>/<kind>")
def details_add(event_id, kind):
    con = db()
    details.add(con, g.user, event_id, kind, common.json_body())
    return jsonify(events.get(con, event_id)), 201


@app.patch("/api/<kind>/<int:row_id>")
def details_update(kind, row_id):
    con = db()
    event_id = details.event_id_of(con, kind, row_id)
    details.update(con, g.user, kind, row_id, common.json_body())
    return jsonify(events.get(con, event_id))


@app.delete("/api/<kind>/<int:row_id>")
def details_delete(kind, row_id):
    con = db()
    details.event_id_of(con, kind, row_id)
    details.delete(con, kind, row_id, g.user)
    return "", 204


@app.put("/api/events/<int:event_id>/doctors/<int:doctor_id>")
def event_doctor_put(event_id, doctor_id):
    con = db()
    details.link_doctor(con, event_id, doctor_id, common.json_body().get("role"), g.user)
    return jsonify(events.get(con, event_id))


@app.delete("/api/events/<int:event_id>/doctors/<int:doctor_id>")
def event_doctor_delete(event_id, doctor_id):
    details.unlink_doctor(db(), event_id, doctor_id, g.user)
    return "", 204



# --- Task 5: files
# Imports sit in this section, not at the top, so parallel tasks' import lines do not collide (E402 is off).
from urllib.parse import quote

import files

FILE_CSP = "sandbox; default-src 'none'"  # an uploaded document never runs as a page on this origin


def _uploaded():
    """The bytes and name of the multipart field `file`; 400 when it is missing or empty."""
    f = request.files.get("file")
    if f is None:
        raise ApiError(400, "Choose a file to upload.")
    data = f.read()
    if not data:
        raise ApiError(400, "That file is empty.")
    return f.filename, data


def _stored_reply(con, stored):
    log.info("file %d %s", stored.id, stored.status)  # the id only: never a file's name or content
    return jsonify(**files.meta(con, stored.id), status=stored.status), 201 if stored.status == "new" else 200


@app.post("/api/files")
@common.multipart
def upload_file():
    name, data = _uploaded()
    return _stored_reply(db(), files.store(db(), g.user, name, data))


@app.post("/api/events/<int:event_id>/files")
@common.multipart
def upload_event_file(event_id):
    con = db()
    files.need_event(con, event_id)
    name, data = _uploaded()
    with files.atomic(con):
        stored = files.store(con, g.user, name, data)
        files.link(con, event_id, stored.id)
    return _stored_reply(con, stored)


@app.get("/api/events/<int:event_id>/files")
def event_files(event_id):
    files.need_event(db(), event_id)
    return jsonify(files.for_event(db(), event_id))


@app.get("/api/files/inbox")
def files_inbox():
    return jsonify(files.inbox(db()))


@app.get("/api/files/<int:file_id>")
def file_meta(file_id):
    return jsonify(files.meta(db(), file_id))


def _disposition(kind, name):
    """`kind; filename="<ascii fallback>"; filename*=UTF-8''<RFC 5987>`. safe_name already dropped control
    characters; the fallback also replaces non-ASCII, quotes and backslashes."""
    fallback = "".join(c if " " <= c <= "~" and c not in '"\\' else "_" for c in name)
    return f"{kind}; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"


@app.get("/api/files/<int:file_id>/content")
def file_content(file_id):
    """The stored bytes under the stored kind (sniffed at upload, never the browser's claim). PDF and images inline,
    XML and HTML as a download; every response sandboxed (no script, an opaque origin, nothing loads), nosniff and
    no-store (both also set app-wide), and not embeddable by another site."""
    meta, data = files.content(db(), file_id)
    resp = app.response_class(data, content_type=meta["mime"])
    kind = "inline" if meta["mime"] in files.INLINE else "attachment"
    resp.headers["Content-Disposition"] = _disposition(kind, meta["name"])
    resp.headers["Content-Security-Policy"] = FILE_CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.put("/api/events/<int:event_id>/files/<int:file_id>")
def link_file(event_id, file_id):
    files.link(db(), event_id, file_id)
    log.info("file %d linked", file_id)
    return jsonify(files.meta(db(), file_id))


@app.delete("/api/events/<int:event_id>/files/<int:file_id>")
def unlink_file(event_id, file_id):
    files.unlink(db(), event_id, file_id)
    log.info("file %d unlinked", file_id)
    return jsonify(files.meta(db(), file_id))


@app.delete("/api/files/<int:file_id>")
def delete_file(file_id):
    con = db()
    with files.atomic(con):
        files.meta(con, file_id)  # 404 before 409
        if files.is_linked(con, file_id):
            raise ApiError(409, "This file is on an event. Take it off the event first.")
        files.delete(con, file_id)
    log.info("file %d deleted", file_id)
    return jsonify(ok=True)


# --- Task 6: figures
import figures  # noqa: E402


@app.get("/api/timeline")
def api_timeline():
    return jsonify(figures.timeline(db(), common.today()))


@app.get("/api/summary")
def api_summary():
    return jsonify(figures.summary(db(), common.today()))


# --- Task 7: C-CDA reader (no routes)


# --- Task 8: import and suggestions
import ccda  # noqa: E402
import imports  # noqa: E402


@app.post("/api/imports")
@common.multipart
def import_download():
    """Import a portal download (multipart `file`): 201 with the run's counts, 400 when the whole upload is refused."""
    name, data = _uploaded()
    try:
        counts = imports.run(db(), g.user, name, data)
    except ccda.Refused as e:
        raise ApiError(400, e.message) from None
    return jsonify(counts), 201


@app.get("/api/suggestions")
def suggestions_pending():
    return jsonify(imports.pending(db(), common.today()))


@app.get("/api/events/<int:event_id>/suggestions")
def event_suggestions(event_id):
    return jsonify(imports.for_event(db(), event_id, common.today()))


@app.post("/api/suggestions/<int:suggestion_id>/accept")
def suggestion_accept(suggestion_id):
    con = db()
    event_id = imports.parse_event_id(common.json_body().get("event_id"))
    s = imports.accept(con, g.user, suggestion_id, event_id)
    return jsonify(suggestion=s, event=events.get(con, s["event_id"]))


@app.post("/api/suggestions/<int:suggestion_id>/dismiss")
def suggestion_dismiss(suggestion_id):
    return jsonify(suggestion=imports.dismiss(db(), g.user, suggestion_id))


@app.patch("/api/suggestions/<int:suggestion_id>")
def suggestion_move(suggestion_id):
    data = common.json_body()
    if "event_id" not in data:
        raise ApiError(400, "Choose an event.")
    return jsonify(suggestion=imports.move(db(), suggestion_id, imports.parse_event_id(data["event_id"])))


@app.post("/api/suggestions/new-event")
def suggestions_new_event():
    con = db()
    event_id = imports.create_event(con, g.user, common.json_body().get("suggestion_ids"))
    return jsonify(events.get(con, event_id)), 201


# --- Task 9: installer, backup and deployment notes (no routes)


# --- Task 10: frontend: shell, home and event page
import html  # noqa: E402
import json  # noqa: E402


@app.get("/")
def index():
    """The single page, with APP_NAME as its title and its home-screen name (the only places the page names itself).
    Everything else is static/js. Read per request: the file is small and the setting is fixed per process."""
    with open(os.path.join(app.static_folder, "index.html"), encoding="utf-8") as f:
        page = f.read().replace("__APP_NAME__", html.escape(settings.APP_NAME, quote=True))
    return app.response_class(page, mimetype="text/html")


@app.get("/manifest.json")
def manifest():
    """static/manifest.json with name and short_name set to APP_NAME. Behind the identity guard like everything
    else, so index.html asks for it with crossorigin="use-credentials"."""
    with open(os.path.join(app.static_folder, "manifest.json"), encoding="utf-8") as f:
        data = {**json.load(f), "name": settings.APP_NAME, "short_name": settings.APP_NAME}
    return app.response_class(json.dumps(data), mimetype="application/manifest+json")


# --- Task 11: frontend: timeline, doctor summary, settings (no routes: the screens use Task 2's and Task 6's)


# --- Task 12: frontend: import, inbox and suggestions


# --- Task 13: check the importer against a real download (no routes)


# --- Task 14: deploy (no routes)


init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=settings.PORT)
