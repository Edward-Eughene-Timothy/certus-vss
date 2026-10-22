from pathlib import Path
import sqlite3
import contextlib
from typing import Generator

storage_dir= Path(__file__).parent.parent
repo_root=storage_dir.parent.parent.parent
data_dir=repo_root / "data"
db_path=data_dir / "certus_telemetry.db"
schema_path=storage_dir / "schema.sql"

def apply_automotive_pragmas(conn: sqlite3.Connection,read_only:bool =False) -> None:
    """
    Applies dteterministic memory bounds and wal concurrency
    """
    cursor=conn.cursor()
    if not read_only:
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
    
    cursor.execute("PRAGMA cache_size = -16000;")
    cursor.execute("PRAGMA mmap_size = 33554432;")  # 32MB
    cursor.execute("PRAGMA temp_store = MEMORY;")
    cursor.execute("PRAGMA foreign_keys = ON;")

def init_database() -> None:
    """
    Initializes the database schema if it does not exist.
    """
    data_dir.mkdir(parents=True,exist_ok=True)
    print(f"[INFO] Initializing database at {db_path}")

    if not schema_path.exists():
        raise FileNotFoundError(f"Schema file not found at {schema_path}")

    with open(schema_path,'r') as f:
        schema_ddl=f.read()

    with sqlite3.connect(db_path) as conn:
        apply_automotive_pragmas(conn,read_only=False)
        conn.executescript(schema_ddl)
        conn.commit()

        print("[INFO] Database schema initialized.")

    @contextlib.contextmanager
    def get_writer_connection() ->Generator[sqlite3.Connection,None,None]:
        """
        Context manager for a writable database connection.
        """
        conn=sqlite3.connect(db_path,timeout=10.0)
        conn.row_factory=sqlite3.Row
        try:
            apply_automotive_pragmas(conn,read_only=False)
            yield conn
        finally:
            conn.close()

    @contextlib.contextmanager
    def get_readonly_connection() -> Generator[sqlite3.Connection, None, None]:
        """
        Strictly Read-Only URI connection for the Analytics & SLM Query Engine.
        Guarantees the AI pipeline cannot mutate or corrupt historical telemetry.
        """
        ro_uri = f"file:{db_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(ro_uri, uri=True, timeout=5.0)
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
            cache=conn.execute("PRAGMA cache_size;").fetchone()[0]
            mmap=conn.execute("PRAGMA mmap_size;").fetchone()[0]
            fk=conn.execute("PRAGMA foreign_keys;").fetchone()[0]

            try:
                conn.execute("DROP TABLE vehicle_events")
                ro_verified = False
            except sqlite3.OperationalError:
                ro_verified = True  

            print(f"[Verification] journal_mode={journal}, cache_size={cache}, mmap_size={mmap}, foreign_keys={fk}, read_only_verified={ro_verified}")


    

    