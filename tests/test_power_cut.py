"""
test_power_cut.py: Verifies SQLite WAL process-crash recovery on abrupt child termination.
"""
import os
import sqlite3
import multiprocessing as mp
from pathlib import Path


def _abrupt_writer_crash(db_path_str: str) -> None:
    conn = sqlite3.connect(db_path_str)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("BEGIN IMMEDIATE;")
    conn.execute("INSERT INTO crash_test VALUES (2, 999.9);")
    # Abruptly terminate process without committing or closing connection cleanly
    os._exit(1)


def test_sqlite_process_crash_recovery(tmp_path: Path) -> None:
    db_file = tmp_path / "wal_crash_recovery.db"

    with sqlite3.connect(db_file) as conn:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("CREATE TABLE crash_test (id INTEGER PRIMARY KEY, val REAL);")
        conn.execute("INSERT INTO crash_test VALUES (1, 42.8);")
        conn.commit()

    ctx = mp.get_context("spawn")
    proc = ctx.Process(target=_abrupt_writer_crash, args=(str(db_file),))
    proc.start()
    proc.join()
    assert proc.exitcode == 1

    # Reopen database and verify WAL rollback and integrity
    with sqlite3.connect(db_file) as conn_recovered:
        integrity = conn_recovered.execute("PRAGMA integrity_check;").fetchone()[0]
        assert integrity == "ok"
        rows = conn_recovered.execute("SELECT id, val FROM crash_test;").fetchall()
        assert rows == [(1, 42.8)], f"Uncommitted data leaked or committed data lost: {rows}"
