"""
test_phase4_2b_state.py: Comprehensive validation tests for ConversationState,
deterministic event anchoring, follow-up reference resolution, and persona consistency.
"""
import sqlite3
from pathlib import Path
import pytest

from certus_vss.presentation.assistant_engine import EdgeVehicleAssistant
from certus_vss.storage.db_engine import init_database


def _seed_event(db_file: Path, event_id: int, timestamp: int, dtc: str, trigger_val: float, trigger_sig: str = "Vehicle.Powertrain.TractionBattery.Temperature"):
    event_min = timestamp // 60
    with sqlite3.connect(db_file) as conn:
        conn.execute(
            """
            INSERT INTO vehicle_events (
                event_id, timestamp_utc_s, event_category, dtc_code,
                severity, trigger_signal, trigger_value, context_json, provenance
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                timestamp,
                "THERMAL",
                dtc,
                "WARNING",
                trigger_sig,
                trigger_val,
                "{}",
                "EDGE_BUFFER_INGESTION_V1",
            ),
        )
        for offset_ms, val in [(-100, trigger_val - 5.0), (0, trigger_val), (100, trigger_val - 1.0)]:
            conn.execute(
                """
                INSERT INTO fault_freeze_frames (event_id, vss_path, offset_ms, raw_value)
                VALUES (?, ?, ?, ?)
                """,
                (event_id, trigger_sig, offset_ms, val),
            )
        conn.execute(
            """
            INSERT OR REPLACE INTO telemetry_rollup_1m (
                vss_path, bucket_epoch_m, val_avg, val_min, val_max, val_delta,
                sample_count, unit, drive_context, provenance
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trigger_sig,
                event_min - 10,
                30.0,
                25.0,
                35.0,
                10.0,
                60,
                "celsius",
                "HIGHWAY_CRUISE",
                "EDGE_AGGREGATOR_V1",
            ),
        )
        conn.commit()


def test_initial_event_pinning(tmp_path: Path):
    """Verifies that an initial diagnostic request pins the active event."""
    db_file = tmp_path / "test_state.db"
    init_database(db_file)
    _seed_event(db_file, event_id=10, timestamp=1700000000, dtc="P0A7F", trigger_val=46.5)

    assistant = EdgeVehicleAssistant(db_path=db_file, enable_slm=False)
    assert assistant.state.active_event_id is None

    reply = assistant.answer("What is wrong with my car?")
    assert "Event #10" in reply
    assert assistant.state.active_event_id == 10
    assert assistant.state.active_vss_path == "Vehicle.Powertrain.TractionBattery.Temperature"
    assert assistant.state.last_dtc_code == "P0A7F"


def test_new_event_arrival_does_not_replace_pinned_event_during_followup(tmp_path: Path):
    """
    CRITICAL: Verifies that if a new event arrives in SQLite,
    subsequent follow-up questions remain strictly anchored to the pinned event.
    """
    db_file = tmp_path / "test_anchor.db"
    init_database(db_file)
    _seed_event(db_file, event_id=10, timestamp=1700000000, dtc="P0A7F", trigger_val=46.5)

    assistant = EdgeVehicleAssistant(db_path=db_file, enable_slm=False)
    assistant.answer("What is wrong with my car?")
    assert assistant.state.active_event_id == 10

    # Background ingestion adds Event #20 with a different measurement (52.0°C)
    _seed_event(db_file, event_id=20, timestamp=1700005000, dtc="P0A7F", trigger_val=52.0)

    # User asks follow-up comparison: must remain anchored to Event #10 (46.5°C), NOT Event #20 (52.0°C)
    comp_reply = assistant.answer("Why is it higher than normal?")
    assert "Pinned Event #10" in comp_reply
    assert "46.5 celsius" in comp_reply
    assert "52.0" not in comp_reply
    assert assistant.state.active_event_id == 10

    # User asks follow-up procedure: must still reference Event #10
    proc_reply = assistant.answer("What should I inspect for it?")
    assert "Event #10" in proc_reply
    assert assistant.state.active_event_id == 10


def test_explicit_new_diagnostic_request_updates_pinned_event(tmp_path: Path):
    """Verifies that an explicit new diagnostic inquiry updates the pinned event to the newest event."""
    db_file = tmp_path / "test_new_diag.db"
    init_database(db_file)
    _seed_event(db_file, event_id=10, timestamp=1700000000, dtc="P0A7F", trigger_val=46.5)

    assistant = EdgeVehicleAssistant(db_path=db_file, enable_slm=False)
    assistant.answer("What is wrong with my car?")
    assert assistant.state.active_event_id == 10

    # Background ingestion adds Event #20
    _seed_event(db_file, event_id=20, timestamp=1700005000, dtc="P0A7F", trigger_val=52.0)

    # User explicitly asks for new diagnostic status
    new_reply = assistant.answer("Check the vehicle diagnostics again.")
    assert "Event #20" in new_reply
    assert "52.0" in new_reply
    assert assistant.state.active_event_id == 20


def test_pinned_event_becoming_unavailable_fails_closed(tmp_path: Path):
    """Verifies that if a pinned event is deleted, follow-ups fail closed rather than substituting another event."""
    db_file = tmp_path / "test_unavail.db"
    init_database(db_file)
    _seed_event(db_file, event_id=10, timestamp=1700000000, dtc="P0A7F", trigger_val=46.5)
    _seed_event(db_file, event_id=20, timestamp=1700005000, dtc="P0A7F", trigger_val=52.0)

    assistant = EdgeVehicleAssistant(db_path=db_file, enable_slm=False)
    # Manually pin event 10
    assistant.state.pin_event(10, "Vehicle.Powertrain.TractionBattery.Temperature", "P0A7F")

    # Delete Event 10 from database
    with sqlite3.connect(db_file) as conn:
        conn.execute("DELETE FROM vehicle_events WHERE event_id = 10")
        conn.commit()

    reply = assistant.answer("Why is it higher than normal?")
    assert "Diagnostic Evidence Unavailable" in reply
    assert "Referenced event #10" in reply
    # Must NOT silently substitute Event #20
    assert "Event #20" not in reply


def test_persona_switching_preserves_pinned_event(tmp_path: Path):
    """Verifies that switching roles alters presentation depth without changing active evidence."""
    db_file = tmp_path / "test_persona.db"
    init_database(db_file)
    _seed_event(db_file, event_id=10, timestamp=1700000000, dtc="P0A7F", trigger_val=46.5)

    assistant = EdgeVehicleAssistant(db_path=db_file, enable_slm=False)
    assistant.answer("What is wrong with my car?")
    assert assistant.state.active_event_id == 10

    # Switch to Technician
    assistant.set_role("technician")
    assert assistant.state.active_event_id == 10
    tech_reply = assistant.answer("What should I inspect for it?")
    assert "Event #10" in tech_reply
    assert "Provisional Procedure TSB-2026-BATT-04" in tech_reply

    # Switch to Fleet Operator
    assistant.set_role("fleet_operator")
    assert assistant.state.active_event_id == 10
    fleet_reply = assistant.answer("Can this vehicle be dispatched for a trip?")
    assert "FLAGGED_FOR_INSPECTION" in fleet_reply
    assert "#10" in fleet_reply


def test_vehicle_switching_invalidates_pinned_event():
    """Verifies that switching active vehicle clears pinned events from the previous vehicle."""
    assistant = EdgeVehicleAssistant(enable_slm=False, vehicle_id="EV_FLEET_01")
    assistant.state.pin_event(10, "Vehicle.Speed", "U0100")
    assert assistant.state.active_event_id == 10

    assistant.switch_vehicle("EV_FLEET_02")
    assert assistant.state.active_vehicle_id == "EV_FLEET_02"
    assert assistant.state.active_event_id is None
    assert assistant.state.active_vss_path is None
    assert assistant.state.last_dtc_code is None


def test_empty_conversation_state_handles_followups_gracefully(tmp_path: Path):
    """Verifies that follow-up questions on a blank session return clear guidance without crashing."""
    db_file = tmp_path / "test_empty.db"
    init_database(db_file)
    assistant = EdgeVehicleAssistant(db_path=db_file, enable_slm=False)

    reply_comp = assistant.answer("Why is it higher than normal?")
    assert "No active diagnostic event is currently pinned" in reply_comp

    reply_proc = assistant.answer("What should I inspect for it?")
    assert "No active diagnostic event is currently pinned" in reply_proc


def test_independent_conversation_sessions_are_isolated(tmp_path: Path):
    """Verifies that two assistant instances have completely isolated conversation states."""
    db_file = tmp_path / "test_iso.db"
    init_database(db_file)
    _seed_event(db_file, event_id=10, timestamp=1700000000, dtc="P0A7F", trigger_val=46.5)

    assistant_a = EdgeVehicleAssistant(db_path=db_file, enable_slm=False, vehicle_id="EV_A")
    assistant_b = EdgeVehicleAssistant(db_path=db_file, enable_slm=False, vehicle_id="EV_B")

    assistant_a.answer("What is wrong with my car?")
    assert assistant_a.state.active_event_id == 10
    assert assistant_b.state.active_event_id is None
