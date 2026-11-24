"""
validator.py: Strict claim & numeric token verification against SQLite evidence.
"""
import math
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


def verify_acquisition_parity(
    response: Dict[str, Any],
    evidence: Dict[str, Any],
) -> None:
    """Require signal identity and at least one grounded measurement."""
    if not isinstance(response, dict):
        raise ParityMismatchError(
            "Acquisition response must be a dictionary."
        )

    samples = evidence.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ParityMismatchError(
            "No acquisition samples available for verification."
        )

    summary = response.get("summary")
    key_facts = response.get("key_facts")

    if not isinstance(summary, str) or not summary.strip():
        raise ParityMismatchError("Missing acquisition summary.")

    if not isinstance(key_facts, list) or not key_facts:
        raise ParityMismatchError(
            "Acquisition key_facts must be a non-empty list."
        )

    if not all(isinstance(item, str) and item.strip() for item in key_facts):
        raise ParityMismatchError("Invalid acquisition key_facts.")

    combined = summary + " " + " ".join(key_facts)
    combined_lower = combined.casefold()

    signal = evidence.get("signal")
    if not isinstance(signal, str) or not signal.strip():
        signal = samples[0].get("signal")

    if not isinstance(signal, str) or signal.casefold() not in combined_lower:
        raise ParityMismatchError(
            "Response does not identify the supplied acquisition signal."
        )

    extracted = {
        round(float(number), 6)
        for number in _NUMBER_PATTERN.findall(combined)
    }

    allowed = set()

    def collect_numbers(value: Any) -> None:
        if isinstance(value, bool) or value is None:
            return

        if isinstance(value, (int, float)):
            if not math.isfinite(value):
                raise ParityMismatchError(
                    "Non-finite numeric value in acquisition evidence."
                )
            allowed.add(round(float(value), 6))

        elif isinstance(value, (list, tuple)):
            for item in value:
                collect_numbers(item)

        elif isinstance(value, dict):
            for item in value.values():
                collect_numbers(item)

    for sample in samples:
        if not isinstance(sample, dict):
            raise ParityMismatchError(
                "Invalid acquisition sample structure."
            )
        collect_numbers(sample.get("value"))

    if not allowed:
        raise ParityMismatchError(
            "Acquisition evidence contains no numeric measurement."
        )

    if not extracted:
        raise ParityMismatchError(
            "Response omits the numeric acquisition measurement."
        )

    unsupported = extracted - allowed

    if unsupported:
        raise ParityMismatchError(
            f"Ungrounded acquisition numeric claims: {sorted(unsupported)}"
        )
