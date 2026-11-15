"""Sickbay: Flask + SQLite. This module holds the Flask object, the guards, the security headers and every route,
in one section per task; route-free logic lives in sibling modules that take a sqlite3.Connection.

Each task adds its routes under its own `# --- Task N` heading below and never edits another task's section."""
import logging
import os
from urllib.parse import urlparse

from flask import Flask, g, jsonify, request
from werkzeug.exceptions import HTTPException

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


# --- Task 3: events and notes
import common  # noqa: E402  (imported here so each task's lines stay in its own section)
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


# --- Task 5: files


# --- Task 6: figures


# --- Task 7: C-CDA reader (no routes)


# --- Task 8: import and suggestions


# --- Task 9: installer, backup and deployment notes (no routes)


# --- Task 10: frontend: shell, home and event page


# --- Task 11: frontend: timeline, doctor summary, settings


# --- Task 12: frontend: import, inbox and suggestions


# --- Task 13: check the importer against a real download (no routes)


# --- Task 14: deploy (no routes)


init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=settings.PORT)
