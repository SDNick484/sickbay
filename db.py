"""The SQLite schema, connections and migrate().

Connections run in autocommit mode (isolation_level=None): a single statement commits on its own, and anything
that must be atomic runs inside `with transaction(con):`. Every schema change adds a step to MIGRATIONS and bumps
SCHEMA_VERSION; migrate() is idempotent and runs at every start, so the host never needs a manual DB step.

Version 1 is the whole schema the plan needs: later tasks add modules and routes, not tables."""
import sqlite3
from contextlib import contextmanager

SCHEMA_VERSION = 1

# Version 1. Statements are separated by ";" and contain none themselves (no triggers): see _statements().
# `stones`, `visits` and the other detail tables refer to `suggestions` before it is created; SQLite checks a
# foreign key when a row is written, not when the table is created, so the order does not matter.
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS lists (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('outcome','location','stone_type','visit_kind','procedure_kind','complication_kind')),
  slug TEXT, label TEXT NOT NULL, sort_order INTEGER NOT NULL DEFAULT 0, retired INTEGER NOT NULL DEFAULT 0,
  counts_nights INTEGER NOT NULL DEFAULT 0, UNIQUE (kind, label), UNIQUE (kind, slug));
CREATE TABLE IF NOT EXISTS doctors (id INTEGER PRIMARY KEY, name TEXT NOT NULL, specialty TEXT, practice TEXT,
  retired INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, started_on TEXT NOT NULL, resolved_on TEXT, outcome_id INTEGER REFERENCES lists(id),
  created_by TEXT NOT NULL, created_at INTEGER NOT NULL, updated_by TEXT NOT NULL, updated_at INTEGER NOT NULL,
  CHECK (resolved_on IS NULL OR resolved_on >= started_on));
CREATE TABLE IF NOT EXISTS stones (
  id INTEGER PRIMARY KEY, event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  size_tenths INTEGER CHECK (size_tenths IS NULL OR size_tenths > 0),
  side TEXT NOT NULL DEFAULT 'unknown' CHECK (side IN ('left','right','both','unknown')),
  location_id INTEGER REFERENCES lists(id), type_id INTEGER REFERENCES lists(id),
  type_from_lab INTEGER NOT NULL DEFAULT 0, suggestion_id INTEGER REFERENCES suggestions(id) ON DELETE SET NULL);
CREATE TABLE IF NOT EXISTS visits (
  id INTEGER PRIMARY KEY, event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  kind_id INTEGER NOT NULL REFERENCES lists(id), from_date TEXT NOT NULL, to_date TEXT,
  suggestion_id INTEGER REFERENCES suggestions(id) ON DELETE SET NULL,
  CHECK (to_date IS NULL OR to_date >= from_date));
CREATE TABLE IF NOT EXISTS procedures (
  id INTEGER PRIMARY KEY, event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  kind_id INTEGER NOT NULL REFERENCES lists(id), on_date TEXT, doctor_id INTEGER REFERENCES doctors(id),
  note TEXT, suggestion_id INTEGER REFERENCES suggestions(id) ON DELETE SET NULL);
CREATE TABLE IF NOT EXISTS complications (
  id INTEGER PRIMARY KEY, event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  kind_id INTEGER NOT NULL REFERENCES lists(id), on_date TEXT, note TEXT,
  suggestion_id INTEGER REFERENCES suggestions(id) ON DELETE SET NULL);
CREATE TABLE IF NOT EXISTS followups (
  id INTEGER PRIMARY KEY, event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  on_date TEXT, what TEXT NOT NULL, doctor_id INTEGER REFERENCES doctors(id), done INTEGER NOT NULL DEFAULT 0,
  suggestion_id INTEGER REFERENCES suggestions(id) ON DELETE SET NULL);
CREATE TABLE IF NOT EXISTS event_doctors (
  event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  doctor_id INTEGER NOT NULL REFERENCES doctors(id), role TEXT, PRIMARY KEY (event_id, doctor_id));
CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY, event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  author TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS files (
  id INTEGER PRIMARY KEY, storage_id TEXT NOT NULL UNIQUE, name TEXT NOT NULL, mime TEXT NOT NULL,
  size INTEGER NOT NULL, sha256 TEXT NOT NULL UNIQUE, doc_id TEXT UNIQUE, set_id TEXT, version INTEGER,
  origin TEXT NOT NULL CHECK (origin IN ('upload','import')), created_by TEXT NOT NULL, created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS event_files (
  event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE, PRIMARY KEY (event_id, file_id));
CREATE TABLE IF NOT EXISTS deleted_documents (key TEXT PRIMARY KEY, deleted_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS imports (
  id INTEGER PRIMARY KEY, created_by TEXT NOT NULL, created_at INTEGER NOT NULL,
  documents INTEGER NOT NULL, new INTEGER NOT NULL, updated INTEGER NOT NULL, same INTEGER NOT NULL,
  skipped_deleted INTEGER NOT NULL, failed INTEGER NOT NULL, suggestions INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS suggestions (
  id INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE, file_id INTEGER REFERENCES files(id) ON DELETE SET NULL,
  import_id INTEGER NOT NULL REFERENCES imports(id),
  kind TEXT NOT NULL CHECK (kind IN ('visit','procedure','complication','stone_type','doctor')),
  on_date TEXT, end_date TEXT, payload TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','accepted','dismissed')),
  target_event_id INTEGER REFERENCES events(id) ON DELETE SET NULL,
  event_id INTEGER REFERENCES events(id) ON DELETE SET NULL, decided_by TEXT, decided_at INTEGER);
CREATE INDEX IF NOT EXISTS events_started ON events(started_on);
CREATE INDEX IF NOT EXISTS suggestions_state ON suggestions(state, on_date)
"""

# The seeded list items, (slug, label) in display order. Lists are data: users rename, add and retire items in
# Settings; the slug is fixed and is how code (the importer, tests) finds a seeded item. Only `admission` counts
# hospital nights.
SEED_LISTS = {
    "outcome": [("passed", "Passed on its own"), ("removed", "Removed by procedure"), ("other", "Other")],
    "location": [("kidney", "Kidney"), ("ureter", "Ureter"), ("bladder", "Bladder"), ("unknown", "Unknown")],
    "stone_type": [("calcium_oxalate", "Calcium oxalate"), ("calcium_phosphate", "Calcium phosphate"),
                   ("uric_acid", "Uric acid"), ("struvite", "Struvite"), ("cystine", "Cystine"), ("mixed", "Mixed"),
                   ("unknown", "Unknown"), ("other", "Other")],
    "visit_kind": [("er", "ER"), ("urgent_care", "Urgent care"), ("admission", "Hospital admission"),
                   ("outpatient", "Outpatient procedure"), ("office", "Office visit")],
    "procedure_kind": [("stent_placement", "Stent placement"), ("stent_removal", "Stent removal"),
                       ("ureteroscopy", "Ureteroscopy"), ("swl", "Shock-wave lithotripsy"),
                       ("pcnl", "Percutaneous nephrolithotomy"), ("nephrostomy", "Nephrostomy tube"), ("other", "Other")],
    "complication_kind": [("uti", "Urinary tract infection"), ("kidney_infection", "Kidney infection"), ("sepsis", "Sepsis"),
                          ("obstruction", "Obstruction"), ("kidney_injury", "Kidney injury"), ("other", "Other")],
}
COUNTS_NIGHTS = {("visit_kind", "admission")}


def _statements(script):
    # Not executescript(): it commits any open transaction first, which would split a migration in two.
    return [s.strip() for s in script.split(";") if s.strip()]


def connect(path):
    """A connection in autocommit mode with Row results, foreign keys on and WAL."""
    con = sqlite3.connect(path, timeout=10, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 10000")
    con.execute("PRAGMA journal_mode = WAL")
    return con


@contextmanager
def transaction(con, mode="IMMEDIATE"):
    """BEGIN IMMEDIATE ... COMMIT, or ROLLBACK if the block raises."""
    con.execute(f"BEGIN {mode}")
    try:
        yield con
    except BaseException:
        con.execute("ROLLBACK")
        raise
    con.execute("COMMIT")


def _version(con):
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone():
        return 0
    row = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return int(row[0]) if row else 0


def _to_v1(con):
    """The whole schema, and the seeded lists. Runs once: migrate() only calls it on a database below version 1."""
    for stmt in _statements(SCHEMA):
        con.execute(stmt)
    for kind, items in SEED_LISTS.items():
        for order, (slug, label) in enumerate(items, 1):
            con.execute("INSERT INTO lists (kind, slug, label, sort_order, counts_nights) VALUES (?, ?, ?, ?, ?)",
                        (kind, slug, label, order, int((kind, slug) in COUNTS_NIGHTS)))


MIGRATIONS = {1: _to_v1}  # version -> step that brings version-1 up to it


def migrate(con):
    """Bring the database up to SCHEMA_VERSION in one transaction. Safe to run any number of times, and from
    several processes at once (BEGIN IMMEDIATE serializes them)."""
    with transaction(con):
        current = _version(con)
        if current > SCHEMA_VERSION:
            raise RuntimeError(f"database schema {current} is newer than this code ({SCHEMA_VERSION})")
        for version in range(current + 1, SCHEMA_VERSION + 1):
            MIGRATIONS[version](con)
        con.execute("INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (str(SCHEMA_VERSION),))
