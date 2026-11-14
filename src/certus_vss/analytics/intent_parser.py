"""
intent_parser.py: Natural language intent classifier and entity extractor.
Routes user queries into diagnostic, metric, range, follow-up, or out-of-scope intents.
Zero hardcoded example measurements are used for classification.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional
from certus_vss.catalog.signals import CANONICAL_VSS_SIGNALS

# 1. Canonical Signal Regex Mapping (strictly mapped to CANONICAL_VSS_SIGNALS)
SIGNAL_ALIASES = [
    (r"\b(air\s*condition(ing)?|a/?c|hvac|climate|heater|cooling)\b", "Vehicle.Cabin.HVAC.PowerDraw"),
    (r"\b(battery\s*temp(erature)?|thermal|battery\s*heat)\b", "Vehicle.Powertrain.TractionBattery.Temperature"),
    (r"\b(motor\s*temp|motor\s*coolant|coolant(\s*temp)?)\b", "Vehicle.Powertrain.ElectricMotor.CoolantTemperature"),
    (r"\b(soc|state\s*of\s*charge|battery(\s*level|\s*charge|\s*life)?|power\s*left|charge\s*left|remaining\s*charge)\b", "Vehicle.Powertrain.TractionBattery.StateOfCharge"),
    (r"\b(amperes?|amps|electrical\s*current)\b", "Vehicle.Powertrain.TractionBattery.Current"),
    (r"\b(speed|velocity|fast|how\s*fast)\b", "Vehicle.Speed"),
    (r"\b(temp(erature)?|hot|overheat(ing)?)\b", "Vehicle.Powertrain.TractionBattery.Temperature"),
]

# 2. Domain Boundary (EV Architecture)
OUT_OF_SCOPE_TERMS = [
    (r"\b(gas\s*mileage|fuel\s*economy|mpg)\b", "Fuel economy (MPG) is not applicable to battery-electric vehicles; energy consumption is tracked via battery State of Charge."),
    (r"\b(gas|gasoline|fuel|petrol)\b", "Fuel/gas sensors are not present on this battery-electric vehicle architecture."),
    (r"\b(engine\s*oil|oil\s*level|oil\s*pressure)\b", "Engine oil sensors are not applicable to an electric powertrain."),
    (r"\b(adas|autopilot|lane\s*keep|cruise\s*control)\b", "ADAS telemetry streams are unmonitored in this telemetry profile."),
    (r"\b(transmission\s*fluid|gearbox\s*oil)\b", "Transmission fluid sensors are not tracked on this single-speed reduction drive."),
]

# 3. New Diagnostic Inquiry Patterns
NEW_DIAGNOSTIC_PATTERNS = [
    r"\b(what('s|\s+is)\s+wrong(\s+with\s+(the|my)\s+(car|vehicle))?)\b",
    r"\b(any\s+(new\s+)?(anomalies|faults|issues|errors|alerts|dtcs|codes))\b",
    r"\b(check\s+(the\s+)?(car|vehicle|health|diagnostics|anomalies))\b",
    r"\b(is\s+there\s+any\s+(problem|fault|issue|warning|alert))\b",
    r"\b(diagnose(\s+(the|my)\s+(car|vehicle))?)\b",
    r"\b(vehicle\s+status|health\s+status|diagnostic\s+status)\b",
]

# 4. Follow-Up Patterns (Linguistic Indicators Only - No Hardcoded Values)
FOLLOWUP_PROCEDURE_PATTERNS = [
    r"\b(what\s+should\s+i\s+inspect(\s+for\s+it)?)\b",
    r"\b(explain\s+that\s+procedure)\b",
    r"\b(how\s+to\s+fix(\s+it)?|service\s+steps?|inspection\s+steps?|repair\s+steps?)\b",
    r"\b(what\s+is\s+the\s+procedure|inspection\s+procedure)\b",
]

FOLLOWUP_COMPARISON_PATTERNS = [
    r"\bwhy\s+is\s+it\s+(so\s+)?(high|higher|elevated|hot|hotter|different)\b",
    r"\bwhy\s+(is\s+this|does\s+it)\s+differ(ent)?(\s+from\s+normal)?\b",
    r"\bhow\s+does\s+(that|it)\s+compare(\s+to\s+average)?\b",
    r"\bwhy\s+the\s+(spike|difference|increase)\b",
]

FOLLOWUP_BASELINE_PATTERNS = [
    r"\bwhat\s+was\s+(the\s+)?(previous\s+)?average\b",
    r"\bwhat\s+is\s+the\s+baseline(\s+average)?\b",
    r"\bwhat\s+is\s+normal(\s+for\s+it)?\b",
]

FOLLOWUP_EXPLANATION_PATTERNS = [
    r"\bwhat\s+does\s+(that|the|this)\s+(fault|code|dtc|warning|event|error)\s+mean\b",
    r"\bwhat\s+about\s+that\s+(event|fault|code|dtc|warning)\b",
    r"\bwhy\s+(is\s+there\s+a\s+warning|did\s+it\s+trigger)\b",
    r"\bexplain\s+(that|the)\s+(fault|warning|code|event|issue)\b",
    r"\bwhat\s+(fault|dtc|code)(\s+code)?\s+is\s+(active|present|it)\b",
    r"\bwhat\s+fault\s+is\s+it\b",
]

RANGE_PATTERNS = [
    r"\b(range|distance\s*left|how\s*far(\s*can\s*i\s*go)?|kilometers\s*left|km\s*left)\b",
]

DISPATCH_PATTERNS = [
    r"\b(run|service|delivery|trip|100\s*km|available|dispatch|good\s*for)\b",
]

GREETING_PATTERNS = [
    r"^(hi|hello|hey|greetings|good\s*(morning|afternoon|evening))\b",
]


def parse_intent(query: str, state: Optional[Any] = None) -> Dict[str, Any]:
    """
    Parses intent with reference resolution against ConversationState.
    Does not depend on hardcoded numbers.
    """
    raw = query.strip()
    if not raw:
        return {"intent": "UNKNOWN", "vss_path": None, "reason": "Empty query"}

    lower = raw.lower()

    # 1. Out of Scope Check
    for pat, reason in OUT_OF_SCOPE_TERMS:
        if re.search(pat, lower):
            return {"intent": "OUT_OF_SCOPE", "vss_path": None, "reason": reason}

    # 2. Greetings
    for pat in GREETING_PATTERNS:
        if re.search(pat, lower):
            return {"intent": "GREETING", "vss_path": None, "reason": "User greeting"}

    # 3. Follow-Up Inquiries (Resolved against active ConversationState)
    has_pinned_event = state is not None and getattr(state, "active_event_id", None) is not None

    if any(re.search(pat, lower) for pat in FOLLOWUP_PROCEDURE_PATTERNS):
        return {
            "intent": "FOLLOWUP_PROCEDURE",
            "vss_path": state.active_vss_path if has_pinned_event else None,
            "target_event_id": state.active_event_id if has_pinned_event else None,
            "reason": "Procedure follow-up query",
        }

    if any(re.search(pat, lower) for pat in FOLLOWUP_COMPARISON_PATTERNS):
        return {
            "intent": "FOLLOWUP_COMPARISON",
            "vss_path": state.active_vss_path if has_pinned_event else None,
            "target_event_id": state.active_event_id if has_pinned_event else None,
            "reason": "Comparison follow-up query",
        }

    if any(re.search(pat, lower) for pat in FOLLOWUP_BASELINE_PATTERNS):
        return {
            "intent": "FOLLOWUP_BASELINE",
            "vss_path": state.active_vss_path if has_pinned_event else None,
            "target_event_id": state.active_event_id if has_pinned_event else None,
            "reason": "Baseline follow-up query",
        }

    if any(re.search(pat, lower) for pat in FOLLOWUP_EXPLANATION_PATTERNS):
        return {
            "intent": "FOLLOWUP_EXPLANATION",
            "vss_path": state.active_vss_path if has_pinned_event else None,
            "target_event_id": state.active_event_id if has_pinned_event else None,
            "reason": "DTC / fault explanation follow-up query",
        }

    # Explicit DTC mention (e.g. "Explain P0A7F")
    dtc_match = re.search(r"\b([PUBC][0-9A-F]{4})\b", raw.upper())
    if dtc_match:
        dtc_code = dtc_match.group(1)
        return {
            "intent": "DTC_QUERY",
            "dtc_code": dtc_code,
            "vss_path": state.active_vss_path if has_pinned_event else None,
            "target_event_id": state.active_event_id if has_pinned_event else None,
            "reason": f"Explicit query for DTC {dtc_code}",
        }

    # 4. Explicit New Diagnostic Inquiry
    if any(re.search(pat, lower) for pat in NEW_DIAGNOSTIC_PATTERNS):
        return {
            "intent": "NEW_DIAGNOSTIC",
            "vss_path": None,
            "reason": "Fresh diagnostic status inquiry",
        }

    # 5. Range Estimation
    if any(re.search(pat, lower) for pat in RANGE_PATTERNS):
        return {
            "intent": "RANGE_ESTIMATE",
            "vss_path": "Vehicle.Powertrain.TractionBattery.StateOfCharge",
            "reason": "Range inquiry",
        }

    # 6. Fleet Dispatch / Readiness
    if any(re.search(pat, lower) for pat in DISPATCH_PATTERNS):
        return {
            "intent": "DISPATCH_CHECK",
            "vss_path": None,
            "reason": "Trip readiness check",
        }

    # 7. Telemetry Signals
    for pat, canonical in SIGNAL_ALIASES:
        if re.search(pat, lower):
            return {
                "intent": "TELEMETRY",
                "vss_path": canonical,
                "reason": f"Signal query for {canonical}",
            }

    # 8. Conversational Fallback
    return {
        "intent": "CONVERSATIONAL",
        "vss_path": state.active_vss_path if has_pinned_event else None,
        "target_event_id": state.active_event_id if has_pinned_event else None,
        "reason": "General conversational turn",
    }
