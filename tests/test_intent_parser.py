import pytest

from certus_vss.analytics.intent_parser import parse_intent


@pytest.mark.parametrize(
    "query, expected",
    [
        ("Show vehicle speed from CARLA", "ACQUISITION"),
        ("What is the vehicle acceleration?", "ACQUISITION"),
        ("Explain the latest DTC fault code", "DIAGNOSTIC"),
        ("Show the diagnostic freeze frame", "DIAGNOSTIC"),
        ("Hello, what can you do?", "GENERAL"),
    ],
)
def test_parse_intent(query, expected):
    result = parse_intent(query)

    assert result["intent"] == expected
    assert result["query"] == query
    assert result["confidence"] in {"HIGH", "LOW"}


@pytest.mark.parametrize("query", ["", "   ", None, 123])
def test_parse_intent_rejects_invalid_input(query):
    with pytest.raises(ValueError):
        parse_intent(query)