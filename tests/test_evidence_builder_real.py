from certus_vss.analytics.evidence_builder import build_evidence


def test_builds_structured_evidence():
    result = build_evidence(
        "Show vehicle speed in the last 10 minutes",
        now_epoch_s=2_000_000_000,
    )

    assert result["intent"] == "telemetry"
    assert result["requested_signal"] == "Vehicle.Speed"
    assert result["time_window"]["period_seconds"] == 600
    assert isinstance(result["evidence"], list)
    assert result["row_count"] == len(result["evidence"])
    assert "limitations" in result


def test_reports_missing_requested_signal_when_no_data_matches():
    result = build_evidence(
        "Show battery temperature",
        now_epoch_s=2_000_000_000,
    )

    assert result["requested_signal"] == "Vehicle.Battery.Temperature"

    if result["row_count"] == 0:
        assert "Vehicle.Battery.Temperature" in result["missing_signals"]


def test_preserves_data_provenance():
    result = build_evidence(
        "Show telemetry in the last 24 hours",
        now_epoch_s=2_000_000_000,
    )

    assert isinstance(result["provenance"], list)
    for row in result["evidence"]:
        assert "provenance" in row


def test_diagnostic_event_evidence():
    result = build_evidence(
        "Show diagnostic trouble codes",
        now_epoch_s=2_000_000_000,
    )

    assert result["intent"] == "diagnostic_events"
    assert isinstance(result["evidence"], list)
    assert result["row_count"] == len(result["evidence"])