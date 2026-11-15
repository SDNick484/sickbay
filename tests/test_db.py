import os
import sqlite3

import pytest

import db
from conftest import list_id, mk_event, mk_row

TABLES = {"meta", "lists", "doctors", "events", "stones", "visits", "procedures", "complications", "followups",
          "event_doctors", "notes", "files", "event_files", "deleted_documents", "imports", "suggestions"}

SEEDED = {
    "outcome": ["passed", "removed", "other"],
    "location": ["kidney", "ureter", "bladder", "unknown"],
    "stone_type": ["calcium_oxalate", "calcium_phosphate", "uric_acid", "struvite", "cystine", "mixed", "unknown", "other"],
    "visit_kind": ["er", "urgent_care", "admission", "outpatient", "office"],
    "procedure_kind": ["stent_placement", "stent_removal", "ureteroscopy", "swl", "pcnl", "nephrostomy", "other"],
    "complication_kind": ["uti", "kidney_infection", "sepsis", "obstruction", "kidney_injury", "other"],
}


def test_migrate_creates_every_table_and_version(con):
    names = {r["name"] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert names == TABLES
    assert db.SCHEMA_VERSION == 1
    assert con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "1"
    indexes = {r["name"] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL")}
    assert {"events_started", "suggestions_state"} <= indexes


def test_migrate_is_idempotent_and_seeds_once(con):
    db.migrate(con); db.migrate(con)
    assert con.execute("SELECT COUNT(*) FROM lists WHERE kind='procedure_kind'").fetchone()[0] == 7
    assert con.execute("SELECT COUNT(*) FROM lists").fetchone()[0] == sum(map(len, SEEDED.values()))
    assert con.execute("SELECT COUNT(*) FROM meta WHERE key='schema_version'").fetchone()[0] == 1


def test_seeded_lists_in_order_with_labels(con):
    for kind, slugs in SEEDED.items():
        rows = con.execute("SELECT slug FROM lists WHERE kind = ? ORDER BY sort_order, id", (kind,)).fetchall()
        assert [r["slug"] for r in rows] == slugs, kind
    assert con.execute("SELECT label FROM lists WHERE kind='procedure_kind' AND slug='swl'").fetchone()[0] == "Shock-wave lithotripsy"
    assert con.execute("SELECT label FROM lists WHERE kind='outcome' AND slug='passed'").fetchone()[0] == "Passed on its own"
    assert con.execute("SELECT COUNT(*) FROM lists WHERE retired != 0").fetchone()[0] == 0


def test_only_admission_counts_nights(con):
    rows = con.execute("SELECT slug FROM lists WHERE counts_nights=1").fetchall()
    assert [r["slug"] for r in rows] == ["admission"]


def test_resolved_before_started_is_refused_by_schema(con):
    with pytest.raises(sqlite3.IntegrityError):
        mk_event(con, "2024-03-10", resolved_on="2024-03-01")


def test_same_day_resolution_and_open_event_are_fine(con):
    assert mk_event(con, "2024-03-10", resolved_on="2024-03-10", outcome="passed")
    assert mk_event(con, "2024-04-01")


def test_visit_end_before_start_is_refused_by_schema(con):
    e = mk_event(con, "2024-03-01")
    with pytest.raises(sqlite3.IntegrityError):
        mk_row(con, "visits", event_id=e, kind_id=list_id(con, "visit_kind", "er"), from_date="2024-03-02", to_date="2024-03-01")


@pytest.mark.parametrize("size", [0, -5])
def test_stone_size_must_be_positive(con, size):
    e = mk_event(con, "2024-03-01")
    with pytest.raises(sqlite3.IntegrityError):
        mk_row(con, "stones", event_id=e, size_tenths=size)


def test_list_kind_and_label_are_checked(con):
    with pytest.raises(sqlite3.IntegrityError):
        mk_row(con, "lists", kind="colour", label="Blue")
    with pytest.raises(sqlite3.IntegrityError):
        mk_row(con, "lists", kind="outcome", label="Other")  # UNIQUE (kind, label)


def test_foreign_keys_enforced(con):
    with pytest.raises(sqlite3.IntegrityError):
        mk_row(con, "notes", event_id=999, author="alex@example.com", created_at=0, updated_at=0, body="x")


def test_deleting_an_event_cascades_to_its_rows_but_keeps_files(con):
    e = mk_event(con, "2024-03-01")
    mk_row(con, "notes", event_id=e, author="alex@example.com", created_at=0, updated_at=0, body="x")
    mk_row(con, "stones", event_id=e, size_tenths=50)
    f = mk_row(con, "files", storage_id="s1", name="a.pdf", mime="application/pdf", size=1, sha256="0" * 64,
               origin="upload", created_by="alex@example.com", created_at=0)
    mk_row(con, "event_files", event_id=e, file_id=f)
    con.execute("DELETE FROM events WHERE id = ?", (e,))
    for table in ("notes", "stones", "event_files"):
        assert con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    assert con.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 1


def test_connect_settings(tmp_path):
    c = db.connect(str(tmp_path / "x.db"))
    try:
        assert c.isolation_level is None  # autocommit
        assert c.row_factory is sqlite3.Row
        assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        c.close()


def test_transaction_rolls_back_on_error(con):
    with pytest.raises(RuntimeError):
        with db.transaction(con):
            mk_event(con, "2024-03-01")
            raise RuntimeError("stop")
    assert con.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    with db.transaction(con):
        mk_event(con, "2024-03-01")
    assert con.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_newer_database_than_code_is_refused(tmp_path):
    c = db.connect(str(tmp_path / "x.db"))
    try:
        db.migrate(c)
        c.execute("UPDATE meta SET value = '99' WHERE key = 'schema_version'")
        with pytest.raises(RuntimeError):
            db.migrate(c)
    finally:
        c.close()


def test_init_db_creates_data_and_files_dirs():
    import settings
    assert os.path.isdir(settings.DATA_DIR) and os.path.isdir(settings.FILES_DIR)
    assert settings.FILES_DIR == os.path.join(settings.DATA_DIR, "files")
    assert settings.DB_PATH == os.path.join(settings.DATA_DIR, "sickbay.db")
