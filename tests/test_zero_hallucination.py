"""
test_zero_hallucination.py: End-to-end latency, parity rejection, fail-closed, and timeout tests.
"""
import pytest
from certus_vss.analytics.evidence_builder import EvidenceBuilder, EvidenceMissingError
from certus_vss.inference.llama_runtime import SLMDiagnosticRuntime
from certus_vss.inference.validator import ParityMismatchError, verify_diagnostic_parity
from certus_vss.inference.watchdog import DeadlineExceededError


def test_slm_end_to_end_latency_and_safety_gates() -> None:
    builder = EvidenceBuilder()
    evidence = builder.build_event_evidence(event_id=1)

    # 1. Verify fail-closed rejection on missing evidence
    with pytest.raises(EvidenceMissingError):
        builder.build_event_evidence(event_id=999999)

    # 2. Verify parity gate explicitly rejects hallucinated numbers and substring traps
    with pytest.raises(ParityMismatchError):
        verify_diagnostic_parity(
            {"summary": "Fault P0A7F at 37.4", "key_facts": ["Trigger value 37.4"]},
            evidence,
        )
    with pytest.raises(ParityMismatchError):
        verify_diagnostic_parity(
            {"summary": "Fault P0A7F at 142.85", "key_facts": ["Trigger value 142.85"]},
            evidence,
        )

    runtime = SLMDiagnosticRuntime()
    try:
        # 3. Verify fail-closed when required fields are stripped from evidence
        bad_evidence = {"event": {"dtc_code": "P0A7F", "trigger_value": None}}
        with pytest.raises(EvidenceMissingError):
            runtime.generate_diagnostic(bad_evidence)

        # 4. Verify end-to-end latency (SQL + EvidenceBuilder + SLM + Validator < 1850ms)
        result = runtime.diagnose_event_end_to_end(event_id=1, deadline_ms=1850.0)
        assert result["status"] == "SUCCESS"
        assert result["latency_ms"] < 1850.0, f"End-to-end latency {result['latency_ms']}ms >= 1850ms"
        assert result["diagnostics"]["evidence_status"] == "VERIFIED"

        # 5. Verify hard watchdog preemption when worker stalls past deadline
        with pytest.raises(DeadlineExceededError):
            runtime.generate_diagnostic(
                evidence,
                deadline_ms=300.0,
                simulate_stall_ms=800.0,
            )

        # 6. Verify persistent worker automatically recovered after SIGKILL preemption
        recovered = runtime.diagnose_event_end_to_end(event_id=1, deadline_ms=1850.0)
        assert recovered["status"] == "SUCCESS"
        assert recovered["latency_ms"] < 1850.0
    finally:
        runtime.close()


if __name__ == "__main__":
    test_slm_end_to_end_latency_and_safety_gates()
    print("[Pass] All end-to-end latency, parity, fail-closed, and watchdog preemption checks passed!")

def test_acquisition_validator_accepts_grounded_values():
    from certus_vss.inference.validator import verify_acquisition_parity

    evidence = {
        "signal": "vehicle.speed",
        "samples": [
            {"signal": "vehicle.speed", "value": 36.0},
            {"signal": "vehicle.speed", "value": [1.0, 2.0, 3.0]},
        ],
    }

    response = {
        "summary": "vehicle.speed is 36.0 km/h.",
        "key_facts": [
            "Acceleration vector is 1.0, 2.0, 3.0."
        ],
    }

    verify_acquisition_parity(response, evidence)


def test_acquisition_validator_rejects_ungrounded_values():
    from certus_vss.inference.validator import (
        verify_acquisition_parity,
        ParityMismatchError,
    )

    evidence = {
        "samples": [
            {"value": 36.0},
        ]
    }

    response = {
        "summary": "Vehicle speed is 36.0 km/h.",
        "key_facts": [
            "Vehicle reached 120 km/h."
        ],
    }

    with pytest.raises(ParityMismatchError):
        verify_acquisition_parity(response, evidence)


def test_acquisition_validator_rejects_missing_samples():
    from certus_vss.inference.validator import (
        verify_acquisition_parity,
        ParityMismatchError,
    )

    response = {
        "summary": "Vehicle speed is 36.0 km/h.",
        "key_facts": ["Speed recorded."],
    }

    with pytest.raises(ParityMismatchError):
        verify_acquisition_parity(response, {"samples": []})

def test_acquisition_payload_preserves_all_small_sample_sets():
    from certus_vss.inference.llama_runtime import (
        prepare_acquisition_inference_evidence,
    )

    evidence = {
        "status": "SUCCESS",
        "actor_id": 24,
        "signal": "vehicle.speed",
        "sample_count": 2,
        "samples": [
            {"sample_id": 1, "value": 36.0, "unit": "km/h"},
            {"sample_id": 2, "value": 42.0, "unit": "km/h"},
        ],
    }

    result = prepare_acquisition_inference_evidence(evidence)

    assert result["samples"] == evidence["samples"]
    assert result["samples_returned"] == 2
    assert result["samples_supplied_to_model"] == 2
    assert result["sample_selection"] == "all"
    assert result["actor_id"] == 24


def test_acquisition_payload_selects_first_middle_and_last():
    from certus_vss.inference.llama_runtime import (
        prepare_acquisition_inference_evidence,
    )

    evidence = {
        "sample_count": 5,
        "samples": [
            {"sample_id": i, "value": float(i)}
            for i in range(1, 6)
        ],
    }

    result = prepare_acquisition_inference_evidence(evidence)

    assert [s["sample_id"] for s in result["samples"]] == [1, 3, 5]
    assert result["samples_returned"] == 5
    assert result["samples_supplied_to_model"] == 3
    assert result["sample_selection"] == "first_middle_last"


def test_acquisition_payload_rejects_inconsistent_sample_count():
    from certus_vss.inference.llama_runtime import (
        prepare_acquisition_inference_evidence,
    )

    evidence = {
        "sample_count": 4,
        "samples": [{"sample_id": 1, "value": 36.0}],
    }

    with pytest.raises(ValueError):
        prepare_acquisition_inference_evidence(evidence)

def test_acquisition_validator_rejects_missing_measurement():
    import pytest

    from certus_vss.inference.validator import (
        ParityMismatchError,
        verify_acquisition_parity,
    )

    evidence = {
        "signal": "vehicle.speed",
        "samples": [
            {"signal": "vehicle.speed", "value": 36.0}
        ],
    }
    response = {
        "summary": "The supplied signal is vehicle.speed.",
        "key_facts": ["vehicle.speed"],
    }

    with pytest.raises(ParityMismatchError):
        verify_acquisition_parity(response, evidence)