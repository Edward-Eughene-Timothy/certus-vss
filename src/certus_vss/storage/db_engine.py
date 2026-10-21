from pathlib import Path
import sqlite3
import contextlib
from typing import Generator
from certus_vss.common.config import DB_PATH

STORAGE_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = STORAGE_DIR / "schema.sql"

def init_database(db_path: Path = DB_PATH, schema_path: Path = SCHEMA_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode = WAL;")
        cursor.execute("PRAGMA synchronous = NORMAL;")
        cursor.execute("PRAGMA cache_size = -16000;")
        cursor.execute("PRAGMA mmap_size = 67108864;")
        cursor.execute("PRAGMA temp_store = MEMORY;")
        with open(schema_path, "r", encoding="utf-8") as f:
            cursor.executescript(f.read())
        conn.commit()

@contextlib.contextmanager
def get_db_connection(db_path: Path = DB_PATH) -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA cache_size = -16000;")
        conn.execute("PRAGMA mmap_size = 67108864;")
        yield conn
    finally:
        conn.close()

if __name__ == "__main__":
    print(f"Initializing database at: {DB_PATH}")
    init_database()
    print("Database initialization successful.")
