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


# --- Task 3: events and notes


# --- Task 4: event details


# --- Task 5: files


# --- Task 6: figures


# --- Task 7: C-CDA reader


# --- Task 8: import and suggestions


# --- Task 9: installer, backup and deployment notes


# --- Task 10: frontend: shell, home and event page


# --- Task 11: frontend: timeline, doctor summary, settings


# --- Task 12: frontend: import, inbox and suggestions


# --- Task 13: check the importer against a real download


# --- Task 14: deploy
