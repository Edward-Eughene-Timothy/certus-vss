"""
test_phase4_2a_evidence.py: Focused Phase 4.2A tests for fail-closed evidence
integration, removal of fabricated fallback dictionaries, independent availability
statuses, and three-way measurement provenance labeling in EdgeVehicleAssistant.
"""
import sqlite3
from pathlib import Path
import pytest

from certus_vss.presentation.assistant_engine import EdgeVehicleAssistant
from certus_vss.storage.db_engine import init_database


def _create_hermetic_db(tmp_path: Path, seed_event: bool = False, seed_long_term: bool = False) -> Path:
    db_file = tmp_path / "hermetic_test.db"
    init_database(db_file)
    if not seed_event:
        return db_file

    event_ts = 1700000000
    event_min = event_ts // 60
    with sqlite3.connect(db_file) as conn:
        conn.execute(
            """
            INSERT INTO vehicle_events (
                event_id, timestamp_utc_s, event_category, dtc_code,
                severity, trigger_signal, trigger_value, context_json, provenance
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                10,
                event_ts,
                "THERMAL",
                "P0A7F",
                "WARNING",
                "Vehicle.Powertrain.TractionBattery.Temperature",
                46.8,
                "{}",
                "EDGE_BUFFER_INGESTION_V1",
            ),
        )
        # Seed 10Hz freeze frame points
        for offset_ms, val in [(-100, 36.0), (0, 46.8), (100, 45.2)]:
            conn.execute(
                """
                INSERT INTO fault_freeze_frames (event_id, vss_path, offset_ms, raw_value)
                VALUES (?, ?, ?, ?)
                """,
                (10, "Vehicle.Powertrain.TractionBattery.Temperature", offset_ms, val),
            )
        # Seed 24h pre-fault baseline bucket (within [event_ts - 86400, event_ts])
        conn.execute(
            """
            INSERT INTO telemetry_rollup_1m (
                vss_path, bucket_epoch_m, val_avg, val_min, val_max, val_delta,
                sample_count, unit, drive_context, provenance
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "Vehicle.Powertrain.TractionBattery.Temperature",
                event_min - 10,
                31.5,
                29.0,
                34.0,
                5.0,
                60,
                "celsius",
                "HIGHWAY_CRUISE",
                "EDGE_AGGREGATOR_V1",
            ),
        )
        if seed_long_term:
            # Seed older long-term historical bucket 5 days prior
            conn.execute(
                """
                INSERT INTO telemetry_rollup_1m (
                    vss_path, bucket_epoch_m, val_avg, val_min, val_max, val_delta,
                    sample_count, unit, drive_context, provenance
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "Vehicle.Powertrain.TractionBattery.Temperature",
                    event_min - 7200,
                    25.5,
                    24.0,
                    27.0,
                    3.0,
                    60,
                    "celsius",
                    "URBAN",
                    "EDGE_AGGREGATOR_V1",
                ),
            )
        conn.commit()
    return db_file


def test_assistant_fails_closed_when_no_events_exist(tmp_path: Path):
    """Verifies that fabricated 47.5 / P0A7F fallback is completely removed."""
    empty_db = _create_hermetic_db(tmp_path, seed_event=False)
    assistant = EdgeVehicleAssistant(db_path=empty_db, enable_slm=False)

    diag_reply = assistant.answer("What is wrong with my car?")
    assert "Diagnostic Evidence Unavailable" in diag_reply
    assert "47.5" not in diag_reply
    assert "P0A7F" not in diag_reply

    explain_reply = assistant.answer("What fault code is active?")
    assert "Diagnostic Evidence Unavailable" in explain_reply
    assert "47.5" not in explain_reply


def test_assistant_three_way_provenance_and_provisional_dtc(tmp_path: Path):
    """Verifies distinct labeling of event trigger, 24h pre-fault baseline, and long-term average."""
    db_file = _create_hermetic_db(tmp_path, seed_event=True, seed_long_term=True)
    assistant = EdgeVehicleAssistant(db_path=db_file, enable_slm=False)
    assistant.set_role("technician")

    diag_reply = assistant.answer("What is wrong with the vehicle?")
    assert "Event trigger measurement: 46.8 celsius" in diag_reply
    assert "24-hour pre-fault baseline average: 31.5 celsius" in diag_reply
    assert "Long-term recorded database average: 28.5 celsius" in diag_reply
    assert "Provisional Procedure TSB-2026-BATT-04" in diag_reply
    assert "Requires OEM manual verification" in diag_reply

    explain_reply = assistant.answer("Explain the fault code P0A7F")
    assert "PROVISIONAL_REFERENCE" in explain_reply
    assert "Unconfirmed against OEM documentation" in explain_reply
    assert "Requires OEM manual verification" in explain_reply


def test_assistant_preserves_event_evidence_when_historical_rollup_unavailable(tmp_path: Path, monkeypatch):
    """Verifies independent availability when long-term rollup returns NO_DATA."""
    db_file = _create_hermetic_db(tmp_path, seed_event=True, seed_long_term=False)
    assistant = EdgeVehicleAssistant(db_path=db_file, enable_slm=False)

    # Simulate long-term rollup unavailability while EvidenceBuilder still succeeds
    monkeypatch.setattr(assistant, "_get_long_term_rollup", lambda vss_path: {"status": "NO_DATA"})

    reply = assistant.answer("What is wrong with the car?")
    assert "Event trigger measurement: 46.8 celsius" in reply
    assert "24-hour pre-fault baseline average: 31.5 celsius" in reply
    assert "Long-term recorded database average: UNAVAILABLE" in reply
