"""
test_phase4_2c_grounding.py: Final Phase 4.2C exhaustive test suite for Output Grounding
& Domain Validation. Covers all 8 frozen requirements.
"""
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from certus_vss.analytics.context_builder import ConversationContextBuilder
from certus_vss.analytics.evidence_builder import EvidenceBuilder
from certus_vss.analytics.grounding_validator import validate_output_grounding, extract_factual_measurements
from certus_vss.catalog.signals import CANONICAL_VSS_SIGNALS, CANONICAL_VSS_UNITS
from certus_vss.presentation.assistant_engine import EdgeVehicleAssistant
from certus_vss.storage.db_engine import init_database


def get_grounded_context(rollup_available: bool = True, dtc_code: str = "P0A7F"):
    ctx = {
        "role": "driver",
        "availability_status": {
            "event_evidence_status": "AVAILABLE",
            "pre_fault_baseline_status": "AVAILABLE",
            "historical_rollup_status": "AVAILABLE" if rollup_available else "UNAVAILABLE",
        },
        "active_fault_snapshot": {
            "event_id": 10,
            "event_timestamp_utc_s": 1700000000,
            "dtc_code": dtc_code,
            "dtc_reference_status": "PROVISIONAL_REFERENCE" if dtc_code else "UNKNOWN_CODE",
            "trigger_signal": "Vehicle.Powertrain.TractionBattery.Temperature",
            "trigger_measurement": {"value": 47.5, "unit": "celsius", "provenance": "recorded at event timestamp 1700000000"},
            "pre_fault_24h_baseline": {"average": 32.43, "min": 25.0, "max": 35.0, "unit": "celsius"},
            "service_procedure": "Inspect coolant circuit manifold. Requires OEM manual verification.",
        },
        "soc_val": 66.7,
        "provenance_reconciliation": {
            "distinction": "The 24-hour pre-fault baseline average was 32.43 celsius. The long-term recorded database average was 28.67 celsius. The event record contains a trigger measurement of 47.5 celsius.",
        },
    }
    if rollup_available:
        ctx["long_term_historical_rollup"] = {
            "status": "SUCCESS",
            "vss_path": "Vehicle.Powertrain.TractionBattery.Temperature",
            "average": 28.67,
            "min": 24.0,
            "max": 35.0,
            "unit": "celsius",
            "start_epoch_s": 1699567980,
            "end_epoch_s": 1699999440,
            "window_minutes": 7191,
        }
    else:
        ctx["long_term_historical_rollup"] = {
            "status": "UNAVAILABLE",
            "reason": "Historical rollup query returned status: 'NO_DATA'.",
        }
    return ctx


# 1. Evidence integrity validation (booleans, strings, NaN, infinity, ordering, timestamps)
def test_context_integrity_rejects_malformed_evidence():
    ctx_bool = get_grounded_context()
    ctx_bool["active_fault_snapshot"]["trigger_measurement"]["value"] = True
    assert not validate_output_grounding("Trigger was 47.5 celsius.", ctx_bool)[0]

    ctx_str = get_grounded_context()
    ctx_str["active_fault_snapshot"]["trigger_measurement"]["value"] = "47.5"
    assert not validate_output_grounding("Trigger was 47.5 celsius.", ctx_str)[0]

    ctx_nan = get_grounded_context()
    ctx_nan["active_fault_snapshot"]["pre_fault_24h_baseline"]["average"] = float("nan")
    assert not validate_output_grounding("Baseline average was 32.43 celsius.", ctx_nan)[0]

    ctx_inv = get_grounded_context()
    ctx_inv["active_fault_snapshot"]["pre_fault_24h_baseline"]["min"] = 40.0
    ctx_inv["active_fault_snapshot"]["pre_fault_24h_baseline"]["average"] = 32.43
    ctx_inv["active_fault_snapshot"]["pre_fault_24h_baseline"]["max"] = 30.0
    assert not validate_output_grounding("Baseline average was 32.43 celsius.", ctx_inv)[0]

    ctx_sig = get_grounded_context()
    ctx_sig["long_term_historical_rollup"]["vss_path"] = "Vehicle.Invalid.Signal"
    assert not validate_output_grounding("Average was 28.67 celsius.", ctx_sig)[0]

    ctx_win = get_grounded_context()
    ctx_win["long_term_historical_rollup"]["start_epoch_s"] = 1700000000
    ctx_win["long_term_historical_rollup"]["end_epoch_s"] = 1700000600
    ctx_win["long_term_historical_rollup"]["window_minutes"] = 500
    assert not validate_output_grounding("Average was 28.67 celsius.", ctx_win)[0]


# 2. All six canonical signal mappings and behavioral validator tests
def test_all_six_canonical_signal_units():
    expected_mappings = {
        "Vehicle.Speed": ("km/h", 85.5, "Vehicle speed was 85.5 km/h."),
        "Vehicle.Powertrain.TractionBattery.Temperature": ("celsius", 42.0, "Battery temperature was 42.0 celsius."),
        "Vehicle.Powertrain.TractionBattery.Current": ("amperes", 125.0, "Traction battery current was 125.0 amperes."),
        "Vehicle.Powertrain.TractionBattery.StateOfCharge": ("percent", 78.5, "State of charge was 78.5%."),
        "Vehicle.Cabin.HVAC.PowerDraw": ("watts", 1450.0, "HVAC power draw was 1450.0 watts."),
        "Vehicle.Powertrain.ElectricMotor.CoolantTemperature": ("celsius", 65.0, "Motor coolant temperature was 65.0 celsius."),
    }
    for sig, (unit, val, valid_sentence) in expected_mappings.items():
        assert CANONICAL_VSS_UNITS[sig] == unit
        assert sig in CANONICAL_VSS_SIGNALS
        sig_ctx = {
            "active_fault_snapshot": {
                "trigger_signal": sig,
                "trigger_measurement": {"value": val, "unit": unit},
            },
            "availability_status": {"historical_rollup_status": "UNAVAILABLE"},
        }
        assert validate_output_grounding(valid_sentence, sig_ctx)[0] is True


# 3. Correct temperature and canonical unit accepted
def test_validator_accepts_valid_grounded_text():
    ctx = get_grounded_context()
    valid_text = (
        "The battery temperature reached 47.5 celsius at trigger, exceeding the 24-hour pre-fault "
        "baseline average of 32.43 celsius and long-term average of 28.67 celsius."
    )
    is_valid, reason = validate_output_grounding(valid_text, ctx)
    assert is_valid is True
    assert reason is None


# 4. Structural numbers strict equality (1.9 rejected, exact integers permitted)
def test_validator_structural_numbers_strict_equality():
    ctx = get_grounded_context()
    text_with_structural = (
        "There are 3 measurements to compare over the last 24 hours: trigger was 47.5 celsius, "
        "and baseline average was 32.43 celsius."
    )
    is_valid, _ = validate_output_grounding(text_with_structural, ctx)
    assert is_valid is True

    text_with_float = "The trigger value was 47.5 celsius with a 1.9 offset factor."
    is_valid_float, reason_float = validate_output_grounding(text_with_float, ctx)
    assert is_valid_float is False
    assert "Unsupported numeric value" in reason_float


# 5. Incompatible physical units rejected (Kelvin, Fahrenheit, MPH)
def test_validator_rejects_incompatible_unit():
    ctx = get_grounded_context()
    assert not validate_output_grounding("The battery temperature was recorded at 47.5 fahrenheit.", ctx)[0]
    assert not validate_output_grounding("The battery temperature was recorded at 47.5 kelvin.", ctx)[0]
    assert not validate_output_grounding("Vehicle speed was 80 mph.", ctx)[0]


# 6. Unknown unit tokens rejected (volts, quarts)
def test_validator_rejects_unknown_unit_tokens():
    ctx = get_grounded_context()
    assert not validate_output_grounding("The measured voltage was 10 volts.", ctx)[0]
    assert not validate_output_grounding("The fluid volume was 50 quarts.", ctx)[0]


# 7. Natural English prose accepted without unit rejection
def test_validator_accepts_ordinary_numeric_prose():
    ctx = get_grounded_context()
    ctx["long_term_historical_rollup"]["window_minutes"] = 43210
    ctx["long_term_historical_rollup"]["start_epoch_s"] = 1690000000
    ctx["long_term_historical_rollup"]["end_epoch_s"] = 1700000000
    assert validate_output_grounding("Analyzed over 43210 recorded minutes of driving data.", ctx)[0] is True
    assert validate_output_grounding("Battery temperature was 47.5 degrees Celsius at trigger.", ctx)[0] is True
    assert validate_output_grounding("Compared to the 24-hour baseline average of 32.43 celsius.", ctx)[0] is True
    assert validate_output_grounding("Found 3 diagnostic events in the log.", ctx)[0] is True
    assert validate_output_grounding("Total of 5 measurements recorded.", ctx)[0] is True


# 8. Unit mismatch for measurement (e.g. temperature expressed as km/h)
def test_wrong_unit_with_valid_number_rejected():
    ctx = get_grounded_context()
    text_kmh = "The battery temperature was 47.5 km/h."
    is_valid, reason = validate_output_grounding(text_kmh, ctx)
    assert is_valid is False
    assert "Unit mismatch" in reason


# 9. Signal-specific cross-signal mismatch rejection (Battery Temp vs Motor Coolant Temp, SOC vs Temp)
def test_signal_specific_cross_signal_mismatch():
    ctx = get_grounded_context()
    mismatched_coolant = "The motor coolant temperature was 47.5 celsius."
    is_valid_coolant, reason_coolant = validate_output_grounding(mismatched_coolant, ctx)
    assert is_valid_coolant is False
    assert "Signal mismatch" in reason_coolant

    mismatched_soc = "The recorded battery temperature was 66.7%."
    is_valid_soc, _ = validate_output_grounding(mismatched_soc, ctx)
    assert is_valid_soc is False


# 10. Explicit vss_path in historical rollup independent of active fault snapshot
def test_historical_rollup_explicit_signal_association():
    ctx = {
        "active_fault_snapshot": {
            "trigger_signal": "Vehicle.Powertrain.TractionBattery.Temperature",
            "trigger_measurement": {"value": 46.8, "unit": "celsius"},
            "pre_fault_24h_baseline": {"average": 31.5, "unit": "celsius"},
        },
        "long_term_historical_rollup": {
            "status": "SUCCESS",
            "vss_path": "Vehicle.Speed",
            "average": 82.0,
            "min": 70.0,
            "max": 95.0,
            "unit": "km/h",
        },
        "availability_status": {"historical_rollup_status": "AVAILABLE"},
    }
    valid_speed = "The long-term database average speed was 82.0 km/h."
    assert validate_output_grounding(valid_speed, ctx)[0] is True

    bad_speed_unit = "The long-term database average speed was 82.0 celsius."
    assert not validate_output_grounding(bad_speed_unit, ctx)[0]

    ctx_missing = dict(ctx)
    ctx_missing["long_term_historical_rollup"] = {
        "status": "SUCCESS",
        "average": 82.0,
        "unit": "km/h",
    }
    assert not validate_output_grounding(valid_speed, ctx_missing)[0]


# 11. Derived measurements validation (deltas and spreads)
def test_derived_measurements_validation():
    ctx = get_grounded_context()
    valid_derived = "Trigger was 15.07 celsius above the 24h baseline, with a baseline spread of 10.0 celsius."
    assert validate_output_grounding(valid_derived, ctx)[0] is True

    invalid_derived = "Trigger was 19.5 celsius above the 24h baseline."
    assert not validate_output_grounding(invalid_derived, ctx)[0]


# 12. All six wrong-provenance attribution directions rejected
def test_all_six_wrong_provenance_attribution_directions():
    ctx = get_grounded_context()
    assert not validate_output_grounding("The 24-hour baseline average was 47.5 celsius.", ctx)[0]
    assert not validate_output_grounding("The long-term database average was 47.5 celsius.", ctx)[0]
    assert not validate_output_grounding("The event trigger reading of 32.43 celsius was observed.", ctx)[0]
    assert not validate_output_grounding("The long-term database average was 32.43 celsius.", ctx)[0]
    assert not validate_output_grounding("The trigger spike was 28.67 celsius.", ctx)[0]
    assert not validate_output_grounding("The 24-hour baseline average was 28.67 celsius.", ctx)[0]


# 13. Known provisional DTC with fabricated plausible EV description or procedure rejected
def test_known_provisional_dtc_with_fabricated_description_rejected():
    ctx = get_grounded_context(dtc_code="P0A7F")
    fabricated_ev_desc = "DTC P0A7F indicates an inverter IGBT short and stator winding open."
    is_valid, reason = validate_output_grounding(fabricated_ev_desc, ctx)
    assert is_valid is False
    assert "fabricated symptom claim" in reason

    bad_proc = "• Procedure Reference: Replace DC-DC converter assembly immediately."
    assert not validate_output_grounding(f"DTC P0A7F:\n{bad_proc}", ctx)[0]


# 14. Unknown DTC with invented diagnostic claims rejected
def test_unknown_dtc_with_invented_description_rejected():
    ctx = get_grounded_context(dtc_code="P9999")
    allowed_text = "Recorded event has code P9999 at 47.5 celsius."
    assert validate_output_grounding(allowed_text, ctx)[0] is True

    invented_text = "DTC P9999 failure detected in circuit with replacement required."
    is_valid, reason = validate_output_grounding(invented_text, ctx)
    assert is_valid is False
    assert "invented diagnostic claim detected" in reason


# 15. False OEM authority claims rejected
def test_validator_rejects_false_oem_authority_claims():
    ctx = get_grounded_context()
    text = "This repair is an OEM-verified manufacturer confirmed procedure."
    is_valid, reason = validate_output_grounding(text, ctx)
    assert is_valid is False
    assert "False technical authority claim" in reason


# 16. Valid and invalid range calculations & SOC provenance
def test_valid_and_invalid_range_calculations():
    ctx = {
        "soc_val": 66.7,
        "soc_unit": "percent",
        "soc_signal": "Vehicle.Powertrain.TractionBattery.StateOfCharge",
        "nominal_range_km": 400.0,
        "estimated_range_km": 266.8,
        "active_fault_snapshot": {},
    }
    valid_range = "Estimated Remaining Range: ~266.8 km (based on recorded State of Charge of 66.7%)."
    assert validate_output_grounding(valid_range, ctx)[0] is True

    bad_math = "Estimated Remaining Range: ~350 km (based on recorded State of Charge of 66.7%)."
    is_valid_math, reason_math = validate_output_grounding(bad_math, ctx)
    assert is_valid_math is False
    assert "Range calculation error" in reason_math

    guaranteed_range = "Guaranteed remaining range of 266.8 km available."
    is_valid_g, reason_g = validate_output_grounding(guaranteed_range, ctx)
    assert is_valid_g is False
    assert "falsely represented as guaranteed" in reason_g

    ctx_no_soc = {"nominal_range_km": 400.0, "estimated_range_km": 266.8, "active_fault_snapshot": {}}
    assert not validate_output_grounding(valid_range, ctx_no_soc)[0]


# 17. SLM validation failure triggers deterministic fallback and last-resort safety
def test_assistant_deterministic_fallback_on_validation_failure():
    assistant = EdgeVehicleAssistant(enable_slm=True)
    mock_llm = MagicMock()
    mock_llm.return_value = {"choices": [{"text": "The battery reached 99.0 celsius and engine oil should be replaced."}]}
    assistant.llm = mock_llm

    ctx = get_grounded_context()
    fallback = "Verified fallback summary: 47.5 celsius trigger."
    out = assistant.generate_validated_slm_text("System", "User", ctx, fallback)
    assert out == fallback
    assert "99.0" not in out
    assert "engine oil" not in out

    bad_stream = list(assistant._finalize_and_stream("Bad 99.0 celsius", ctx, "Also bad 88.0 celsius"))
    assert len(bad_stream) == 1
    assert "Diagnostic Evidence Unavailable" in bad_stream[0]


# 18. Every deterministic response branch passes universal validation boundary
def test_every_deterministic_response_branch_passes_universal_boundary(tmp_path: Path):
    db_file = tmp_path / "branches_test.db"
    init_database(db_file)
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
                10, event_ts, "THERMAL", "P0A7F", "WARNING",
                "Vehicle.Powertrain.TractionBattery.Temperature", 46.8, "{}", "EDGE_BUFFER_INGESTION_V1"
            ),
        )
        for offset_ms, val in [(-100, 36.0), (0, 46.8), (100, 45.2)]:
            conn.execute(
                "INSERT INTO fault_freeze_frames (event_id, vss_path, offset_ms, raw_value) VALUES (?, ?, ?, ?)",
                (10, "Vehicle.Powertrain.TractionBattery.Temperature", offset_ms, val),
            )
        conn.execute(
            """
            INSERT INTO telemetry_rollup_1m (
                vss_path, bucket_epoch_m, val_avg, val_min, val_max, val_delta,
                sample_count, unit, drive_context, provenance
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("Vehicle.Powertrain.TractionBattery.Temperature", event_min - 10, 31.5, 29.0, 34.0, 5.0, 60, "celsius", "HIGHWAY_CRUISE", "EDGE_AGGREGATOR_V1"),
        )
        conn.execute(
            """
            INSERT INTO telemetry_rollup_1m (
                vss_path, bucket_epoch_m, val_avg, val_min, val_max, val_delta,
                sample_count, unit, drive_context, provenance
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("Vehicle.Powertrain.TractionBattery.StateOfCharge", event_min - 10, 75.0, 74.0, 76.0, 2.0, 60, "percent", "HIGHWAY_CRUISE", "EDGE_AGGREGATOR_V1"),
        )
        conn.commit()

    assistant = EdgeVehicleAssistant(db_path=db_file, enable_slm=False, nominal_range_km=400.0)

    queries = [
        "Hello",                              # GREETING
        "What is my oil pressure?",           # OUT_OF_SCOPE
        "What is wrong with my car?",         # NEW_DIAGNOSTIC
        "Why is it higher than normal?",      # FOLLOWUP_COMPARISON
        "What should I inspect for it?",      # FOLLOWUP_PROCEDURE
        "What was the average?",              # FOLLOWUP_BASELINE
        "Explain that fault",                 # FOLLOWUP_EXPLANATION
        "Can I dispatch this car?",           # DISPATCH_CHECK
        "What is the battery temperature?",   # TELEMETRY
        "How far can I drive?",               # RANGE_ESTIMATE
    ]
    for q in queries:
        resp = assistant.answer(q)
        assert resp is not None and len(resp) > 0
        assert "Response could not be validated" not in resp


# 19. Genuine SQLite EvidenceBuilder -> ContextBuilder -> GroundingValidator integration flow
def test_genuine_sqlite_evidence_and_context_builder_flow(tmp_path: Path):
    db_file = tmp_path / "genuine_sqlite_flow.db"
    init_database(db_file)

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
        for offset_ms, val in [(-100, 36.0), (0, 46.8), (100, 45.2)]:
            conn.execute(
                """
                INSERT INTO fault_freeze_frames (event_id, vss_path, offset_ms, raw_value)
                VALUES (?, ?, ?, ?)
                """,
                (10, "Vehicle.Powertrain.TractionBattery.Temperature", offset_ms, val),
            )
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
        conn.execute(
            """
            INSERT INTO telemetry_rollup_1m (
                vss_path, bucket_epoch_m, val_avg, val_min, val_max, val_delta,
                sample_count, unit, drive_context, provenance
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "Vehicle.Speed",
                event_min - 100,
                82.0,
                75.0,
                90.0,
                15.0,
                60,
                "km/h",
                "HIGHWAY_CRUISE",
                "EDGE_AGGREGATOR_V1",
            ),
        )
        conn.commit()

    assistant = EdgeVehicleAssistant(db_path=db_file, enable_slm=False)
    actual_evidence = assistant.evidence_builder.build_event_evidence(event_id=10)
    assert "baseline" in actual_evidence
    assert actual_evidence["baseline"]["aggregates"]["average"] == 31.5

    bounds_temp = assistant.get_signal_time_bounds("Vehicle.Powertrain.TractionBattery.Temperature")
    bounds_speed = assistant.get_signal_time_bounds("Vehicle.Speed")
    assert bounds_temp is not None
    assert bounds_speed is not None
    assert bounds_temp != bounds_speed

    actual_rollup = assistant._get_long_term_rollup("Vehicle.Powertrain.TractionBattery.Temperature")
    assert actual_rollup["status"] == "SUCCESS"
    assert actual_rollup["aggregates"]["average"] == 28.5
    assert actual_rollup["window_minutes"] == 2
    assert actual_rollup["vss_path"] == "Vehicle.Powertrain.TractionBattery.Temperature"

    real_ctx = ConversationContextBuilder.build_grounded_context(
        evidence=actual_evidence,
        historical_rollup=actual_rollup,
        role="technician",
    )
    real_ctx["long_term_historical_rollup"]["vss_path"] = "Vehicle.Powertrain.TractionBattery.Temperature"

    valid_text = (
        "Event trigger measurement was 46.8 celsius. The 24-hour pre-fault baseline average was 31.5 celsius, "
        "and the long-term recorded database average was 28.5 celsius."
    )
    is_valid, reason = validate_output_grounding(valid_text, real_ctx)
    assert is_valid is True, f"Validation failed on real SQLite evidence: {reason}"

    invalid_text = "The 24-hour baseline average was 46.8 celsius."
    is_valid_inv, reason_inv = validate_output_grounding(invalid_text, real_ctx)
    assert is_valid_inv is False
    assert "Semantic provenance error" in reason_inv
