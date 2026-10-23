"""
db_engine.py: Core SQLite storage engine for CertusVSS.
Exports connection managers with automotive PRAGMA settings.
"""

from pathlib import Path
import sqlite3
import contextlib
from typing import Generator

STORAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = STORAGE_DIR.parent.parent.parent
DATA_DIR = REPO_ROOT / "data"
DB_PATH = DATA_DIR / "certus_telemetry.db"
SCHEMA_PATH = STORAGE_DIR / "schema.sql"


def apply_automotive_pragmas(conn: sqlite3.Connection, read_only: bool = False) -> None:
    """Applies mission-critical performance, concurrency, and durability PRAGMAs."""
    cursor = conn.cursor()
    if not read_only:
        cursor.execute("PRAGMA journal_mode = WAL;")
        cursor.execute("PRAGMA synchronous = NORMAL;")
    
    # 16 MB cache ceiling
    cursor.execute("PRAGMA cache_size = -16000;")
    # 32 MB memory-mapped I/O bound
    cursor.execute("PRAGMA mmap_size = 33554432;")
    cursor.execute("PRAGMA temp_store = MEMORY;")
    cursor.execute("PRAGMA foreign_keys = ON;")


def init_database() -> None:
    """Initializes the database and executes schema.sql."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(f"Schema file missing at: {SCHEMA_PATH}")

    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema_ddl = f.read()

    with sqlite3.connect(DB_PATH) as conn:
        apply_automotive_pragmas(conn, read_only=False)
        conn.executescript(schema_ddl)
        conn.commit()


@contextlib.contextmanager
def get_writer_connection() -> Generator[sqlite3.Connection, None, None]:
    """Read-Write connection for the ingestion worker and historical seeder."""
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        apply_automotive_pragmas(conn, read_only=False)
        yield conn
    finally:
        conn.close()


@contextlib.contextmanager
def get_readonly_connection() -> Generator[sqlite3.Connection, None, None]:
    """Strictly Read-Only URI connection for analytics and SLM inference."""
    ro_uri = f"file:{DB_PATH.as_posix()}?mode=ro"
    conn = sqlite3.connect(ro_uri, uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        apply_automotive_pragmas(conn, read_only=True)
        yield conn
    finally:
        conn.close()


# Backward-compatibility alias
get_db_connection = get_writer_connection

if __name__ == "__main__":
    init_database()