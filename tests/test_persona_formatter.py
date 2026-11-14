"""
test_persona_formatter.py: Hermetic tests for persona_formatter.py.
"""
import sqlite3
import time
from pathlib import Path
import pytest

import certus_vss.storage.db_engine as db_mod
import certus_vss.analytics.evidence_builder as eb_mod
import certus_vss.analytics.query_engine as qe_mod
from certus_vss.analytics.evidence_builder import EvidenceBuilder
from certus_vss.presentation.persona_formatter import (
    IncompleteEvidenceError,
    UncertifiedDiagnosticError,
    format_persona_view,
)


@pytest.fixture
def base_evidence():
    return {
        "event": {
            "trigger_signal": "Vehicle.Powertrain.TractionBattery.Temperature",
            "trigger_value": 47.5,
            "event_category": "THERMAL",
            "dtc_code": "P0A7F",
            "severity": "CRITICAL",
        },
        "waveform": {"min": 36.0, "max": 43.0},
        "baseline": {"avg": 28.67, "min": 25.0, "max": 32.0},
    }


@pytest.fixture
def base_diagnostic():
    return {
        "summary": "Fault P0A7F at 47.5",
        "key_facts": ["Trigger value 47.5"],
        "evidence_status": "VERIFIED",
    }


def test_driver_thermal_warning_descriptive_alert(base_evidence, base_diagnostic):
    view = format_persona_view(base_evidence, base_diagnostic, role="driver")
    assert view["role"] == "driver"
    assert view["alert_level"] == "CRITICAL"
    assert view["measured_value"] == {"value": 47.5, "unit": "celsius"}
    assert "Refer to vehicle safety guidance" in view["action"]
    assert "policy_source" not in view


def test_driver_advisory_no_false_nominal_claims(base_evidence, base_diagnostic):
    base_evidence["event"]["severity"] = "ADVISORY"
    base_evidence["event"]["trigger_value"] = 30.5
    base_diagnostic["summary"] = "Event at 30.5"
    base_diagnostic["key_facts"] = ["Trigger value 30.5"]
    view = format_persona_view(base_evidence, base_diagnostic, role="driver")
    assert view["alert_level"] == "ADVISORY"
    assert "Diagnostic notice recorded" in view["action"]
    assert "nominal" not in view["action"]


def test_technician_valid_dtc_and_procedure(base_evidence, base_diagnostic):
    manual = {"P0A7F": "PROC-DOC-P0A7F"}
    view = format_persona_view(base_evidence, base_diagnostic, role="technician", service_manual_lookup=manual)
    assert view["dtc_code"] == "P0A7F"
    assert view["observed_trigger"] == {"value": 47.5, "unit": "celsius"}
    assert view["waveform_bounds"]["min"] == {"value": 36.0, "unit": "celsius"}
    assert view["baseline_stats"]["avg"] == {"value": 28.67, "unit": "celsius"}
    assert view["service_procedure"] == "PROC-DOC-P0A7F"


def test_technician_missing_dtc_shows_unavailable(base_evidence, base_diagnostic):
    base_evidence["event"]["dtc_code"] = None
    view = format_persona_view(base_evidence, base_diagnostic, role="technician")
    assert view["dtc_code"] == "UNAVAILABLE"
    assert view["service_procedure"] == "UNAVAILABLE"


def test_boundary_parity_verification_rejects_hallucination(base_evidence, base_diagnostic):
    base_diagnostic["summary"] = "Fault at 99.9"
    with pytest.raises(UncertifiedDiagnosticError, match="Boundary diagnostic parity verification failed"):
        format_persona_view(base_evidence, base_diagnostic, role="driver")


def test_unverified_status_flag_rejected(base_evidence, base_diagnostic):
    base_diagnostic["evidence_status"] = "UNVERIFIED"
    with pytest.raises(UncertifiedDiagnosticError, match="lacks 'evidence_status' == 'VERIFIED'"):
        format_persona_view(base_evidence, base_diagnostic, role="driver")


def test_validation_order_raises_incomplete_evidence_error(base_evidence, base_diagnostic):
    del base_evidence["baseline"]["avg"]
    with pytest.raises(IncompleteEvidenceError, match="Missing required numeric evidence: avg"):
        format_persona_view(base_evidence, base_diagnostic, role="technician")

    base_evidence["baseline"]["avg"] = 28.67
    del base_evidence["waveform"]["max"]
    with pytest.raises(IncompleteEvidenceError, match="Missing required numeric evidence: max"):
        format_persona_view(base_evidence, base_diagnostic, role="technician")


def test_empty_string_validation(base_evidence, base_diagnostic):
    base_evidence["event"]["trigger_signal"] = "   "
    with pytest.raises(IncompleteEvidenceError, match="Missing or empty 'trigger_signal'"):
        format_persona_view(base_evidence, base_diagnostic, role="driver")

    base_evidence["event"]["trigger_signal"] = "Vehicle.Speed"
    base_diagnostic["summary"] = ""
    with pytest.raises(IncompleteEvidenceError, match="Field 'summary' must be a non-empty string"):
        format_persona_view(base_evidence, base_diagnostic, role="driver")


def test_unknown_signal_path_resolves_to_unknown_unit(base_evidence, base_diagnostic):
    base_evidence["event"]["trigger_signal"] = "Vehicle.Unknown.Component.Value"
    view = format_persona_view(base_evidence, base_diagnostic, role="driver")
    assert view["measured_value"]["unit"] == "UNKNOWN"


def test_unknown_severity_produces_unclassified_and_assessment_required(base_evidence, base_diagnostic):
    base_evidence["event"]["severity"] = "NON_STANDARD_TIER"
    driver_view = format_persona_view(base_evidence, base_diagnostic, role="driver")
    assert driver_view["alert_level"] == "UNCLASSIFIED"
    assert "unclassified telemetry event" in driver_view["action"]

    fleet_view = format_persona_view(base_evidence, base_diagnostic, role="fleet_operator")
    assert fleet_view["asset_status"] == "ASSESSMENT_REQUIRED"
    assert fleet_view["maintenance_priority"] == "ASSESSMENT_REQUIRED"


def test_invalid_role_raises_value_error(base_evidence, base_diagnostic):
    with pytest.raises(ValueError, match="Invalid role: 'passenger'"):
        format_persona_view(base_evidence, base_diagnostic, role="passenger")


def test_hermetic_evidence_builder_integration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    Hermetic test: Creates a clean database using true schema.sql, seeds actual tables
    (vehicle_events, fault_freeze_frames, telemetry_rollup_1m), points get_readonly_connection
    to it, and runs EvidenceBuilder.
    """
    db_file = tmp_path / "hermetic_test.db"

    # Initialize schema using db_engine's own init_database function
    db_mod.init_database(db_path=db_file, schema_path=db_mod.SCHEMA_PATH)

    event_ts = 1_700_000_000
    signal_path = "Vehicle.Speed"
    trigger_val = 115.5

    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO vehicle_events (
            event_id, timestamp_utc_s, event_category, dtc_code,
            severity, trigger_signal, trigger_value, context_json, provenance
        ) VALUES (42, ?, 'OVERSPEED', 'P1234', 'WARNING', ?, ?, '{}', 'HERMETIC_TEST')
        """,
        (event_ts, signal_path, trigger_val)
    )

    # Seed 10Hz freeze frames (-1000ms to +1000ms)
    for offset in range(-1000, 1100, 100):
        cur.execute(
            """
            INSERT INTO fault_freeze_frames (event_id, offset_ms, vss_path, raw_value)
            VALUES (42, ?, ?, ?)
            """,
            (offset, signal_path, 100.0 + (offset / 100))
        )

    # Seed 1-minute rollup
    bucket_m = event_ts // 60
    cur.execute(
        """
        INSERT INTO telemetry_rollup_1m (
            vss_path, bucket_epoch_m, val_avg, val_min, val_max,
            val_delta, sample_count, unit, drive_context, provenance
        ) VALUES (?, ?, 85.0, 60.0, 110.0, 50.0, 600, 'km/h', 'CRUISE', 'HERMETIC_TEST')
        """,
        (signal_path, bucket_m)
    )
    conn.commit()
    conn.close()

    # Capture original function before patching to avoid recursion
    orig_readonly_conn = db_mod.get_readonly_connection

    def hermetic_readonly_conn(db_path=None):
        return orig_readonly_conn(db_path=db_file)

    monkeypatch.setattr(db_mod, "get_readonly_connection", hermetic_readonly_conn)
    monkeypatch.setattr(eb_mod, "get_readonly_connection", hermetic_readonly_conn)
    monkeypatch.setattr(qe_mod, "get_readonly_connection", hermetic_readonly_conn)

    builder = EvidenceBuilder()
    evidence = builder.build_event_evidence(event_id=42)

    diagnostic = {
        "summary": f"Fault P1234 at {trigger_val}",
        "key_facts": [f"Trigger value {trigger_val}"],
        "evidence_status": "VERIFIED"
    }

    driver_view = format_persona_view(evidence, diagnostic, role="driver")
    assert driver_view["role"] == "driver"
    assert driver_view["measured_value"] == {"value": 115.5, "unit": "km/h"}

    tech_view = format_persona_view(evidence, diagnostic, role="technician")
    assert tech_view["role"] == "technician"
    assert tech_view["dtc_code"] == "P1234"
    assert tech_view["observed_trigger"] == {"value": 115.5, "unit": "km/h"}
    assert tech_view["waveform_bounds"]["min"]["value"] == 90.0
    assert tech_view["waveform_bounds"]["max"]["value"] == 110.0
    assert tech_view["baseline_stats"]["avg"]["value"] == 85.0


def test_persona_formatter_latency_benchmark(base_evidence, base_diagnostic):
    iterations = 2000
    t0 = time.perf_counter_ns()
    for _ in range(iterations):
        format_persona_view(base_evidence, base_diagnostic, role="driver")
        format_persona_view(base_evidence, base_diagnostic, role="technician")
        format_persona_view(base_evidence, base_diagnostic, role="fleet_operator")
    t1 = time.perf_counter_ns()
    avg_ns = (t1 - t0) / (iterations * 3)
    avg_us = avg_ns / 1000.0
    print(f"\n[BENCHMARK] Formatter latency: {avg_us:.2f} µs per format call")
    assert avg_us < 200.0, f"Formatter exceeded 200µs SLA: {avg_us:.2f}µs"
