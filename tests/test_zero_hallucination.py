import pytest

from certus_vss.inference.llama_runtime import (
    generate_explanation,
    summarize_evidence,
)


def test_inference_rejects_non_dictionary_evidence():
    with pytest.raises(
        ValueError,
        match="evidence_json must be a dictionary",
    ):
        generate_explanation("Vehicle speed is 0")


def test_evidence_summary_preserves_zero_reading():
    summary = summarize_evidence(
        {
            "intent": "telemetry",
            "row_count": 1,
            "requested_signal": "Vehicle.Speed",
            "observed_signals": ["Vehicle.Speed"],
            "missing_signals": [],
            "evidence": [
                {
                    "vss_path": "Vehicle.Speed",
                    "val_avg": 0,
                    "unit": "km/h",
                }
            ],
        }
    )

    assert "Vehicle.Speed: recorded average 0 km/h" in summary
    assert "missing" not in summary.lower()


def test_evidence_summary_reports_missing_signals():
    summary = summarize_evidence(
        {
            "intent": "telemetry",
            "row_count": 0,
            "requested_signal": "Vehicle.Speed",
            "observed_signals": [],
            "missing_signals": ["Vehicle.Speed"],
            "evidence": [],
        }
    )

    assert "Vehicle.Speed" in summary
    assert "no matching observations" in summary
    assert "No evidence records were returned." in summary