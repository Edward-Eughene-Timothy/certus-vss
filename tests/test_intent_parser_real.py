import pytest

from certus_vss.analytics.intent_parser import parse_intent


def test_parses_vehicle_speed():
    result = parse_intent("What was the vehicle speed in the last 10 minutes?")

    assert result["intent"] == "telemetry"
    assert result["signal"] == "Vehicle.Speed"
    assert result["period_seconds"] == 600


def test_parses_battery_temperature():
    result = parse_intent("Show battery temperature")

    assert result["signal"] == "Vehicle.Battery.Temperature"


def test_parses_diagnostic_trouble_codes():
    result = parse_intent("Show diagnostic trouble codes")

    assert result["intent"] == "diagnostic_events"


def test_parses_safety_events():
    result = parse_intent("Show recent safety events")

    assert result["intent"] == "safety_events"


def test_parses_freeze_frames():
    result = parse_intent("Show the fault freeze-frame")

    assert result["intent"] == "freeze_frames"


def test_rejects_empty_query():
    with pytest.raises(ValueError):
        parse_intent("   ")


def test_rejects_non_string_query():
    with pytest.raises(ValueError):
        parse_intent(None)