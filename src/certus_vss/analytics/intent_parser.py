"""
Deterministic query intent classifier.
"""


ACQUISITION_KEYWORDS = {
    "carla",
    "acquisition",
    "telemetry",
    "vehicle speed",
    "speed",
    "velocity",
    "acceleration",
    "position",
    "angular velocity",
    "throttle",
    "brake",
    "steering",
    "steer",
    "gear",
    "weather",
    "sensor",
}

DIAGNOSTIC_KEYWORDS = {
    "diagnostic",
    "diagnostics",
    "fault",
    "dtc",
    "trouble code",
    "freeze frame",
    "malfunction",
    "error code",
    "fault code",
}


def parse_intent(query: str):
    """Classify a user query without invoking an LLM."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")

    normalized = query.casefold()

    acquisition_score = sum(
        keyword in normalized for keyword in ACQUISITION_KEYWORDS
    )
    diagnostic_score = sum(
        keyword in normalized for keyword in DIAGNOSTIC_KEYWORDS
    )

    if acquisition_score > diagnostic_score and acquisition_score > 0:
        intent = "ACQUISITION"
    elif diagnostic_score > acquisition_score and diagnostic_score > 0:
        intent = "DIAGNOSTIC"
    else:
        intent = "GENERAL"

    return {
        "intent": intent,
        "query": query.strip(),
        "confidence": (
            "HIGH"
            if max(acquisition_score, diagnostic_score) >= 2
            else "LOW"
        ),
    }