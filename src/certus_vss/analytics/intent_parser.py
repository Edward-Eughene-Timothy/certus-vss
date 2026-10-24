"""
Classify vehicle diagnostic questions into structured, validated intents.
"""

import re
from typing import Any


SIGNAL_ALIASES = {
    "speed": "Vehicle.Speed",
    "vehicle speed": "Vehicle.Speed",
    "battery temperature": "Vehicle.Battery.Temperature",
    "battery temp": "Vehicle.Battery.Temperature",
    "state of charge": "Vehicle.Battery.StateOfCharge",
    "battery soc": "Vehicle.Battery.StateOfCharge",
    "battery current": "Vehicle.Battery.Current",
    "motor coolant temperature": "Vehicle.Powertrain.TractionMotor.CoolantTemperature",
    "cabin hvac power": "Vehicle.Cabin.HVAC.Power",
}


def parse_intent(query: str) -> dict[str, Any]:
    """Return a structured intent without generating or executing SQL."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")

    text = re.sub(r"\s+", " ", query.strip().lower())

    if any(term in text for term in ("trouble code", "dtc", "fault code", "diagnostic code")):
        intent = "diagnostic_events"
    elif any(term in text for term in ("safety event", "warning", "critical event", "fault event")):
        intent = "safety_events"
    elif any(term in text for term in ("freeze frame", "freeze-frame", "fault snapshot")):
        intent = "freeze_frames"
    else:
        intent = "telemetry"

    signal = None
    for alias in sorted(SIGNAL_ALIASES, key=len, reverse=True):
        if alias in text:
            signal = SIGNAL_ALIASES[alias]
            break

    period_seconds = 3600
    period_patterns = (
        (r"\blast\s+(\d+)\s+minutes?\b", 60),
        (r"\blast\s+(\d+)\s+hours?\b", 3600),
        (r"\blast\s+(\d+)\s+days?\b", 86400),
    )
    for pattern, multiplier in period_patterns:
        match = re.search(pattern, text)
        if match:
            period_seconds = min(int(match.group(1)) * multiplier, 30 * 86400)
            break

    return {
        "intent": intent,
        "signal": signal,
        "period_seconds": period_seconds,
        "original_query": query.strip(),
    }