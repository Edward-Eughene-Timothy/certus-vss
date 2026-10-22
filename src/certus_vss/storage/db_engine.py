
from pathlib import Path
import sqlite3
import contextlib
from typing import Generator

from certus_vss.common.config import DB_PATH

STORAGE_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = STORAGE_DIR / "schema.sql"

# Preserve the original project path when it is available.
storage_dir = STORAGE_DIR.parent
repo_root = storage_dir.parent.parent.parent
data_dir = repo_root / "data"
db_path = Path(DB_PATH)
schema_path = SCHEMA_PATH


def apply_automotive_pragmas(
    conn: sqlite3.Connection,
    read_only: bool = False,
) -> None:
    """Apply bounded SQLite settings and concurrency configuration."""
    cursor = conn.cursor()
    if not read_only:
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")

    cursor.execute("PRAGMA cache_size=-16000;")
    cursor.execute("PRAGMA mmap_size=33554432;")
    cursor.execute("PRAGMA temp_store=MEMORY;")
    cursor.execute("PRAGMA foreign_keys=ON;")


def init_database(
    db_path: Path = DB_PATH,
    schema_path: Path = SCHEMA_PATH,
) -> None:
    """Initialize the schema without deleting existing data."""
    db_path = Path(db_path)
    schema_path = Path(schema_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    if not schema_path.exists():
        raise FileNotFoundError(f"Schema file not found at {schema_path}")

    with open(schema_path, "r", encoding="utf-8") as file:
        schema_ddl = file.read()

    with sqlite3.connect(db_path) as conn:
        apply_automotive_pragmas(conn)
        conn.executescript(schema_ddl)
        conn.commit()

@contextlib.contextmanager
def get_db_connection(
    db_path: Path = DB_PATH,
) -> Generator[sqlite3.Connection, None, None]:
    """Provide a configured writable connection for existing callers."""
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        apply_automotive_pragmas(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

@contextlib.contextmanager
def get_writer_connection() -> Generator[sqlite3.Connection, None, None]:
    """Provide a configured writable database connection."""
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        apply_automotive_pragmas(conn)
        yield conn
    finally:
        conn.close()


@contextlib.contextmanager
def get_readonly_connection() -> Generator[sqlite3.Connection, None, None]:
    """Provide a strictly read-only connection for analytics and SLM queries."""
    uri = db_path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        apply_automotive_pragmas(conn, read_only=True)
        yield conn
    finally:
        conn.close()


if __name__ == "__main__":
    init_database()
    with get_readonly_connection() as conn:
        journal = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        cache = conn.execute("PRAGMA cache_size;").fetchone()[0]
        mmap = conn.execute("PRAGMA mmap_size;").fetchone()[0]
        foreign_keys = conn.execute("PRAGMA foreign_keys;").fetchone()[0]

        try:
            conn.execute("DROP TABLE vehicle_events")
            read_only_verified = False
        except sqlite3.OperationalError:
            read_only_verified = True

        print(
            f"[Verification] journal_mode={journal}, "
            f"cache_size={cache}, mmap_size={mmap}, "
            f"foreign_keys={foreign_keys}, "
            f"read_only_verified={read_only_verified}"
        )
