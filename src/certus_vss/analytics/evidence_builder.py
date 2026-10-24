"""
Build structured diagnostic evidence and identify missing sensor signals.
"""

from typing import Any

from certus_vss.analytics.intent_parser import parse_intent
from certus_vss.analytics.sql_executor import execute_sql


def build_evidence(
    query: str,
    *,
    now_epoch_s: int | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """
    Parse a diagnostic question, retrieve approved database results,
    and assemble evidence for the explanation layer.

    This function does not calculate new vehicle measurements or
    generate diagnostic conclusions.
    """
    intent = parse_intent(query)

    execution_options: dict[str, Any] = {
        "limit": limit,
    }
    if now_epoch_s is not None:
        execution_options["now_epoch_s"] = now_epoch_s

    result = execute_sql(intent, **execution_options)
    records = result["results"]

    observed_signals = sorted({
        row["vss_path"]
        for row in records
        if row.get("vss_path")
    })

    requested_signal = intent.get("signal")
    missing_signals = (
        [requested_signal]
        if requested_signal and requested_signal not in observed_signals
        else []
    )

    provenances = sorted({
        row["provenance"]
        for row in records
        if row.get("provenance")
    })

    return {
        "original_query": intent["original_query"],
        "intent": intent["intent"],
        "time_window": {
            "cutoff_epoch_s": result["cutoff_epoch_s"],
            "period_seconds": intent["period_seconds"],
        },
        "requested_signal": requested_signal,
        "observed_signals": observed_signals,
        "missing_signals": missing_signals,
        "provenance": provenances,
        "row_count": result["row_count"],
        "evidence": records,
        "limitations": [
            "Evidence contains only records returned by the database query.",
            "Missing data must not be interpreted as a normal sensor reading.",
            "No diagnostic conclusion is established by this evidence alone.",
        ],
    }