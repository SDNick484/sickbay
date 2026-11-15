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

- Lists live in `lists.py` and are rows: a new kind of list is a schema change (the `kind` CHECK), a new item is data.
  The API never sets `slug` (only seeded items have one; code finds them by slug) and never deletes an item: retire it.
  `counts_nights` is settable on `visit_kind` items only.
- Duplicate labels within a kind are 409, retired items included (the unique pair covers them), so renaming onto a
  retired label also fails.
- A doctor row can be deleted only while nothing refers to it: `event_doctors`, `procedures.doctor_id` and
  `followups.doctor_id`. A new table that refers to `doctors` goes in `lists._DOCTOR_REFS`.
- `lists.find_doctor` matches retired doctors too, so the importer never re-creates one; callers that offer doctors
  to pick from use `lists.doctors(con)` (active only).
- `lists.all` is named as the plan's interface says and shadows the builtin inside `lists.py`; call it as
  `lists.all(con)`.

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

- `details.py` is route-free like `events.py`. A new kind of row is a schema change; a new field is an entry in
  `details._FIELDS` (input key, column, parser) plus the SQL in `details.for_event`. The input keys are the column
  names (`kind_id`, `location_id`, `type_id`, `doctor_id`, `from_date`, `to_date`, `on_date`, `what`, `done`, `note`,
  `side`, `type_from_lab`) except size, which is `size_mm`; the response reads `{id, label}` for a list value and
  `{id, name}` for a doctor.
- `events.get` (so every event route) returns `stones, visits, procedures, complications, followups, doctors` beside
  the event's own keys; the detail routes answer with it, as do the event and note routes. Deletes are 204.
- Sizes: `details.parse_size` is the only place a typed size becomes tenths (dot decimal, 0.1 to 200, a third
  decimal rounds half up). `details._size_mm` formats the same way as `figures._size_mm`; a test keeps them equal.
- `details.add/update/delete/link_doctor/unlink_doctor` open no transaction of their own (the importer runs them
  inside one); they are atomic through a savepoint. Each moves the event's `updated_at`, and `updated_by` when given
  `user` (`delete`, `link_doctor`, `unlink_doctor` take it as an optional last argument).
- A retired list item or doctor is accepted on `procedures`/`followups`/`stones`/`visits` only as the row's current
  value. `link_doctor` accepts a retired doctor, since the importer matches them by name.
- Dates inside an event are free (no future check); only a visit's end before its start is refused.

### Task 5: files

- A blob is `FILES_DIR/<storage_id>`, 32 random hex digits; `files._path` refuses to build a path from anything else.
  The uploaded name is display text only, cleaned by `files.safe_name`, and never reaches a path, a URL or a log line
  (log `file <id> <status>`).
- The kind comes from `files.sniff` (content), never the name or the browser's type; anything else raises
  `files.NotAccepted` (an `ApiError` 415). A new kind means `sniff`, a test, and adding it to `files.INLINE` only if
  a browser shows it inert (never markup: HTML, XML and SVG-in-XML stay downloads).
- `GET /api/files/<id>/content` sets its own `Content-Security-Policy: sandbox; default-src 'none'`, `nosniff`,
  `no-store`, `Cross-Origin-Resource-Policy: same-origin` and an RFC 5987 `Content-Disposition`. The app-wide
  `X-Frame-Options: DENY` applies too, so the frontend opens a file in a new tab (or an `<img>`), never an iframe.
  `tests/test_files.py` parametrizes every kind over these headers; keep them in step.
- Blobs follow the transaction: `files.store` and `files.delete` run inside `with files.atomic(con):`, a
  `db.transaction` that removes the blobs it wrote on rollback and the ones it replaced or deleted on commit. Called
  with no transaction open they open their own; inside a bare `db.transaction` they raise `RuntimeError`. So the
  importer wraps its whole run in `files.atomic(con)`, not `db.transaction(con)`. `link`/`unlink` are plain SQL and
  fine in any transaction.
- Deleted-document memory: `files.delete` records `sha:`, `doc:` and `set:` keys in `deleted_documents`, and
  `store(..., origin="import")` then answers `Stored(None, "deleted")`. A person uploading the same bytes by hand
  (`origin="upload"`) is choosing it: that store forgets the `sha:` key and keeps the file.
- `DELETE /api/files/<id>` is for the inbox: a file still on an event is 409; take it off its events first. Deleting
  an event only drops its `event_files` rows (schema cascade), so its files go back to the inbox.
- Fixtures: `upload_headers(email)` (`mut()` without the JSON type, for `data={"file": ...}`), `mk_file(con, data=
  PDF_BYTES, name="test.pdf", **store_kwargs) -> id`, `PDF_BYTES`.

### Task 6: figures

- `figures.py` is read-only SQL over schema v1 and has no routes of its own; `GET /api/timeline` and
  `GET /api/summary` (in `app.py`) call it with `common.today()`. The summary is the timeline plus `doctors`.
- The rules are the spec's "Figures across events": gap is start to start and the open event counts; typical gap is
  the median (even count: mean of the middle two, rounded half up) with shortest and longest, `None` under two
  events; `since_last_days` is today minus the latest start (0 if that start is somehow after today); duration only
  for resolved events; nights are `to - from` over visits whose list item has `counts_nights` (the flag, not the
  `admission` slug), a visit with no end date counting nothing.
- A new figure is a new key in `headline` or the rows; Task 11's screens read these keys. Nothing here may look
  forward: no key or value names a next date, an expectation or a score (`test_no_projection_keys` checks the keys).
- Row `sizes_mm` are text like Task 4's `size_mm` (`"7"`, `"7.5"`); `sides` leaves out `unknown`; `types` are the
  distinct list labels; `procedures`, `complications` and `visit_kinds` keep one entry per row, in date order.

### Task 7: C-CDA reader

- `ccda.py` works on bytes in memory and never writes anything: a ZIP member's path is only a name (cleaned: no
  `..`, root or drive). Whoever stores a document (Task 5's `files.store`) picks the blob path; display names go
  through `files.safe_name`.
- Untrusted XML is parsed only through `ccda.parse`, never `ElementTree.fromstring` directly: it decodes the bytes
  itself, refuses any `<!DOCTYPE`/`<!ENTITY` in the bytes or the decoded text, then hands expat that same text.
- The ZIP limits (`MAX_MEMBERS`, `MAX_MEMBER_BYTES`, `MAX_TOTAL_BYTES`) count bytes as `ccda._inflate` produces
  them; `zipfile` only reads the directory. Members that share bytes, lie about their size or fail their CRC are
  `Refused(DAMAGED)`. Keep tests that forge headers (`tests/test_ccda.py` `patch`) working when touching this.
- Errors: `Refused` is the whole upload (400 with `.message`); `Unsafe` and `Unreadable` are one document (count it
  `failed`, keep going); `Document.error` is a member kept but not read (nested ZIP, unknown compression). Nothing
  else may escape `read_download` or `parse`; the fuzz tests check that.
- An entry reports one (system, code): of the code and its translations, the first by system in `ccda.PREFER[kind]`
  (CPT for procedures, ICD-10 for diagnoses), never "whichever `codes.py` knows", so keys built from it stay put when
  codes are added. `Entry.codes` holds every known-system code and translation for a lookup that needs them all.
- Adding a code: one line in `codes.py` with a comment naming it, one row in `tests/test_codes.py`, checked against
  the code system's reference first. Slug values must be seeded slugs in `db.SEED_LISTS`; `test_codes.py` enforces it.
- New synthetic documents come from `tests/ccda_build.py` (`make_ccda`, `make_zip`), with made-up content only.

### Task 8: import and suggestions

### Task 9: installer, backup and deployment notes

- `installer/setup-head.sh` is the installer; `installer/build.sh` appends the app (every top-level `*.py`, `static/`,
  `ops/sickbay-backup`) as a base64 tarball and bakes household defaults from `deploy/deploy.env` (only
  `ALLOWED_EMAILS USERS APP_NAME CF_TEAM_DOMAIN CF_AUD`; never `DEV_EMAIL`). A new top-level module ships by glob.
- A new setting also needs an `add_setting "$CONF" ...` line and a name in the service's `export` line in
  `setup-head.sh`; `tests/test_installer_static.py` checks `settings.py` against `.env.example`, those two and nothing
  extra.
- `ops/sickbay-backup` writes `sickbay-YYYY-MM-DD.tar.gz` (`sickbay.db` from `sqlite3 .backup`, integrity-checked, and
  `files/`). If the layout of the data folder changes (a new directory beside `files/`), change the script and
  `tests/test_backup.py` together, and the restore steps in the README's "Backups and restore".
- The shell is POSIX `sh` for BusyBox, checked with `shellcheck -s sh`.

### Task 10: frontend: shell, home and event page

- `GET /` and `GET /manifest.json` (in `app.py`) fill `APP_NAME` into `static/index.html` (`__APP_NAME__`, HTML-escaped)
  and `static/manifest.json`; `/static/*` is Flask's own static route, behind the same identity guard, so the page
  links the manifest with `crossorigin="use-credentials"`. The page's `document.title` is set once, in `main.js`,
  from `/api/me`; `tests/test_frontend.py` keeps it that way.
- Screens build markup with `core.el(tag, attrs, ...children)` (text becomes text nodes; `style` and `on*` attributes
  throw; handlers go in `on: {click: fn}`), so nothing typed or imported reaches `innerHTML`. If a screen does use a
  template literal for markup, every `${}` must be one call to `esc`, `mm`, `daysLabel`, `dateLabel` or a `render*`
  function (`tests/test_frontend.py` scans every `static/js/*.js`).
- Only `api.js` calls `fetch`: `get`, `send(method, path, body)` and `upload(path, file)` add `X-Sickbay: 1` and the
  JSON (or multipart) body; a refusal or a dead network resolves to `{error}`, a 204 to `{}`. Callers test `.error`.
- Routes: `main.js` `SCREENS` is keyed by the first hash segment, one line each; a screen module exports
  `render({view, parts, query, alive, rerender})`. Tasks 11 and 12 add lines there, links in `index.html`'s `<nav
  id="nav">`, and (Task 11) the print stylesheet link where the comment in `<head>` says.
- `timeline.js` exports `renderTimeline(host, events?)` (Task 11 replaces its body) and `eventLink(ev)`.
- `event.js` keeps one state object and redraws the whole page after each mutation (every event route answers with
  the whole event, a 204 triggers a refetch). One form is open at a time (`st.open`). The empty
  `<section id="event-suggestions">` is the seam for Task 12; it is hidden while empty. `event.js` exports `form`,
  `valueOf` and `daysBetween` for reuse and tests. A file opens as a plain `<a target="_blank" rel="noopener">` to
  `/api/files/<id>/content`, never an iframe.
- A new part of the record is an entry in `event.js`'s `PARTS` (kind, title, `describe`, `fields`) when it follows the
  detail routes' shape.

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
