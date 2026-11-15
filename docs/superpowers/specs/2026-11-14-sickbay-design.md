# Sickbay: Design

A self-hosted kidney stone history tracker for a patient and a caregiver. Names in this document are generic: the
two users are Alex and Sam, the doctors are Dr. Rivera and Dr. Chen, and every date, size and record shown is made up.

## Intent and why

People with recurring kidney stones are asked the same questions at every new urologist, ER visit and follow-up:
how many stones, how often, how big, what were they made of, what was done, were there infections, how long in
hospital. The answers are scattered. Some are in the patient portal (a hospital's MyChart, for example); stones passed
at home are in nobody's records; the rest is memory.

Sickbay is one private place that holds the whole history: one record per stone episode (an **event**), filled in
during and after it, with the portal's documents attached. It has two jobs:

1. **Answer a doctor in one look**: a printable summary of every episode.
2. **Show the pattern**: above all, how long it usually is between stones, plus sizes, types, procedures and hospital
   time over the years.

It records history. It never interprets it.

## Goals, non-goals, anti-goals

**Goals**

1. Starting an event during an episode takes seconds on a phone: a start date and an optional note.
2. Every detail can be added later, by either user, from phone or computer, as it becomes known (lab results for the
   stone's type often arrive weeks later).
3. Each event holds: when it started and ended, hospital time, complications and infections, procedures, stone size,
   side, location and type, the doctors involved, follow-up care, a dated notes log, and attached files.
4. The patient portal's record download (a ZIP of C-CDA documents) can be imported. Its documents are kept and attached
   to the right event, and the importer suggests values from them that a user accepts one at a time.
5. Manual entry is a first-class path. An event with no imported document is a complete event.
6. A timeline of all events with the gap between each, the typical gap, and the time since the last one.
7. A one-page doctor summary that prints or saves as a PDF.
8. Sign-in required for everything, two users only, default deny. Nightly private backups of the database and files.

**Non-goals**

- A live connection to the patient portal's API (Epic's patient FHIR API). Research showed it is workable for one
  household (free self-service registration, a public HTTPS callback, periodic re-login), but the file import covers
  the need with no registration and nothing opened to the internet. It may come later; nothing here should block it.
- Sharing with doctors through a link or an account. The summary is printed or saved as a PDF and handed over.
- An agent API or MCP server (the owner's other apps have one). For health data, fewer ways in.
- Off-site backups. Backups land in one folder on the host as one archive per night; where copies go, and whether they
  are encrypted, is the owner's decision later. Nothing here should make that harder.
- Tracking anything but stone episodes (medications, diet, fluids, symptoms diaries).
- More than one patient.

**Anti-goals: it has failed if any of these happen**

- It gives medical advice: a recommendation, a risk score, a predicted date of the next stone, a "you should", a diet
  tip. Medians and counts of what happened are fine; any projection forward is not.
- Anyone but the two users reads any of it: a request without a verified sign-in gets anything but 403, a file is
  reachable by a guessable or public URL, or health details reach a page title, the home-screen name, a log line,
  a CI log, a backup readable by a non-root user, or the repository.
- A suggestion from an import changes an event without a user accepting it.
- Importing the same download twice doubles anything.
- Recording during an episode is slow enough that it gets skipped.

## Constraints

- Follows the owner's app conventions as set in `SDNick484/gift-tracker`: Flask and SQLite, plain HTML/CSS/JS ES
  modules with no build step, gunicorn under OpenRC on an Alpine LXC, one self-contained installer script, a Cloudflare
  Tunnel and the app's own Cloudflare Access application in front. Runtime dependencies must exist as Alpine `py3-*`
  packages (`py3-flask py3-gunicorn py3-jwt py3-cryptography`); prefer the standard library.
- Small modules (`app.py`, `db.py`, `identity.py`, feature modules), not one large `app.py`.
- The repository is public and generic: no personal names, dates, providers, hospitals, locations or real records, in
  files or in commits. All test data is synthetic. Real portal downloads and credentials never enter the repository.
- Two users with equal rights, on phone and desktop.

## Users and access

- **Identity** comes only from a verified `Cf-Access-Jwt-Assertion` (signature, `aud` = the app's `CF_AUD`, issuer =
  the team domain, expiry) whose email is in `ALLOWED_EMAILS`. Display names come from `USERS`
  (`a@example.com=Alex,...`). No LAN role, ever: a request from the home network without a valid assertion is 403.
- **Default deny.** A `before_request` guard answers 403 `{"error": "Forbidden"}` on every path except `/healthz`,
  which answers `ok` and nothing else. A test walks `app.url_map` and requires 403 without identity on every rule but
  `/healthz`, so a new route cannot skip the guard.
- **Development** only: `DEV_EMAIL` set, a loopback remote address, and none of the Cloudflare headers present. The
  installer never writes `DEV_EMAIL`.
- **Mutations** (every non-GET/HEAD `/api/` request) need header `X-Sickbay: 1` and a same-origin `Origin`, and a JSON
  body, except the upload routes, which take `multipart/form-data` under the same header and Origin checks.
- **The Cloudflare Access application** for this hostname allows exactly the two users' emails. Setting it up is the
  owner's step; the app does not rely on it alone (the allow-list above is checked again).
- **Response headers**: a CSP with no inline scripts or styles and `frame-ancestors 'none'`; `X-Content-Type-Options:
  nosniff`; `Referrer-Policy: no-referrer`; `Cache-Control: no-store` on every `/api/` and file response, so health data
  does not sit in a shared browser's cache.
- **Neutral surfaces.** The page title, the manifest name and the home-screen name are the `APP_NAME` setting, default
  `Logbook`; no event detail, doctor, file name or condition appears in any `<title>`, the manifest, a URL path segment
  other than numeric ids, or a log line. Logs name a route, a status, an id or an exception class, never a value a user
  typed or a file's name or content.

## The event record

An event is one stone episode. Only `started_on` is required; everything else may be blank and filled in later.

| Part | Holds |
|---|---|
| Event | `started_on` (date), `resolved_on` (date, empty while open), `outcome` (from a list: passed on its own, removed by procedure, other; empty while open), created/updated by and when |
| Stones (0 or more) | size in mm (stored as integer tenths of a mm), side (left, right, both, unknown), location (from a list: kidney, ureter, bladder, unknown), type (from a list: calcium oxalate, calcium phosphate, uric acid, struvite, cystine, mixed, unknown, other) and whether the type came from a lab analysis |
| Care visits (0 or more) | kind (from a list: ER, urgent care, hospital admission, outpatient procedure, office visit), from date, to date; nights are counted from admissions |
| Procedures (0 or more) | kind (from a list: stent placement, stent removal, ureteroscopy, shock-wave lithotripsy, percutaneous nephrolithotomy, nephrostomy tube, other), date, doctor, note |
| Complications (0 or more) | kind (from a list: urinary tract infection, kidney infection, sepsis, obstruction, kidney injury, other), date, note |
| Doctors | links to the shared doctor list, with a role for this event (e.g. urologist, ER) |
| Follow-ups (0 or more) | date, what (free text, e.g. "stent removal", "CT scan"), doctor, done flag |
| Notes | a dated log: author, time, text. Notes are appended; a note can be edited or deleted by either user |
| Files | attached documents (below) |

- **Lists are data, not code.** Every "from a list" above is a table seeded with those defaults and editable in
  Settings (rename, add, retire). A retired item stays on old events.
- **Doctors** are one shared list (name, specialty, optional practice), picked from on any event, so the same doctor is
  never typed two ways.
- An event with no `resolved_on` is **open**. Open events are listed first on the home screen. "Mark resolved" sets the
  end date (default today) and the outcome.
- Several stones in one episode are several stone rows on one event.

## Screens

The mockups shown during design are in `2026-11-14-sickbay-mockups.html` beside this document (made-up data; open it
in a browser). They are the reference; layout details are the builder's.

- **Home**: open events first, then a "New event" button (one tap: date defaulting to today, optional note, save), then
  the timeline.
- **Event**: everything about one episode on one page, phone-first. Each row edits in place. Pending import
  suggestions sit in their own section, apart from what users entered, each with Accept and Dismiss. The notes log has
  an "Add a note" field at the end. Files are listed with their name, kind and size.
- **Timeline**: headline figures (number of episodes, typical gap, time since the last episode started, total hospital
  nights), then every event newest first, each showing date, open or resolved, size and side, hospital time,
  procedures, and type, with the gap to the previous event between them. An event with a complication is marked.
- **Doctor summary**: a print-styled page: the headline figures, then a table of every event (started, resolved, size
  and side, type, hospital, procedures, complications), then the doctors seen. No app branding. A line at the foot
  says it is a record of history, not an interpretation. Printed or saved as a PDF from the browser.
- **Import**: upload a download ZIP (or a single document), see what was found, and an inbox of documents not yet
  attached to an event.
- **Settings**: the editable lists and the doctor list.

### Figures across events

- **Gap**: from one event's `started_on` to the next event's `started_on`. The open event's start is known, so it
  counts.
- **Typical gap**: the median of all gaps, shown with the shortest and longest. Needs at least two events; otherwise
  shown as "—".
- **Since last**: today minus the latest `started_on`.
- **Duration** of an event: `resolved_on` minus `started_on`, for resolved events only.
- **Hospital nights**: the sum, over admission visits, of `to` minus `from`.
- Nothing is projected forward (see anti-goals).

## Files

- A file can be attached to any number of events (a link table, not one `event_id` on the file): a portal summary
  document often covers years of visits, so one document can be the source for several episodes. A file attached to
  no event sits in the import inbox until it is attached or deleted.
- Accepted kinds: PDF, PNG, JPEG, and the XML and HTML documents from a portal download. A configurable size limit per
  upload (default 25 MB). The kind is checked from the content, not only the name.
- Stored under the data directory by a random id, never by the uploaded name; the name is kept in the database for
  display. Served only through an authenticated route by numeric id, with `no-store`. PDF and images are served inline;
  XML and HTML are never rendered as pages (served as a download, or shown through a plain-text view of the document's
  readable sections, at the builder's choice).
- Deleting an event asks first, and deletes its notes. Its files are unlinked; a file left attached to no event goes
  back to the import inbox, never silently deleted.

## Importing a patient portal download

The portal's record download is a ZIP in the IHE XDM layout: one or more folders of C-CDA XML documents (`DOC0001.XML`
and so on) with a `METADATA.XML`, an `INDEX.HTM`, and often a PDF summary. Menu wording differs by health system
("Download my record", "Share my record").

1. **Upload.** A user uploads the ZIP (or one XML or PDF). The ZIP is read in memory, never extracted to disk by member
   name. Limits on member count, per-member size and total uncompressed size refuse a ZIP bomb. Member paths are
   ignored except to tell documents apart.
2. **Parse safely.** XML is parsed with the standard library, and any document with a DOCTYPE or entity declaration is
   refused (no entity expansion, no external fetch). A document that fails to parse is kept as a file and reported,
   never a 500.
3. **Keep the documents.** Each document becomes a file in the import inbox. A download may hold one document per
   visit, or one summary covering years, or both; the importer must handle either, so everything below works per
   **entry** (one encounter, one procedure, one diagnosis), not per document.
   - A document is the same document when its SHA-256 matches, or (for C-CDA) when its `ClinicalDocument/id` or its
     `setId` and `versionNumber` match one already stored. A later download of the same record, rebuilt by the portal
     with new timestamps, therefore adds only what is new; a newer version of a stored document replaces the older
     file's content and keeps its links.
   - A document a user deleted is remembered by those keys (a small table of deleted identities, no content), so the
     next import does not bring it back.
4. **Suggest.** From coded entries only (no free-text guessing), the importer proposes values:
   - care visits from encounters (date range and kind: emergency, inpatient, ambulatory);
   - procedures from the procedures section (stent, ureteroscopy, lithotripsy, nephrolithotomy, by code);
   - complications from diagnoses (urinary tract infection, sepsis, obstruction, by code);
   - stone type from a stone analysis result, where present;
   - doctors named as performers or attending physicians, matched against the doctor list or offered as new.

   - **Which entries.** An entry produces a suggestion when it is stone-related itself (a stone diagnosis, a stone
     procedure, a stone analysis) or when its encounter carries a stone diagnosis, or when its date falls within an
     existing event's window. A summary document's unrelated history (a flu visit) produces nothing. Documents that
     produce nothing stay in the inbox for the user to attach or delete.
   - **Identity.** A suggestion is keyed by its entry's own C-CDA `id` (root and extension) where present, else by a
     hash of its document identity, kind, date and code. Re-imports never repeat a suggestion, and a dismissed one
     stays dismissed.
   - **Target, computed when shown, not stored.** An event's window runs from `started_on` minus 7 days to
     `resolved_on` plus 30 days; an open event's window ends 90 days after `started_on`, so an event left open by
     mistake does not collect every later document. A suggestion whose date falls in one window targets that event;
     in two, the event whose `started_on` is nearest. Suggestions that fall in no window are grouped into **proposed
     episodes**: sorted by date, each joins the previous one's group when it is within 14 days of it. A proposed
     episode is accepted as a whole ("create this event"), which creates one event with `started_on` = its earliest
     date and accepts nothing else automatically; its individual suggestions then target the new event and are
     accepted one at a time like any other. A user can also move a suggestion to a different event, or split it off.
5. **Accept or dismiss.** A suggestion changes nothing until a user accepts it. Accepting writes the value through the
   same code a manual edit uses, records that it came from an import, and links its source document to the event.

The code tables the importer recognises (ICD-10, SNOMED CT, CPT and LOINC codes for the above) live in one module,
with a test per entry, so adding a code is one line.

## Hosting, installer and backups

As in gift-tracker:

- `installer/build.sh` builds `dist/setup-sickbay.sh`: `installer/setup-head.sh` plus the app as a base64 tarball.
  Re-runnable; keeps data, config and backups; `--uninstall` removes the app and service only. Household values come
  from a git-ignored `deploy/deploy.env` (`deploy/deploy.env.example` committed). No second instance, no MCP service.
- Names and paths: service `sickbay`, `/opt/sickbay`, `/var/lib/sickbay` (database and `files/`, mode 750),
  `/etc/conf.d/sickbay` (mode 600, root), `/var/log/sickbay.log`. Default port 8774, gunicorn bound to `127.0.0.1`
  only; cloudflared in the container is the one way in.
- Settings: `SICKBAY_PORT`, `ALLOWED_EMAILS`, `USERS`, `CF_TEAM_DOMAIN`, `CF_AUD`, `APP_NAME`, `MAX_UPLOAD_MB`.
- **Backups**: `ops/sickbay-backup` in `/etc/periodic/daily`. Each night: a SQLite `.backup` checked with `PRAGMA
  integrity_check`, plus the files directory, packed into one archive `sickbay-YYYY-MM-DD.tar.gz` in
  `/var/backups/sickbay` (directory 700, archive 600, root), kept 14 days. A failed run logs `FAILED` and leaves no
  partial archive. The installer takes a first backup. The README says how to restore one and names that folder as
  the one thing an off-site copy needs.
- Schema changes go through an idempotent `migrate()` with a `SCHEMA_VERSION`; no manual database step on the host.

## Repository, privacy and CI

- `CLAUDE.md` holds the rules above in the same form as gift-tracker's, plus the "changes together" list.
- **Privacy scan** from `SDNick484/jigs`: `.github/scripts/privacy_scan.py` and its test copied verbatim, and a CI job
  that runs it over files and every commit's author, committer and message, with the `PRIVACY_DENYLIST` secret
  (patterns only when the secret is missing, with a warning). Setting that secret is the owner's step.
- `tests/test_hygiene.py` as in gift-tracker: only `example.com` emails in tracked files, plus a git-ignored
  `tests/hygiene_denylist.txt`.
- `.gitignore` covers `deploy/*.env` (except the example), `dist/`, `.env`, any `*.zip`, `imports/` and `private/`,
  so a real download dropped in the working tree for a manual test is not committed.
- Test fixtures are hand-written synthetic C-CDA documents and ZIPs, built in the tests or kept under
  `tests/fixtures/`.
- CI jobs, as gift-tracker's: pytest on Python 3.12 and the latest, pytest on Alpine's `py3-*` packages (with the
  `sqlite3` CLI for the backup tests), `ruff check`, `node --check` on `static/js/*.js`, `node --test` for frontend
  helpers, `shellcheck -s sh` on the installer and backup script, and the privacy scan. Actions pinned by commit SHA,
  as in jigs.
- Commits: Conventional Commits, author `SDNick484 <SDNick484@users.noreply.github.com>`.

## Decided during design

- Import is by file (the portal's ZIP); the portal API is a non-goal for now.
- Manual entry and import are equal paths; import brings documents and suggestions, and suggestions need acceptance.
- Events start small and are filled in over time, with a dated notes log and attached files from the portal.
- Two users, equal rights, Cloudflare Access plus the app's own allow-list, default deny.
- Backups stay on the host for now, packaged so an off-site copy is easy later.
- The screens: event page, timeline, doctor summary, as in the mockups.

## Calls made without the owner (check these)

- The record's lists (outcome, location, type, visit kind, procedure kind, complication kind) and their defaults.
- Size stored in tenths of a mm; several stones per event.
- The gap is measured start to start, and the open event counts; typical gap is the median.
- The suggestion window (7 days before start to 30 days after resolution, 90 days after start while open; nearest
  start wins an overlap) and the 14-day grouping of proposed episodes.
- Import works per entry, so it handles per-visit documents and whole-history summaries alike; a file can link to
  several events; re-downloads de-duplicate by document and entry identity; deleted documents stay deleted.
- Suggestions only from coded entries, never free text; size is never suggested.
- Deleting an event unlinks its files rather than deleting them.
- No agent API, no PIN lock, no doctor sharing link.
- Default `APP_NAME` `Logbook`, port 8774.
- `Cache-Control: no-store` on all data and files.

## Left to the builder

- Module boundaries beyond those named, the exact schema, route names and JSON shapes.
- Page layout and styling within the mockups, and whether the timeline has a small chart (inline SVG, no library).
- How XML and HTML documents are shown (download or plain-text view).
- The exact code lists, as long as each code has a test.
