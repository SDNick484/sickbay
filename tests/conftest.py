"""Test setup. settings.py reads the environment once at import, so set it before importing the app, and give every
test a fresh database.

Shared by every task: each task adds its own fixtures and helpers under its own heading at the end of this file, and
never edits another task's section."""
import os
import re
import shutil
import sys
import tempfile
import time
import types

import pytest

DATA = tempfile.mkdtemp(prefix="sickbay-test-")
os.environ.update(
    DATA_DIR=DATA,
    ALLOWED_EMAILS="alex@example.com,sam@example.com",
    USERS="alex@example.com=Alex,sam@example.com=Sam",
    CF_TEAM_DOMAIN="example.cloudflareaccess.com",
    CF_AUD="aud-test",
    DEV_EMAIL="",  # never inherit a developer's shell setting: it would admit loopback requests
)
for name in ("SICKBAY_PORT", "APP_NAME", "MAX_UPLOAD_MB"):
    os.environ.pop(name, None)  # test the defaults
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jwt  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from flask import jsonify, request  # noqa: E402

import app as sickbay  # noqa: E402
import common  # noqa: E402
import db  # noqa: E402
import identity  # noqa: E402
import settings  # noqa: E402

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)

# Stand in for Cloudflare's JWKS endpoint: always hand back the test public key.
identity._jwk_client = types.SimpleNamespace(
    get_signing_key_from_jwt=lambda _t: types.SimpleNamespace(key=KEY.public_key()))

# An exception in a view is raised in the test instead of becoming a bare 500, so a failure shows its traceback.
# A test of the 500 path itself turns this off with monkeypatch.setitem(sickbay.app.config, "TESTING", False).
sickbay.app.testing = True

LAN_IP = "192.0.2.50"        # RFC 5737
ORIGIN = "http://localhost"  # the Flask test client's host
ALEX = {"email": "alex@example.com", "name": "Alex"}
SAM = {"email": "sam@example.com", "name": "Sam"}


# --- Task 1: scaffold
def token(email, key=KEY, aud="aud-test", iss="https://example.cloudflareaccess.com", exp=3600, **claims):
    """A Cloudflare Access JWT. exp is seconds from now; exp=None leaves the claim out."""
    payload = {"email": email, "aud": aud, "iss": iss, "iat": int(time.time()), **claims}
    if exp is not None:
        payload["exp"] = int(time.time()) + exp
    return jwt.encode(payload, key, algorithm="RS256")


def auth(email="alex@example.com", **bad):
    """Headers of a request that came through Cloudflare Access signed in as `email`. Keyword arguments forge a bad
    token: aud=, iss=, exp= (seconds from now), key= (another signing key)."""
    return {"Cf-Ray": "test", "Cf-Access-Authenticated-User-Email": email,
            "Cf-Access-Jwt-Assertion": token(email, **bad)}


def mut(email="alex@example.com", **bad):
    """auth() plus what every mutation needs: X-Sickbay: 1, a same-origin Origin and a JSON content type. For a
    multipart upload, drop the Content-Type (`{k: v for k, v in mut().items() if k != "Content-Type"}`) and let the
    test client set it from `data=`."""
    return {**auth(email, **bad), "X-Sickbay": "1", "Origin": ORIGIN, "Content-Type": "application/json"}


def list_id(con, kind, slug):
    """The id of a seeded list item, e.g. list_id(con, "visit_kind", "admission")."""
    row = con.execute("SELECT id FROM lists WHERE kind = ? AND slug = ?", (kind, slug)).fetchone()
    assert row is not None, f"no {kind} item {slug!r}"
    return row["id"]


_NAME = re.compile(r"[a-z_][a-z0-9_]*")


def mk_row(con, table, **cols):
    """INSERT one row straight into `table` (no API, no checks beyond the schema's); returns its rowid. Give every
    NOT NULL column without a default yourself."""
    assert _NAME.fullmatch(table) and all(_NAME.fullmatch(c) for c in cols), "table and column names only"
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
    return con.execute(sql, tuple(cols.values())).lastrowid


def mk_event(con, started_on, resolved_on=None, outcome=None):
    """An event inserted straight into the database, created by Alex; returns its id. `outcome` is an outcome
    slug ("passed", "removed", "other") or a list item id."""
    outcome_id = list_id(con, "outcome", outcome) if isinstance(outcome, str) else outcome
    t = common.now()
    return mk_row(con, "events", started_on=started_on, resolved_on=resolved_on, outcome_id=outcome_id,
                  created_by=ALEX["email"], created_at=t, updated_by=ALEX["email"], updated_at=t)


# Test-only routes, registered before any request (Flask refuses new routes after the first one). They sit behind the
# same guards as every real route, so the url_map walk in tests/test_identity.py covers them too.
@sickbay.app.post("/api/_test/echo")
def _test_echo():
    return jsonify(common.json_body())


@sickbay.app.post("/api/_test/upload")
@common.multipart
def _test_upload():
    f = request.files.get("file")
    return jsonify(size=len(f.read()) if f else None)


@sickbay.app.get("/api/_test/boom")
def _test_boom():
    raise ValueError("ZZ-SECRET-VALUE")  # an exception whose message must never reach a log line


@pytest.fixture(autouse=True)
def fresh_db():
    """Every test starts from an empty, migrated database and an empty files directory."""
    for name in os.listdir(DATA):
        path = os.path.join(DATA, name)
        shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
    sickbay.init_db()


@pytest.fixture
def client():
    return sickbay.app.test_client()


@pytest.fixture
def con():
    c = db.connect(settings.DB_PATH)
    yield c
    c.close()


@pytest.fixture
def clock(monkeypatch):
    """Freezes common.now() (seconds) at a fixed instant; clock.advance(n) moves it on. Only code that calls
    `common.now()` through the module sees it (not `from common import now`)."""
    state = types.SimpleNamespace(t=1_700_000_000)
    state.advance = lambda seconds: setattr(state, "t", state.t + seconds)
    monkeypatch.setattr(common, "now", lambda: state.t)
    return state


@pytest.fixture
def freeze_today(monkeypatch):
    """freeze_today("2024-03-01") makes common.today() return that date (through the module, as with clock)."""
    def freeze(day):
        monkeypatch.setattr(common, "today", lambda: day)
    return freeze


# The plain helpers above are importable (`from conftest import auth, mk_event`) and also fixtures of the same name,
# so a test may take them either way.
@pytest.fixture(name="auth")
def _auth_fixture():
    return auth


@pytest.fixture(name="mut")
def _mut_fixture():
    return mut


@pytest.fixture(name="list_id")
def _list_id_fixture():
    return list_id


@pytest.fixture(name="mk_row")
def _mk_row_fixture():
    return mk_row


@pytest.fixture(name="mk_event")
def _mk_event_fixture():
    return mk_event


# --- Task 2: lists and doctors
def mk_doctor(con, name="Dr. Rivera", specialty=None, practice=None, retired=0):
    """A doctor inserted straight into the list; returns its id."""
    return mk_row(con, "doctors", name=name, specialty=specialty, practice=practice, retired=retired)


@pytest.fixture(name="mk_doctor")
def _mk_doctor_fixture():
    return mk_doctor


# --- Task 3: events and notes
def post_event(client, email="alex@example.com", **data):
    """POST /api/events as `email`; returns the response."""
    return client.post("/api/events", json=data, headers=mut(email))


@pytest.fixture(name="post_event")
def _post_event_fixture():
    return post_event


# --- Task 4: event details
def add_detail(client, event_id, kind, email="alex@example.com", **data):
    """POST /api/events/<id>/<kind> as `email`; returns the response (the whole event, 201)."""
    return client.post(f"/api/events/{event_id}/{kind}", json=data, headers=mut(email))


@pytest.fixture(name="add_detail")
def _add_detail_fixture():
    return add_detail


# --- Task 5: files
PDF_BYTES = b"%PDF-1.4\n% synthetic test document\n%%EOF\n"


def upload_headers(email="alex@example.com"):
    """mut() without the JSON content type, for a multipart upload: the test client sets it from `data=`."""
    return {k: v for k, v in mut(email).items() if k != "Content-Type"}


def mk_file(con, data=PDF_BYTES, name="test.pdf", **kw):
    """A file stored through files.store (blob and row) by Alex; returns its id (None if it was deleted before).
    Keyword arguments go to store: origin, doc_id, set_id, version."""
    import files
    return files.store(con, ALEX, name, data, **kw).id


@pytest.fixture(name="upload_headers")
def _upload_headers_fixture():
    return upload_headers


@pytest.fixture(name="mk_file")
def _mk_file_fixture():
    return mk_file


# --- Task 6: figures


# --- Task 7: C-CDA reader
# Synthetic documents and archives, built in memory (no ZIP is ever committed): `from ccda_build import make_ccda,
# make_zip` or take the fixtures of the same name. tests/ccda_build.py documents the shapes they take.
from ccda_build import make_ccda, make_zip  # noqa: E402


@pytest.fixture(name="make_ccda")
def _make_ccda_fixture():
    return make_ccda


@pytest.fixture(name="make_zip")
def _make_zip_fixture():
    return make_zip


# --- Task 8: import and suggestions
def post_import(client, data, name="download.zip", email="alex@example.com"):
    """POST /api/imports with `data` as the multipart field `file`, as `email`; returns the response."""
    import io
    return client.post("/api/imports", data={"file": (io.BytesIO(data), name)}, headers=upload_headers(email),
                       content_type="multipart/form-data")


@pytest.fixture(name="post_import")
def _post_import_fixture():
    return post_import


# --- Task 9: installer, backup and deployment notes


# --- Task 10: frontend: shell, home and event page
import os as _os  # noqa: E402


def read_static(rel):
    """The text of a file under static/ (the frontend's own files; tests/test_frontend.py scans them)."""
    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    with open(_os.path.join(root, "static", rel), encoding="utf-8") as f:
        return f.read()


# --- Task 11: frontend: timeline, doctor summary, settings
@pytest.fixture
def history(con):
    """Five synthetic events (the newest open) with a stone, an admission, a procedure and a complication on some, and
    one doctor on a procedure; returns their ids oldest first."""
    ids = [mk_event(con, d, r, o) for d, r, o in (
        ("2021-01-15", "2021-01-20", "passed"), ("2022-03-01", "2022-03-20", "removed"),
        ("2023-02-10", "2023-02-16", "passed"), ("2024-05-02", "2024-05-09", "removed"), ("2024-09-02", None, None))]
    doctor = mk_row(con, "doctors", name="Dr. Rivera", specialty="urology")
    mk_row(con, "stones", event_id=ids[1], size_tenths=90, side="right")
    mk_row(con, "visits", event_id=ids[1], kind_id=list_id(con, "visit_kind", "admission"), from_date="2022-03-01", to_date="2022-03-04")
    mk_row(con, "procedures", event_id=ids[1], kind_id=list_id(con, "procedure_kind", "swl"), doctor_id=doctor)
    mk_row(con, "complications", event_id=ids[1], kind_id=list_id(con, "complication_kind", "uti"))
    mk_row(con, "stones", event_id=ids[4], size_tenths=75, side="left")
    return ids


# --- Task 12: frontend: import, inbox and suggestions
@pytest.fixture
def imported(client, con):
    """One event (2024-03-01) and a synthetic download whose suggestions land everywhere the import screen lists:
    on that event, in a proposed episode (May 2022) and undated. Returns the event id."""
    from ccda_build import EXAMPLE_ROOT, make_ccda, make_zip
    event = mk_event(con, "2024-03-01")

    def enc(day, n):
        return {"id": f"{EXAMPLE_ROOT}^enc{n}", "cls": "EMER", "time": day, "diagnoses": [("icd10", "N20.1")],
                "performers": [("Robin", "Rivera")]}
    doc = make_ccda(doc_id="00000000-0000-4000-8000-000000000001",
                    encounters=[enc("20240301", 1), enc("20220501", 2)],
                    procedures=[{"id": f"{EXAMPLE_ROOT}^p", "code": ("cpt", "52353")}])
    assert post_import(client, make_zip({"DOC0001.XML": doc})).status_code == 201
    return event


# --- Task 13: check the importer against a real download


# --- Task 14: deploy
