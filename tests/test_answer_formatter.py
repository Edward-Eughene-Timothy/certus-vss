"""
test_answer_formatter.py: Verifies text rendering from structured persona views.
"""
import pytest
from certus_vss.presentation.answer_formatter import (
    format_driver_answer,
    format_fleet_answer,
    format_technician_answer,
    render_persona_report,
)


@pytest.fixture
def driver_view():
    return {
        "role": "driver",
        "alert_level": "CRITICAL",
        "signal": "Vehicle.Powertrain.TractionBattery.Temperature",
        "measured_value": {"value": 47.5, "unit": "celsius"},
        "summary": "Fault P0A7F at 47.5",
        "action": "Refer to vehicle safety guidance.",
    }


@pytest.fixture
def technician_view():
    return {
        "role": "technician",
        "dtc_code": "P0A7F",
        "vss_path": "Vehicle.Powertrain.TractionBattery.Temperature",
        "observed_trigger": {"value": 47.5, "unit": "celsius"},
        "waveform_bounds": {
            "min": {"value": 36.0, "unit": "celsius"},
            "max": {"value": 43.0, "unit": "celsius"},
        },
        "baseline_stats": {
            "avg": {"value": 28.67, "unit": "celsius"},
            "min": {"value": 25.0, "unit": "celsius"},
            "max": {"value": 32.0, "unit": "celsius"},
        },
        "service_procedure": "PROC-DOC-P0A7F",
    }


@pytest.fixture
def fleet_view():
    return {
        "role": "fleet_operator",
        "asset_status": "FLAGGED_FOR_INSPECTION",
        "maintenance_priority": "IMMEDIATE_ACTION",
        "operational_impact": "UNKNOWN - Vehicle scheduling and dispatch assignment data unavailable",
        "diagnostic_summary": "Fault P0A7F at 47.5",
        "trigger_signal": "Vehicle.Powertrain.TractionBattery.Temperature",
        "measured_value": {"value": 47.5, "unit": "celsius"},
    }


def test_render_driver_report(driver_view):
    report = render_persona_report(driver_view)
    assert "[CRITICAL] Fault P0A7F at 47.5" in report
    assert "Observed reading: 47.5 celsius" in report
    assert "Notice: Refer to vehicle safety guidance." in report


def test_render_technician_report(technician_view):
    report = render_persona_report(technician_view)
    assert "Diagnostic Trouble Code: P0A7F" in report
    assert "Vehicle.Powertrain.TractionBattery.Temperature" in report
    assert "Trigger Measurement:     47.5 celsius" in report
    assert "Waveform Bounds:         Min 36.0 celsius | Max 43.0 celsius" in report
    assert "Historical Baseline Avg: 28.67 celsius" in report
    assert "Service Procedure Ref:   PROC-DOC-P0A7F" in report


def test_render_fleet_report(fleet_view):
    report = render_persona_report(fleet_view)
    assert "Fleet Asset Status:      FLAGGED_FOR_INSPECTION" in report
    assert "Maintenance Priority:    IMMEDIATE_ACTION" in report
    assert "Operational Impact:      UNKNOWN" in report
    assert "Telemetry Summary:       Fault P0A7F at 47.5 (47.5 celsius)" in report


def test_invalid_persona_view_raises():
    with pytest.raises(ValueError, match="persona_view must be a dictionary"):
        render_persona_report("not-a-dict")

    with pytest.raises(ValueError, match="Unsupported or missing role"):
        render_persona_report({"role": "invalid_role"})
