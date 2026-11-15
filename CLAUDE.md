# Sickbay: notes for Claude

A private kidney stone history tracker for a patient and a caregiver: one record per stone episode (an event), filled
in over time, with the patient portal's documents attached, an import of the portal's record download with
suggestions, a timeline and a printable doctor summary. It records history and never interprets it. Flask + SQLite,
plain ES-module frontend with no build step, gunicorn under OpenRC on an Alpine LXC, behind a Cloudflare Tunnel and
its own Cloudflare Access application. The design is `docs/superpowers/specs/2026-11-14-sickbay-design.md`; the build
order is `docs/superpowers/plans/2026-11-14-sickbay.md`. Do not edit either to make code fit.

## Rules that must keep holding

- Runtime dependencies must exist as Alpine `py3-*` packages: `py3-flask py3-gunicorn py3-jwt py3-cryptography`. Prefer the standard library (no `lxml`, no `defusedxml`).
- No frontend framework, bundler or npm step. Native ES modules in `static/js/`.
- The repository is public and generic: no personal names, dates, providers, hospitals, locations or real records, in files, commits, branch names or PR text. Generic names (Alex, Sam; Dr. Rivera, Dr. Chen), `example.com` emails, RFC 5737 addresses. All test data synthetic. Real portal downloads and credentials never enter the repository. `tests/test_hygiene.py` enforces the emails and the ZIPs, plus any strings in the git-ignored `tests/hygiene_denylist.txt`; the privacy scan (below) checks the rest it can.
- Identity only from a verified `Cf-Access-Jwt-Assertion` whose email is in `ALLOWED_EMAILS`. No LAN role, ever. Development exception only with `DEV_EMAIL` set, a loopback address and none of `identity.CF_HEADERS`; the installer never writes `DEV_EMAIL`.
- Default deny: every route answers 403 `{"error": "Forbidden"}` without identity, except `/healthz` (`ok`).
- Every non-GET/HEAD request needs header `X-Sickbay: 1` and a same-origin `Origin`; a JSON body unless the view is marked `@multipart` (then `multipart/form-data`).
- Read bodies with `common.json_body()`, strings with `common.text()`, dates with `common.parse_date()`; bad shapes are 400 with a message a person can read. Never store non-finite numbers. Stone size is integer tenths of a mm.
- `Cache-Control: no-store` on every `/api/` response and every file response. CSP with no inline scripts or styles and `frame-ancestors 'none'`; `nosniff`; `Referrer-Policy: no-referrer`.
- Everything user-typed or imported goes through `esc()` before `innerHTML`. No `style=""`.
- No event detail, doctor, note, file name or condition in any `<title>`, the manifest, a URL segment other than numeric ids, or a log line. Page title and manifest name are `APP_NAME`, default `Logbook`.
- The app records history and gives no advice: no recommendation, risk score or projected date anywhere, in UI text or API.
- Schema changes go through idempotent `migrate()` and bump `SCHEMA_VERSION`. No manual DB step on the host.
- Lists are data, not code: outcome, location, stone type, visit kind, procedure kind and complication kind are rows.
- Commits: Conventional Commits, one logical change each, author `SDNick484 <SDNick484@users.noreply.github.com>`.
- Docs use OpenRC commands and say `vim`.
- Default port `8774`. Gunicorn binds `127.0.0.1` only. Names: service `sickbay`, `/opt/sickbay`, `/var/lib/sickbay`, `/etc/conf.d/sickbay`, `/var/backups/sickbay`.

## The privacy scan

- `.github/scripts/privacy_scan.py` and `tests/test_privacy_scan.py` are byte-for-byte copies from `SDNick484/jigs`
  (blob `762c462` and `a2ab902`), keeping jigs' `privacy-scan: allow` line. Don't edit them here: change them in jigs
  and copy them again.
- It checks patterns (addresses, emails, SteamIDs) and a denylist held in the `PRIVACY_DENYLIST` repository secret,
  over tracked and untracked-but-not-ignored files and over every commit's author, committer and message.
- The denylist can't be read from a session, so a local run (`python .github/scripts/privacy_scan.py`) only covers the
  patterns: a clean local run does not mean CI will pass. CI runs it with `--require-denylist` wherever the secret is
  available, and warns when it is not (pull requests from forks).
- CI output is public, so the scan prints where and which rule, never the value. If it reports `denylist entry #N`,
  reword the line; never guess the term in a commit message, PR text or comment. A flagged commit message means
  rewriting that commit before merge, since history is scanned.
- If it flags a line that is fine (a code system OID that looks like an address, say), rewrite the line; don't mark it.

## Changes together

Each task adds its notes under its own heading below and never edits another task's.

### Task 1: scaffold

- A new setting goes in `settings.py`, `.env.example` and the installer's settings list (Task 9 tests the last two).
- The whole schema is version 1 in `db.py`; later tasks add modules and routes, not tables. A later schema change
  bumps `db.SCHEMA_VERSION` and adds a step to `db.MIGRATIONS`; never edit `_to_v1` once it has shipped.
- A new route of any kind stays behind the identity guard: `tests/test_identity.py` walks `app.url_map` (path
  arguments filled with `1`, every method) and expects 403 without identity everywhere but `/healthz`. Nothing to add
  for a new route; a new open path would have to go in `app.OPEN_PATHS`, and there should never be another.
- An upload route is marked `@common.multipart` **under** the route decorator, so the guard accepts
  `multipart/form-data` for it (still with `X-Sickbay: 1` and a same-origin `Origin`). Every other mutation is JSON.
- `app.security_headers` sets `Content-Security-Policy` only when the view did not: a view may set a stricter one (the
  file content route's `sandbox; default-src 'none'`), never a looser one. `Cache-Control: no-store` is forced on
  every response.
- Logs: `app.log_request` writes method, route rule and status; an unhandled exception is logged as its route rule
  and class (`Sickbay.log_exception`), never its message. Keep user values out of URLs (path and query string): the
  rule is logged here, but gunicorn's access log, if enabled, prints the raw path and query.
- "Now" and "today" are `common.now()` and `common.today()`, called through the module (never
  `from common import today`), so `clock` and `freeze_today` move them.
- A request body larger than `MAX_UPLOAD_MB` is 413 `{"error": "That file is too large."}`.
- A file copied verbatim from another repo that names a non-example email goes in `VERBATIM_COPIES` in
  `tests/test_hygiene.py` (the privacy scan still checks it).

### Task 2: lists and doctors

### Task 3: events and notes

- `events.py` is route-free; `app.py` calls it with `g.user` and `db()`. Each event route answers with
  `events.get(...)` (the whole event with its notes), so the page redraws from one response. Deletes are 204.
- Dates go through `common.parse_date` and then `events.check_dates(started_on, resolved_on)`: end before start and a
  start after tomorrow are 400. Task 4's detail dates reuse `check_dates` (or its own end-after-start rule).
- `events.update` changes only the keys present. `resolved_on: null` reopens and clears the outcome; an outcome on an
  open event is 400. An outcome must be an `outcome` list item (a retired one only if it is already the value); the
  list is read with SQL, not through `lists.py`.
- A start date that did not change is not re-checked against today, so an old event stays editable.
- Task 4 merges `details.for_event` into `events.get`; notes do not bump the event's `updated_by/updated_at`.
- A note's `author_name` comes from `settings.USERS`, falling back to the email. Either user may edit or delete any
  note; the author never changes.

### Task 4: event details

### Task 5: files

### Task 6: figures

### Task 7: C-CDA reader

### Task 8: import and suggestions

### Task 9: installer, backup and deployment notes

### Task 10: frontend: shell, home and event page

### Task 11: frontend: timeline, doctor summary, settings

### Task 12: frontend: import, inbox and suggestions

### Task 13: check the importer against a real download

### Task 14: deploy

## File structure

```
app.py            Flask object, guards, security headers, every route (one section per task)
settings.py       environment config, read once at import
db.py             SCHEMA, SCHEMA_VERSION, MIGRATIONS, connect(), transaction(), migrate(), seed lists
common.py         ApiError, json_body, text, parse_date, today, now, multipart marker
identity.py       Cloudflare JWT verification, resolve_user()
lists.py          editable lists and the doctor list
events.py         events, open/resolved, notes log
details.py        stones, care visits, procedures, complications, follow-ups, event doctors
files.py          file storage, kind sniffing, links to events, inbox, deleted identities
figures.py        gaps, headline figures, timeline rows, summary rows
ccda.py           safe download reading (ZIP/XDM, XML, PDF) and C-CDA entry extraction
codes.py          code tables: diagnosis, complication, procedure, stone analysis
imports.py        import runs, suggestions, targets, proposed episodes, accept/dismiss/move
static/           index.html, style.css, print.css, manifest.json, icon.svg
static/js/        main (router), core, api, home, event, timeline, summary, settings, importer, suggestions
installer/        setup-head.sh, build.sh
ops/              sickbay-backup
deploy/           deploy.env.example
tools/            import_check.py (Task 13; prints structure only)
tests/            conftest.py, ccda_build.py (synthetic C-CDA builder), one test file per module
.github/          workflows/ci.yml, scripts/privacy_scan.py
```

Not every file exists yet; the plan says which task creates each.

## How the code is put together

- `db.connect()` opens connections in autocommit mode (`isolation_level=None`) with `sqlite3.Row` results, foreign
  keys on and WAL. A single statement commits by itself; wrap anything that must be atomic in
  `with db.transaction(con):` (`BEGIN IMMEDIATE`).
- `app.db()` is the per-request connection in `flask.g`. `app.init_db()` creates `DATA_DIR` and `FILES_DIR` and runs
  `migrate()` at import, so every start migrates. `migrate()` seeds the lists once (in the version 1 step); seeded
  items carry a fixed `slug`, user-added ones have none.
- `identity.resolve_user(headers, remote_addr)` returns `{"email", "name"}` or `None`. The `before_request` guard in
  `app.py` turns `None` into 403 `{"error": "Forbidden"}` on every path except `/healthz`, then applies the mutation
  checks to every request that is not GET or HEAD. `g.user` is the signed-in user.
- Raise `common.ApiError(status, message, **extra)` from routes and modules; it becomes `{"error": message, **extra}`.
  Other HTTP errors become `{"error": "<name>"}` (`Not Found`, `Method Not Allowed`).
- `common.text(data, key, *, required=False, max_len=2000)`: stripped text; missing, null or blank is `None`.
  `common.parse_date(value, *, field, required=False)`: `YYYY-MM-DD` only, a real calendar date from 1900 on;
  `None` or blank is `None`; anything else is 400 `"<field> must be a date like 2024-03-01."`, with `field` the name a
  person reads ("Start date").

## Tests

- `tests/conftest.py` sets the environment before importing `app`, and gives every test a fresh database and files
  directory (`client`, `con`). Each task adds its own fixtures under its own heading at the end.
- Cloudflare is simulated: `identity._jwk_client` is stubbed with a test RSA key. `auth(email)` returns Cloudflare
  headers with a signed JWT (keyword arguments `aud`, `iss`, `exp`, `key` forge bad ones); `mut(email)` adds
  `X-Sickbay: 1`, a same-origin `Origin` and a JSON content type (drop `Content-Type` for a multipart upload).
- Database helpers: `mk_event(con, started_on, resolved_on=None, outcome=None)` (outcome slug or id),
  `mk_row(con, table, **cols)` (a raw insert; give every NOT NULL column), `list_id(con, kind, slug)`. `auth`, `mut`,
  `mk_event`, `mk_row` and `list_id` are plain functions (`from conftest import ...`) and fixtures of the same name.
- `clock` freezes `common.now()` (`clock.advance(n)`); `freeze_today("2024-03-01")` freezes `common.today()`.
- The app runs with `testing = True`, so an exception in a view is raised in the test, with its traceback. A test of
  the 500 path sets `monkeypatch.setitem(app.config, "TESTING", False)`.
- Test-only routes `/api/_test/echo` (JSON), `/api/_test/upload` (`@multipart`) and `/api/_test/boom` (raises) are
  registered in `conftest.py`, behind the same guards.
- The Flask test client's default remote address is `127.0.0.1`; `conftest.py` clears `DEV_EMAIL` so loopback is not
  admitted by accident. Use `environ_base={"REMOTE_ADDR": "192.0.2.50"}` (`conftest.LAN_IP`) for a LAN request.

## Verification gate

Run before every commit; all of it must pass:

```sh
python -m pytest -q && ruff check && python .github/scripts/privacy_scan.py
```

Once they exist, also `for f in static/js/*.js; do node --check "$f"; done`, `node --test tests/*.test.mjs` and
`shellcheck -s sh installer/*.sh ops/sickbay-backup`.

CI (`.github/workflows/ci.yml`) runs pytest on Python 3.12 and the latest Python, pytest on Alpine's `py3-*`
packages (with the `sqlite3` CLI and `tar` for the backup tests), `ruff check`, `node --check`, `node --test`,
`shellcheck -s sh` (the last three skip until their files exist) and the privacy scan over files and every commit.
