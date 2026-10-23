"""
test_deterministic_math.py: Validates mathematical parity between SQLite
bytecode aggregations and Python reference implementations over full windows.
"""

import math
import time
from certus_vss.storage.db_engine import get_readonly_connection
from certus_vss.analytics.query_engine import DeterministicQueryEngine


def get_latest_telemetry_epoch_s() -> int:
    with get_readonly_connection() as conn:
        row = conn.execute("SELECT MAX(bucket_epoch_m) FROM telemetry_rollup_1m;").fetchone()
        if row and row[0] is not None:
            return int(row[0]) * 60
    return int(time.time())


def test_deterministic_computations() -> None:
    engine = DeterministicQueryEngine()
    test_signal = "Vehicle.Powertrain.TractionBattery.Temperature"
    
    end_s = get_latest_telemetry_epoch_s()
    start_s = end_s - (3 * 86400)  # Exactly 3 days (4,320 minutes)

    engine_result = engine.get_signal_metrics(
        vss_path=test_signal,
        start_epoch_s=start_s,
        end_epoch_s=end_s,
    )
    assert engine_result["status"] == "SUCCESS", "Engine query returned non-success status"
    sql_agg = engine_result["aggregates"]

    start_m = start_s // 60
    end_m = end_s // 60
    query = """
        SELECT val_avg, val_min, val_max, sample_count
        FROM telemetry_rollup_1m
        WHERE vss_path = ? AND bucket_epoch_m BETWEEN ? AND ?;
    """

    with get_readonly_connection() as conn:
        rows = conn.execute(query, (test_signal, start_m, end_m)).fetchall()

    assert len(rows) > 0, "Boundary Failure: No rollup rows found in the designated window"
    assert len(rows) == engine_result["window_minutes"], "Row count mismatch between test and engine"
    assert len(rows) == 4321, f"Expected 4321 inclusive minute buckets, got {len(rows)}"

    val_avgs = [r["val_avg"] for r in rows]
    val_mins = [r["val_min"] for r in rows]
    val_maxs = [r["val_max"] for r in rows]
    sample_counts = [r["sample_count"] for r in rows]

    py_unweighted_avg = round(sum(val_avgs) / len(val_avgs), 2)
    total_weighted_sum = sum(a * s for a, s in zip(val_avgs, sample_counts))
    total_samples = sum(sample_counts)
    py_weighted_avg = round(total_weighted_sum / total_samples, 2)

    py_min = round(min(val_mins), 2)
    py_max = round(max(val_maxs), 2)
    py_spread = round(py_max - py_min, 2)

    print("=" * 70)
    print("DETERMINISTIC ARITHMETIC VERIFICATION (FULL 3-DAY WINDOW)")
    print("=" * 70)
    print(f"Evaluated Signal     : {test_signal}")
    print(f"Time Boundaries      : [{start_m}m to {end_m}m] ({len(rows):,} minutes evaluated)")
    print(f"Total Underlying Raw : {total_samples:,} samples (at 10 Hz)")
    print("-" * 70)
    print(f"Average (Engine SQL) : {sql_agg['average']}")
    print(f"Average (Python Unw) : {py_unweighted_avg}")
    print(f"Average (Python Wtd) : {py_weighted_avg}")
    print(f"Minimum Value        : SQL={sql_agg['min']}  | Python={py_min}")
    print(f"Maximum Value        : SQL={sql_agg['max']}  | Python={py_max}")
    print(f"Spread (Delta)       : SQL={sql_agg['spread']} | Python={py_spread}")
    print("=" * 70)

    assert math.isclose(sql_agg["average"], py_unweighted_avg, abs_tol=0.01)
    assert math.isclose(sql_agg["average"], py_weighted_avg, abs_tol=0.01)
    assert sql_agg["min"] == py_min
    assert sql_agg["max"] == py_max
    assert sql_agg["spread"] == py_spread

    print("VERDICT: PASS (100% deterministic mathematical parity verified)")


if __name__ == "__main__":
    test_deterministic_computations()
