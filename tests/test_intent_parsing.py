from certus_vss.analytics.intent_parser import parse_intent


def test_vehicle_speed_query_identifies_signal():
    result = parse_intent("What was the vehicle speed in the last 10 minutes?")

    assert result["signal"] == "Vehicle.Speed"
    assert result["period_seconds"] == 600


def test_safety_event_query_identifies_intent():
    result = parse_intent("Show safety events")

    assert result["intent"] == "safety_events"