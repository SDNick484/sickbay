"""ops/sickbay-backup run against a throwaway data folder: what it writes, what it refuses, what it prunes."""
import os
import shutil
import sqlite3
import stat
import subprocess
import tarfile
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "ops", "sickbay-backup")

pytestmark = pytest.mark.skipif(not (shutil.which("sqlite3") and shutil.which("tar")),
                                reason="needs the sqlite3 CLI and tar")


@pytest.fixture
def host(tmp_path):
    """A data folder with a small database and two files, and a backup folder that is not yet private."""
    data, backups = tmp_path / "data", tmp_path / "backups"
    (data / "files").mkdir(parents=True)
    con = sqlite3.connect(data / "sickbay.db")
    con.execute("create table t (v text)")
    con.execute("insert into t values ('one'), ('two')")
    con.commit()
    con.close()
    (data / "files" / "1.pdf").write_bytes(b"%PDF-synthetic\x00\x01\x02")
    (data / "files" / "2.png").write_bytes(bytes(range(256)))
    backups.mkdir(mode=0o755)
    env = {**os.environ, "DATA_DIR": str(data), "BACKUP_DIR": str(backups), "LOG": str(tmp_path / "log")}
    return data, backups, env, tmp_path / "log"


def run(env, check=True):
    return subprocess.run(["sh", SCRIPT], env=env, check=check, capture_output=True, text=True)


def today():
    return time.strftime("%Y-%m-%d")


def test_backup_archive_has_db_and_files_mode_600(host):
    data, backups, env, log = host
    run(env)
    archive = backups / f"sickbay-{today()}.tar.gz"
    assert [p.name for p in backups.iterdir()] == [archive.name], "only the archive is left (no part, no work folder)"
    assert stat.S_IMODE(archive.stat().st_mode) == 0o600
    assert stat.S_IMODE(backups.stat().st_mode) == 0o700
    with tarfile.open(archive) as tar:
        names = {m.name.rstrip("/") for m in tar.getmembers()}
    assert {"sickbay.db", "files", "files/1.pdf", "files/2.png"} <= names
    assert " ok " in log.read_text()


def test_backup_without_a_files_folder_still_archives_the_database(host):
    data, backups, env, _ = host
    shutil.rmtree(data / "files")
    run(env)
    with tarfile.open(backups / f"sickbay-{today()}.tar.gz") as tar:
        assert "sickbay.db" in {m.name for m in tar.getmembers()}


def test_corrupt_db_fails_and_leaves_no_archive(host):
    data, backups, env, log = host
    (data / "sickbay.db").write_bytes(b"this is not a database" * 200)
    r = run(env, check=False)
    assert r.returncode == 1
    assert list(backups.iterdir()) == [], "no archive, part file or work folder"
    assert "FAILED" in log.read_text()


def test_a_failed_run_keeps_yesterdays_archive(host):
    data, backups, env, _ = host
    keep = backups / "sickbay-2000-01-01.tar.gz"
    keep.write_text("yesterday")
    (data / "sickbay.db").write_bytes(b"garbage" * 500)
    assert run(env, check=False).returncode == 1
    assert [p.name for p in backups.iterdir()] == [keep.name]


def test_old_archives_pruned(host):
    data, backups, env, _ = host
    old, recent, other = (backups / "sickbay-2000-01-01.tar.gz", backups / "sickbay-2000-01-02.tar.gz",
                          backups / "notes.txt")
    for p in (old, recent, other):
        p.write_text("x")
    ancient, week = time.time() - 30 * 86400, time.time() - 7 * 86400
    os.utime(old, (ancient, ancient))
    os.utime(recent, (week, week))
    os.utime(other, (ancient, ancient))
    run(env)
    assert sorted(p.name for p in backups.iterdir()) == sorted(["notes.txt", recent.name, f"sickbay-{today()}.tar.gz"])


def test_keep_days_is_overridable(host):
    _, backups, env, _ = host
    old = backups / "sickbay-2000-01-01.tar.gz"
    old.write_text("x")
    three = time.time() - 3 * 86400
    os.utime(old, (three, three))
    run({**env, "KEEP_DAYS": "1"})
    assert not old.exists()


def test_backup_with_no_database_is_not_an_error(tmp_path):
    env = {**os.environ, "DATA_DIR": str(tmp_path / "none"), "BACKUP_DIR": str(tmp_path / "b"), "LOG": str(tmp_path / "log")}
    run(env)
    assert not (tmp_path / "b").exists()


def test_restore_round_trip(host, tmp_path):
    data, backups, env, _ = host
    run(env)
    scratch = tmp_path / "restore"
    scratch.mkdir()
    subprocess.run(["tar", "-xzf", str(backups / f"sickbay-{today()}.tar.gz"), "-C", str(scratch)], check=True)
    assert sqlite3.connect(scratch / "sickbay.db").execute("select v from t order by v").fetchall() == [("one",), ("two",)]
    for name in ("1.pdf", "2.png"):
        assert (scratch / "files" / name).read_bytes() == (data / "files" / name).read_bytes()
    assert not (scratch / "files").is_symlink()


def test_backup_of_a_live_wal_database_is_consistent(host, tmp_path):
    """A writer holding the database open in WAL mode, with rows only in the WAL, is captured whole."""
    data, backups, env, _ = host
    con = sqlite3.connect(data / "sickbay.db")
    con.execute("pragma journal_mode=wal")
    con.execute("insert into t values ('in the wal')")
    con.commit()
    run(env)
    scratch = tmp_path / "restore"
    scratch.mkdir()
    subprocess.run(["tar", "-xzf", str(backups / f"sickbay-{today()}.tar.gz"), "-C", str(scratch)], check=True)
    rows = sqlite3.connect(scratch / "sickbay.db").execute("select count(*) from t").fetchone()
    con.close()
    assert rows == (3,)
