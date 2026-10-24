import pytest

from certus_vss.analytics.sql_executor import execute_sql


def test_rejects_unsupported_intent():
    with pytest.raises(ValueError, match="Unsupported intent"):
        execute_sql({"intent": "delete_everything"})


def test_rejects_invalid_limit():
    with pytest.raises(ValueError, match="limit"):
        execute_sql({"intent": "telemetry"}, limit=0)


def test_rejects_negative_period():
    with pytest.raises(ValueError, match="period_seconds"):
        execute_sql(
            {"intent": "telemetry", "period_seconds": -1}
        )


def test_executes_telemetry_query():
    result = execute_sql(
        {
            "intent": "telemetry",
            "signal": None,
            "period_seconds": 3600,
        },
        now_epoch_s=2_000_000_000,
    )

    assert result["intent"] == "telemetry"
    assert isinstance(result["results"], list)
    assert result["row_count"] == len(result["results"])


def test_executes_diagnostic_event_query():
    result = execute_sql(
        {"intent": "diagnostic_events", "period_seconds": 86400},
        now_epoch_s=2_000_000_000,
    )

    assert result["intent"] == "diagnostic_events"
    assert isinstance(result["results"], list)


def test_executes_safety_event_query():
    result = execute_sql(
        {"intent": "safety_events", "period_seconds": 86400},
        now_epoch_s=2_000_000_000,
    )

    assert result["intent"] == "safety_events"
    assert isinstance(result["results"], list)


def test_executes_freeze_frame_query():
    result = execute_sql(
        {"intent": "freeze_frames", "period_seconds": 86400},
        now_epoch_s=2_000_000_000,
    )

    assert result["intent"] == "freeze_frames"
    assert isinstance(result["results"], list)