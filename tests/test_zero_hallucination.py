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
