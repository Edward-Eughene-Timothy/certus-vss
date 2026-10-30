"""
test_boundary_hardening.py: Phase 3 hardening tests addressing diagnostic integrity,
60-second rollup window boundaries, and ring buffer wraparound guarantees.
"""
import math
import sqlite3
import time
from certus_vss.common.config import DB_PATH
from certus_vss.ingestion.ring_buffer import TelemetryRingBuffer, SIGNAL_PATHS
from certus_vss.storage.rollup_worker import flush_buffer_to_rollup
from certus_vss.storage.event_capture import record_safety_event
from certus_vss.inference.llama_runtime import SLMDiagnosticRuntime


def test_full_60s_rollup_window_aggregation(tmp_path) -> None:
    """Verifies that an entire 60-second window (600 frames @ 10 Hz) aggregates accurately."""
    db_path = tmp_path / "rollup_60s.sqlite"
    buf = TelemetryRingBuffer(maxlen=1000)

    # Minute bucket 100: t = 6000.0 to t = 6059.9 (exactly 600 frames at 10 Hz)
    # Speed increments linearly from 10.0 to 70.0 km/h
    for i in range(600):
        t = 6000.0 + (i * 0.1)
        speed = 10.0 + (60.0 * (i / 599.0))
        signals = {p: None for p in SIGNAL_PATHS}
        signals["Vehicle.Speed"] = speed
        assert buf.append({"timestamp": t, "sequence": i, "signals": signals})

    # Advance stream to t = 6061.0 so minute 100 is fully closed
    assert buf.append({"timestamp": 6061.0, "sequence": 600, "signals": {p: 0.0 for p in SIGNAL_PATHS}})

    inserted = flush_buffer_to_rollup(buf, db_path=db_path)
    assert inserted >= 1

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT val_avg, val_min, val_max, val_delta, sample_count FROM telemetry_rollup_1m "
            "WHERE vss_path='Vehicle.Speed' AND bucket_epoch_m=100"
        ).fetchone()

        assert row is not None
        avg_val, min_val, max_val, delta_val, count = row
        assert count == 600
        assert math.isclose(min_val, 10.0, rel_tol=1e-2)
        assert math.isclose(max_val, 70.0, rel_tol=1e-2)
        assert math.isclose(avg_val, 40.0, abs_tol=0.2)
        assert math.isclose(delta_val, 60.0, rel_tol=1e-2)


def test_ring_buffer_wraparound_eviction_integrity() -> None:
    """Verifies FIFO eviction and continuous bit accuracy across ring buffer wraparound."""
    capacity = 100
    buf = TelemetryRingBuffer(maxlen=capacity)

    # Push 150 frames to force eviction of the first 50 frames
    for i in range(150):
        signals = {p: float(i) for p in SIGNAL_PATHS}
        assert buf.append({"timestamp": 1000.0 + i, "sequence": i, "signals": signals})

    assert len(buf) == capacity
    snapshot = buf.snapshot()
    assert len(snapshot) == capacity

    # Oldest retained frame must be sequence 50; newest sequence 149
    assert snapshot[0]["sequence"] == 50
    assert snapshot[-1]["sequence"] == 149
    assert snapshot[0]["signals"]["Vehicle.Speed"] == 50.0
    assert snapshot[-1]["signals"]["Vehicle.Speed"] == 149.0


def test_null_dtc_diagnostic_purity() -> None:
    """Verifies that events without an ECU DTC output an Event tag without fabricating an SAE code."""
    buf = TelemetryRingBuffer(maxlen=100)
    now = time.time()
    trigger_signal = "Vehicle.Powertrain.TractionBattery.Temperature"
    trigger_val = 49.2

    for seq in range(10):
        ts = now - 5.0 + (seq * 0.5)
        signals = {p: 32.0 for p in SIGNAL_PATHS}
        signals[trigger_signal] = trigger_val if seq == 9 else 32.0
        assert buf.append({"timestamp": ts, "sequence": seq, "signals": signals})

    event_id = record_safety_event(
        buffer=buf,
        event={
            "timestamp": now,
            "event_type": "thermal_warning",
            "dtc_code": None,
            "trigger_signal": trigger_signal,
            "intensity": trigger_val,
        },
        db_path=DB_PATH,
    )

    runtime = SLMDiagnosticRuntime()
    try:
        res = runtime.diagnose_event_end_to_end(event_id=event_id, deadline_ms=1850.0)
        assert res["status"] == "SUCCESS"
        assert res["diagnostics"]["evidence_status"] == "VERIFIED"
        assert "Event THERMAL_WARNING at 49.2" in res["diagnostics"]["summary"]
        assert "Fault P0A7F" not in res["diagnostics"]["summary"]
    finally:
        runtime.close()
