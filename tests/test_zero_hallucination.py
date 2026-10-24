from certus_vss.inference.llama_runtime import generate_explanation


def test_inference_rejects_non_dictionary_evidence():
    try:
        generate_explanation("Vehicle speed is 0")
    except ValueError as exc:
        assert "evidence_json must be a dictionary" in str(exc)
    else:
        raise AssertionError("Expected ValueError for non-dictionary evidence")