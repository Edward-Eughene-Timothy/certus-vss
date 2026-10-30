"""
test_integration_e2e.py: End-to-end integration test uniting Track B ingestion/event capture
with Track A GBNF-constrained SLM inference and Parity Gate validation.
"""
import time
from certus_vss.common.config import DB_PATH
from certus_vss.ingestion.ring_buffer import TelemetryRingBuffer, SIGNAL_PATHS
from certus_vss.storage.event_capture import record_safety_event
from certus_vss.storage.rollup_worker import flush_buffer_to_rollup
from certus_vss.inference.llama_runtime import SLMDiagnosticRuntime


def test_end_to_end_track_a_track_b_integration() -> None:
    buf = TelemetryRingBuffer(maxlen=200)
    now = time.time()
    trigger_signal = "Vehicle.Powertrain.TractionBattery.Temperature"
    trigger_val = 47.5

    # 1. Populate ring buffer around event time
    for seq in range(10):
        ts = now - 5.0 + (seq * 0.5)
        signals = {path: 35.0 for path in SIGNAL_PATHS}
        signals[trigger_signal] = trigger_val if seq == 9 else 35.0
        assert buf.append({
            "timestamp": ts,
            "sequence": seq,
            "signals": signals,
        })

    # 2. Record safety event using Track B event capture
    event_id = record_safety_event(
        buffer=buf,
        event={
            "timestamp": now,
            "event_type": "thermal_warning",
            "dtc_code": "P0A7F",
            "trigger_signal": trigger_signal,
            "intensity": trigger_val,
        },
        db_path=DB_PATH,
    )
    assert isinstance(event_id, int)
    assert event_id > 0

    # 3. Execute Track A SLM Diagnostic Runtime & Parity Gate
    runtime = SLMDiagnosticRuntime()
    try:
        res = runtime.diagnose_event_end_to_end(event_id=event_id, deadline_ms=1850.0)
        assert res["status"] == "SUCCESS"
        assert res["latency_ms"] < 1850.0
        assert res["diagnostics"]["evidence_status"] == "VERIFIED"
        assert str(trigger_val) in res["diagnostics"]["summary"]
    finally:
        runtime.close()
