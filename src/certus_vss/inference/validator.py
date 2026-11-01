"""
validator.py: Strict claim & numeric token verification against SQLite evidence.
"""
import re
from typing import Any, Dict, Set


class ParityMismatchError(ValueError):
    """Raised when SLM output contains ungrounded numbers or omits the trigger value."""
    pass


# Matches standalone numbers (integers, decimals, negative, scientific), ignoring DTC codes like P0A7F
_NUMBER_PATTERN = re.compile(r"(?<![A-Za-z0-9_])-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?(?![A-Za-z0-9_])")


def verify_diagnostic_parity(diagnostics: Dict[str, Any], evidence: Dict[str, Any]) -> None:
    if not isinstance(diagnostics, dict):
        raise ParityMismatchError("Diagnostics payload must be a dictionary.")

    ev = evidence.get("event")
    wf = evidence.get("waveform")
    bl = evidence.get("baseline")
    if not isinstance(ev, dict) or not isinstance(wf, dict) or not isinstance(bl, dict):
        raise ParityMismatchError("Incomplete evidence structure passed to validator.")

    expected_trigger = round(float(ev["trigger_value"]), 2)
    summary_text = str(diagnostics.get("summary", ""))
    key_facts_list = diagnostics.get("key_facts", [])
    if not isinstance(key_facts_list, list) or not key_facts_list:
        raise ParityMismatchError("key_facts must be a non-empty list.")

    combined = f"{summary_text} " + " ".join(str(k) for k in key_facts_list)
    extracted_tokens = [round(float(m), 2) for m in _NUMBER_PATTERN.findall(combined)]

    if expected_trigger not in extracted_tokens:
        raise ParityMismatchError(
            f"Ground-truth trigger value {expected_trigger} not found as a standalone numeric token in {extracted_tokens}"
        )

    allowed: Set[float] = {
        expected_trigger,
        round(float(wf["min"]), 2),
        round(float(wf["max"]), 2),
        round(float(bl["avg"]), 2),
        round(float(bl["min"]), 2),
        round(float(bl["max"]), 2),
    }

    for num in extracted_tokens:
        if num not in allowed:
            raise ParityMismatchError(
                f"Ungrounded numeric claim {num} detected. Allowed SQLite values: {sorted(allowed)}"
            )
