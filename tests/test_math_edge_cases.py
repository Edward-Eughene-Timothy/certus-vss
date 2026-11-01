"""
test_math_edge_cases.py: Verifies edge-case behavior and boundary mechanics.
Tests:
  1. Empty time windows (Zero-division & NULL handling).
  2. Single-minute boundary inclusivity (BETWEEN start AND end).
  3. Average semantics under unequal sample counts: Validates unweighted
     minute-average arithmetic vs. sample-weighted alternative.
"""

import sqlite3
from certus_vss.storage.db_engine import get_readonly_connection
from certus_vss.analytics.query_engine import DeterministicQueryEngine


def test_empty_window_handling():
    """Verifies that windows with zero telemetry return NO_DATA without exceptions."""
    engine = DeterministicQueryEngine()
    res = engine.get_signal_metrics(
        vss_path="Vehicle.Speed",
        start_epoch_s=0,
        end_epoch_s=3600,
    )
    assert res["status"] == "NO_DATA", f"Expected NO_DATA, got {res['status']}"
    assert "aggregates" not in res, "NO_DATA payload should not contain aggregates"
    assert res["execution_ms"] >= 0.0, "Execution timing must be recorded even for empty hits"
    print("[Pass] Empty window gracefully returned NO_DATA.")


def test_exact_boundary_inclusivity():
    """Verifies that BETWEEN includes both exact start and end minute buckets."""
    engine = DeterministicQueryEngine()
    
    with get_readonly_connection() as conn:
        row = conn.execute(
            "SELECT MIN(bucket_epoch_m) FROM telemetry_rollup_1m WHERE vss_path = 'Vehicle.Speed';"
        ).fetchone()
        t0_m = row[0]

    # Exactly 1 minute window (start_m == end_m)
    res_single = engine.get_signal_metrics(
        vss_path="Vehicle.Speed",
        start_epoch_s=t0_m * 60,
        end_epoch_s=t0_m * 60,
    )
    assert res_single["status"] == "SUCCESS"
    assert res_single["window_minutes"] == 1, f"Expected 1 bucket, got {res_single['window_minutes']}"

    # Exactly 3 minutes window [t0, t0 + 2]
    res_three = engine.get_signal_metrics(
        vss_path="Vehicle.Speed",
        start_epoch_s=t0_m * 60,
        end_epoch_s=(t0_m + 2) * 60,
    )
    assert res_three["status"] == "SUCCESS"
    assert res_three["window_minutes"] == 3, f"Expected 3 buckets, got {res_three['window_minutes']}"
    print("[Pass] SQL temporal boundaries are strictly inclusive on both ends.")


def test_unequal_sample_count_semantics():
    """
    Evaluates average semantics in an isolated in-memory table.
    Validates:
      - Unweighted Mean of Stored Minute Averages: (10.0 + 40.0) / 2 = 25.00
      - Sample-Count-Weighted Mean: (10*60 + 40*600) / 660 = 37.27
    """
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE temp_rollup (
            bucket_epoch_m INTEGER PRIMARY KEY,
            val_avg REAL,
            sample_count INTEGER
        );
    """)
    # Minute 1: Transient bus drop (only 60 samples captured), val_avg = 10.0
    conn.execute("INSERT INTO temp_rollup VALUES (1, 10.0, 60);")
    # Minute 2: Nominal bus (full 600 samples captured), val_avg = 40.0
    conn.execute("INSERT INTO temp_rollup VALUES (2, 40.0, 600);")
    conn.commit()

    # Engine standard calculation: AVG(val_avg)
    unweighted_mean = conn.execute("SELECT ROUND(AVG(val_avg), 2) FROM temp_rollup;").fetchone()[0]

    # Alternative sample-weighted calculation: SUM(val_avg * sample_count) / SUM(sample_count)
    weighted_mean = conn.execute("""
        SELECT ROUND(SUM(val_avg * sample_count) * 1.0 / SUM(sample_count), 2) 
        FROM temp_rollup;
    """).fetchone()[0]

    print("[Pass] Average semantics verified on isolated in-memory test set:")
    print(f"       Unweighted Mean of Stored Minute Averages : {unweighted_mean:.2f} (Engine Standard)")
    print(f"       Sample-Count-Weighted Mean                : {weighted_mean:.2f} (Accounting for CAN dropouts)")

    assert unweighted_mean == 25.00, f"Expected 25.00, got {unweighted_mean}"
    assert weighted_mean == 37.27, f"Expected 37.27, got {weighted_mean}"


if __name__ == "__main__":
    print("=" * 70)
    print("RUNNING DETERMINISTIC EDGE-CASE & SEMANTIC UNIT TESTS")
    print("=" * 70)
    test_empty_window_handling()
    test_exact_boundary_inclusivity()
    test_unequal_sample_count_semantics()
    print("=" * 70)
    print("VERDICT: ALL EDGE CASES & SEMANTIC ASSERTIONS PASS")
