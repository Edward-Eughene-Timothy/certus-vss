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
    assert "NO matching observations" in summary
    assert "values are unknown, not zero" in summary
    assert "Do not claim these signals are present" in summary
    assert "No evidence records were returned." in summary

def test_missing_telemetry_does_not_invoke_model(monkeypatch):
    from certus_vss.inference import llama_runtime

    def unexpected_model_call(*args, **kwargs):
        raise AssertionError("Model should not run when evidence is missing")

    monkeypatch.setattr(
        llama_runtime,
        "_generate_with_model",
        unexpected_model_call,
    )

    result = llama_runtime.generate_explanation(
        {
            "intent": "telemetry",
            "row_count": 0,
            "requested_signal": "Vehicle.Speed",
            "observed_signals": [],
            "missing_signals": ["Vehicle.Speed"],
            "evidence": [],
        }
    )

    assert "Vehicle.Speed" in result
    assert "unknown, not zero" in result
    assert "does not establish a diagnosis or root cause" in result

def test_mixed_telemetry_preserves_measurements_and_missing_signals(monkeypatch):
    from certus_vss.inference import llama_runtime

    def unexpected_model_call(*args, **kwargs):
        raise AssertionError(
            "Model should not run when requested signals are missing"
        )

    monkeypatch.setattr(
        llama_runtime,
        "_generate_with_model",
        unexpected_model_call,
    )

    result = llama_runtime.generate_explanation(
        {
            "intent": "telemetry",
            "row_count": 1,
            "requested_signal": "Vehicle.Speed",
            "observed_signals": ["Vehicle.Speed"],
            "missing_signals": ["Vehicle.Battery.Temperature"],
            "evidence": [
                {
                    "vss_path": "Vehicle.Speed",
                    "val_avg": 0,
                    "unit": "km/h",
                }
            ],
        }
    )

    assert "Vehicle.Speed: recorded average 0 km/h" in result
    assert "Vehicle.Battery.Temperature" in result
    assert "unknown, not zero" in result
    assert "does not establish a diagnosis or root cause" in result