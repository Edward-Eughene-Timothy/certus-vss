from certus_vss.storage.db_engine import get_db_connection, init_database

def test_pragmas():
    init_database()
    with get_db_connection() as conn:
        mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        assert mode.lower() == "wal"
