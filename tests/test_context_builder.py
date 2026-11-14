"""
test_context_builder.py: Comprehensive validation tests for ConversationContextBuilder.
Verifies fail-closed behavior, type checks, unit checks, window metadata,
independent availability statuses, and aggregate ordering.
"""
import pytest
from certus_vss.analytics.context_builder import ConversationContextBuilder
from certus_vss.analytics.evidence_builder import EvidenceMissingError
from certus_vss.catalog.signals import CANONICAL_VSS_SIGNALS


def get_valid_evidence():
    return {
        "event": {
            "event_id": 4,
            "timestamp_utc_s": 1700000000,
            "dtc_code": "P0A7F",
            "trigger_signal": "Vehicle.Powertrain.TractionBattery.Temperature",
            "trigger_value": 47.5,
        },
        "baseline": {"avg": 32.43, "min": 25.0, "max": 35.0},
    }


def get_valid_rollup():
    return {
        "status": "SUCCESS",
        "window_minutes": 43210,
        "start_epoch_s": 1697400000,
        "end_epoch_s": 1700000000,
        "aggregates": {"average": 28.67, "min": 25.75, "max": 35.0},
        "unit": "celsius",
    }


def test_context_builder_success_flow():
    evidence = get_valid_evidence()
    rollup = get_valid_rollup()
    ctx = ConversationContextBuilder.build_grounded_context(evidence, historical_rollup=rollup, role="driver")

    assert set(ctx["canonical_signals"]) == set(CANONICAL_VSS_SIGNALS)
    assert ctx["availability_status"] == {
        "event_evidence_status": "AVAILABLE",
        "pre_fault_baseline_status": "AVAILABLE",
        "historical_rollup_status": "AVAILABLE",
    }
    assert ctx["active_fault_snapshot"]["dtc_reference_status"] == "PROVISIONAL_REFERENCE"
    assert "Provisional Procedure TSB-2026-BATT-04" in ctx["active_fault_snapshot"]["service_procedure"]
    assert ctx["active_fault_snapshot"]["trigger_measurement"]["value"] == 47.5

    rollup_ctx = ctx["long_term_historical_rollup"]
    assert rollup_ctx["start_epoch_s"] == 1697400000
    assert rollup_ctx["end_epoch_s"] == 1700000000
    assert rollup_ctx["window_minutes"] == 43210

    reconcile = ctx["provenance_reconciliation"]
    assert "24-hour pre-fault baseline average was 32.43" in reconcile["distinction"]
    assert "long-term recorded database average was 28.67" in reconcile["distinction"]
    assert "trigger measurement of 47.5 celsius" in reconcile["distinction"]


def test_context_builder_rejects_boolean_values():
    evidence = get_valid_evidence()
    evidence["event"]["trigger_value"] = True
    with pytest.raises(EvidenceMissingError, match="Invalid non-numeric type"):
        ConversationContextBuilder.build_grounded_context(evidence)

    evidence_ts = get_valid_evidence()
    evidence_ts["event"]["timestamp_utc_s"] = False
    with pytest.raises(EvidenceMissingError, match="Invalid non-numeric type"):
        ConversationContextBuilder.build_grounded_context(evidence_ts)


def test_context_builder_rejects_numeric_strings():
    evidence = get_valid_evidence()
    evidence["event"]["trigger_value"] = "47.5"
    with pytest.raises(EvidenceMissingError, match="Invalid non-numeric type"):
        ConversationContextBuilder.build_grounded_context(evidence)

    evidence_base = get_valid_evidence()
    evidence_base["baseline"]["avg"] = "32.43"
    with pytest.raises(EvidenceMissingError, match="Invalid non-numeric type"):
        ConversationContextBuilder.build_grounded_context(evidence_base)


def test_context_builder_rejects_fractional_timestamps():
    evidence = get_valid_evidence()
    evidence["event"]["timestamp_utc_s"] = 1700000000.5
    with pytest.raises(EvidenceMissingError, match="Fractional event timestamp is not supported"):
        ConversationContextBuilder.build_grounded_context(evidence)


def test_context_builder_rejects_unsupported_signals():
    evidence = get_valid_evidence()
    evidence["event"]["trigger_signal"] = "Vehicle.Powertrain.TractionBattery.Voltage"
    with pytest.raises(EvidenceMissingError, match="not in CANONICAL_VSS_SIGNALS"):
        ConversationContextBuilder.build_grounded_context(evidence)


def test_context_builder_rejects_invalid_aggregate_ordering():
    ev_bad_min = get_valid_evidence()
    ev_bad_min["baseline"] = {"avg": 30.0, "min": 35.0, "max": 40.0}
    with pytest.raises(EvidenceMissingError, match="Invalid baseline aggregate ordering"):
        ConversationContextBuilder.build_grounded_context(ev_bad_min)

    ev_bad_max = get_valid_evidence()
    ev_bad_max["baseline"] = {"avg": 45.0, "min": 20.0, "max": 40.0}
    with pytest.raises(EvidenceMissingError, match="Invalid baseline aggregate ordering"):
        ConversationContextBuilder.build_grounded_context(ev_bad_max)

    rollup_bad = get_valid_rollup()
    rollup_bad["aggregates"] = {"average": 20.0, "min": 25.0, "max": 30.0}
    with pytest.raises(EvidenceMissingError, match="Invalid historical rollup aggregate ordering"):
        ConversationContextBuilder.build_grounded_context(get_valid_evidence(), historical_rollup=rollup_bad)


def test_context_builder_rejects_missing_aggregates_in_rollup():
    rollup_no_avg = get_valid_rollup()
    del rollup_no_avg["aggregates"]["average"]
    with pytest.raises(EvidenceMissingError, match="aggregates missing 'average'"):
        ConversationContextBuilder.build_grounded_context(get_valid_evidence(), historical_rollup=rollup_no_avg)

    rollup_no_dict = get_valid_rollup()
    rollup_no_dict["aggregates"] = None
    with pytest.raises(EvidenceMissingError, match="missing 'aggregates' dictionary"):
        ConversationContextBuilder.build_grounded_context(get_valid_evidence(), historical_rollup=rollup_no_dict)


def test_context_builder_rejects_invalid_window_duration():
    rollup_zero_min = get_valid_rollup()
    rollup_zero_min["window_minutes"] = 0
    with pytest.raises(EvidenceMissingError, match="window duration must be positive"):
        ConversationContextBuilder.build_grounded_context(get_valid_evidence(), historical_rollup=rollup_zero_min)

    rollup_neg_min = get_valid_rollup()
    rollup_neg_min["window_minutes"] = -10
    with pytest.raises(EvidenceMissingError, match="window duration must be positive"):
        ConversationContextBuilder.build_grounded_context(get_valid_evidence(), historical_rollup=rollup_neg_min)


def test_context_builder_rejects_unit_mismatch():
    rollup_bad_unit = get_valid_rollup()
    rollup_bad_unit["unit"] = "fahrenheit"
    with pytest.raises(EvidenceMissingError, match="Historical rollup unit mismatch"):
        ConversationContextBuilder.build_grounded_context(get_valid_evidence(), historical_rollup=rollup_bad_unit)


def test_context_builder_unknown_dtc():
    evidence = get_valid_evidence()
    evidence["event"]["dtc_code"] = "P9999"
    ctx = ConversationContextBuilder.build_grounded_context(evidence)

    snapshot = ctx["active_fault_snapshot"]
    assert snapshot["dtc_reference_status"] == "UNKNOWN_CODE"
    assert snapshot["dtc_title"] is None
    assert snapshot["dtc_description"] is None
    assert snapshot["service_procedure"] is None


def test_context_builder_handles_failed_historical_rollup_independently():
    evidence = get_valid_evidence()
    rollup = {"status": "NO_DATA"}
    ctx = ConversationContextBuilder.build_grounded_context(evidence, historical_rollup=rollup)

    assert ctx["availability_status"] == {
        "event_evidence_status": "AVAILABLE",
        "pre_fault_baseline_status": "AVAILABLE",
        "historical_rollup_status": "UNAVAILABLE",
    }
    assert ctx["long_term_historical_rollup"]["status"] == "UNAVAILABLE"
    assert ctx["active_fault_snapshot"]["trigger_measurement"]["value"] == 47.5
    assert ctx["active_fault_snapshot"]["pre_fault_24h_baseline"]["average"] == 32.43
    assert ctx["provenance_reconciliation"]["long_term_rollup_avg"] is None
    assert "long-term recorded database average is unavailable" in ctx["provenance_reconciliation"]["distinction"]
