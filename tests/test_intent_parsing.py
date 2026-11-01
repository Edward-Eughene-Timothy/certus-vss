"""
test_intent_parsing.py: Verifies DeterministicQueryEngine query routing and fail-closed unknown types.
"""
import json
import pytest
from certus_vss.analytics.query_engine import DeterministicQueryEngine


def test_query_type_routing_and_rejection() -> None:
    engine = DeterministicQueryEngine()

    diag_json = engine.build_evidence_block("diagnostics", limit=2)
    diag_data = json.loads(diag_json)
    assert diag_data["status"] == "SUCCESS"
    assert diag_data["event_count"] >= 1

    fault_json = engine.build_evidence_block(
        "fault_analysis",
        vss_path="Vehicle.Powertrain.TractionBattery.Temperature",
    )
    fault_data = json.loads(fault_json)
    assert fault_data["event_metadata"]["dtc_code"] in ("P0A7F", "U0100")
    assert fault_data["freeze_frame"]["status"] == "SUCCESS"

    with pytest.raises(ValueError, match="Unknown query_type"):
        engine.build_evidence_block("unsupported_intent_type")
