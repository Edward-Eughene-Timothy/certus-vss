"""
db_engine.py: Core SQLite storage engine for CertusVSS.
Exports connection managers with automotive PRAGMA settings and configurable paths for Track A & Track B.
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

# Aliases expected by Track B modules
storage_dir = STORAGE_DIR.parent
repo_root = REPO_ROOT
data_dir = DATA_DIR
db_path = DB_PATH
schema_path = SCHEMA_PATH


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


def init_database(
    db_path: Path = DB_PATH,
    schema_path: Path = SCHEMA_PATH,
) -> None:
    """Initializes the database and executes schema.sql without deleting existing data."""
    target_db = Path(db_path)
    target_schema = Path(schema_path)
    target_db.parent.mkdir(parents=True, exist_ok=True)

    if not target_schema.exists():
        raise FileNotFoundError(f"Schema file missing at: {target_schema}")

    with open(target_schema, "r", encoding="utf-8") as f:
        schema_ddl = f.read()

    with sqlite3.connect(target_db) as conn:
        apply_automotive_pragmas(conn, read_only=False)
        conn.executescript(schema_ddl)
        conn.commit()


@contextlib.contextmanager
def get_db_connection(
    db_path: Path = DB_PATH,
) -> Generator[sqlite3.Connection, None, None]:
    """Provide a configured writable connection with automatic commit/rollback."""
    conn = sqlite3.connect(Path(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        apply_automotive_pragmas(conn, read_only=False)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextlib.contextmanager
def get_writer_connection(
    db_path: Path = DB_PATH,
) -> Generator[sqlite3.Connection, None, None]:
    """Read-Write connection for the ingestion worker and historical seeder."""
    conn = sqlite3.connect(Path(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        apply_automotive_pragmas(conn, read_only=False)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextlib.contextmanager
def get_readonly_connection(
    db_path: Path = DB_PATH,
) -> Generator[sqlite3.Connection, None, None]:
    """Strictly Read-Only URI connection for analytics and SLM inference."""
    ro_uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(ro_uri, uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        apply_automotive_pragmas(conn, read_only=True)
        yield conn
    finally:
        conn.close()


if __name__ == "__main__":
    init_database()
