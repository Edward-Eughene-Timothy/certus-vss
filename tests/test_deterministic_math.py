"""
test_deterministic_math.py: Hermetically isolated verification of analytical metrics.
Validates statistical determinism on an ephemeral, self-seeded SQLite database.
"""
import math
import sqlite3
from pathlib import Path
from certus_vss.storage.db_engine import init_database
from certus_vss.analytics.query_engine import DeterministicQueryEngine


def test_deterministic_computations(tmp_path: Path) -> None:
    """Verifies analytical aggregates on an isolated database with analytically known data."""
    test_db = tmp_path / "hermetic_test.sqlite"
    init_database(db_path=test_db)

    with sqlite3.connect(test_db) as conn:
        records = []
        for m in range(100):
            records.append((
                m,
                "Vehicle.Speed",
                "km/h",            # unit (NOT NULL)
                float(50 + m),     # val_avg
                float(40 + m),     # val_min
                float(60 + m),     # val_max
                20.0,              # val_delta
                600,               # sample_count
                "HIGHWAY_CRUISE",  # drive_context
                "SEED_SYNTHETIC"   # provenance (NOT NULL)
            ))
        conn.executemany(
            """
            INSERT INTO telemetry_rollup_1m 
            (bucket_epoch_m, vss_path, unit, val_avg, val_min, val_max, val_delta, sample_count, drive_context, provenance)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            records
        )
        conn.commit()

    engine = DeterministicQueryEngine(db_path=test_db)

    # Query window: minute 10 to minute 59 (50 minutes total)
    start_s = 10 * 60
    end_s = 59 * 60

    result = engine.get_signal_metrics(
        vss_path="Vehicle.Speed",
        start_epoch_s=start_s,
        end_epoch_s=end_s
    )

    assert result["status"] == "SUCCESS", f"Query failed: {result}"
    aggs = result["aggregates"]

    assert math.isclose(aggs["min"], 50.0, abs_tol=1e-3)
    assert math.isclose(aggs["max"], 119.0, abs_tol=1e-3)
    assert math.isclose(aggs["average"], 84.5, abs_tol=1e-3)
