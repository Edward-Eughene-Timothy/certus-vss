"""
context_builder.py: Builds deterministic, verified factual context payloads
for the conversational SLM layer. Disallows hallucination of DTC meanings,
unsupported sensors, and contradictory baselines.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional

from certus_vss.catalog.signals import CANONICAL_VSS_SIGNALS, get_signal_unit
from certus_vss.catalog.dtc_catalog import get_dtc_reference
from certus_vss.analytics.evidence_builder import EvidenceMissingError


def _require_finite_float(val: Any, name: str) -> float:
    """Validates that a field is strictly numeric (float or int), non-boolean, non-string, and finite."""
    if val is None or isinstance(val, bool) or isinstance(val, str):
        raise EvidenceMissingError(f"Invalid non-numeric type for {name}: {type(val).__name__} ({val!r})")
    try:
        f_val = float(val)
    except (ValueError, TypeError) as err:
        raise EvidenceMissingError(f"Invalid numeric field {name}: {val!r}") from err
    if not math.isfinite(f_val):
        raise EvidenceMissingError(f"Non-finite numeric value for {name}: {f_val}")
    return f_val


class ConversationContextBuilder:
    """Builds grounded, verified context structures for SLM conversational synthesis."""

    @staticmethod
    def build_grounded_context(
        evidence: Dict[str, Any],
        historical_rollup: Optional[Dict[str, Any]] = None,
        role: str = "driver",
    ) -> Dict[str, Any]:
        """
        Validates evidence strictly and builds structured, provenance-labeled facts.
        Fails closed on missing, non-finite, out-of-order, or invalid baseline/event/rollup fields.
        Supports independent availability statuses for event evidence, 24h baseline, and historical rollup.
        """
        if not isinstance(evidence, dict) or "event" not in evidence:
            raise EvidenceMissingError("Evidence missing required event metadata.")

        event = evidence["event"]
        if not isinstance(event, dict):
            raise EvidenceMissingError("Event metadata must be a dictionary.")

        # 1. Validate event timestamp: reject fractional timestamps explicitly
        raw_ts = _require_finite_float(event.get("timestamp_utc_s"), "event.timestamp_utc_s")
        if not float(raw_ts).is_integer():
            raise EvidenceMissingError(f"Fractional event timestamp is not supported: {raw_ts}")
        event_time_s = int(raw_ts)

        # 2. Validate trigger signal and canonical catalog membership
        trigger_sig = event.get("trigger_signal")
        if not isinstance(trigger_sig, str) or not trigger_sig.strip():
            raise EvidenceMissingError("Evidence missing required string: trigger_signal")
        trigger_sig = trigger_sig.strip()

        if trigger_sig not in CANONICAL_VSS_SIGNALS:
            raise EvidenceMissingError(
                f"trigger_signal '{trigger_sig}' is not in CANONICAL_VSS_SIGNALS catalog."
            )

        # 3. Require valid canonical unit; reject unknown units
        unit = get_signal_unit(trigger_sig)
        if unit is None:
            raise EvidenceMissingError(f"Canonical unit missing or unknown for signal: '{trigger_sig}'")

        # 4. Validate trigger value
        trigger_val = _require_finite_float(event.get("trigger_value"), "event.trigger_value")

        # 5. Validate pre-fault baseline (avg, min, max required, finite, and ordered)
        baseline = evidence.get("baseline")
        if not isinstance(baseline, dict):
            raise EvidenceMissingError("Pre-fault baseline is missing or not a dictionary.")

        required_baseline_fields = ("avg", "min", "max")
        for field in required_baseline_fields:
            if field not in baseline:
                raise EvidenceMissingError(f"Pre-fault baseline is missing required field: {field}")

        b_avg = _require_finite_float(baseline.get("avg"), "baseline.avg")
        b_min = _require_finite_float(baseline.get("min"), "baseline.min")
        b_max = _require_finite_float(baseline.get("max"), "baseline.max")

        if not (b_min <= b_avg <= b_max):
            raise EvidenceMissingError(
                f"Invalid baseline aggregate ordering: min ({b_min}) <= avg ({b_avg}) <= max ({b_max}) violated."
            )

        # 6. Process DTC Reference (fail-closed, explicit status, strictly provisional)
        dtc = event.get("dtc_code")
        dtc_code_str = dtc.strip() if isinstance(dtc, str) and dtc.strip() else None
        dtc_ref = get_dtc_reference(dtc_code_str)

        if dtc_ref:
            dtc_status = "PROVISIONAL_REFERENCE"
            dtc_title = dtc_ref["title"]
            dtc_desc = dtc_ref["description"]
            dtc_proc = dtc_ref["service_procedure"]
        else:
            dtc_status = "UNKNOWN_CODE"
            dtc_title = None
            dtc_desc = None
            dtc_proc = None

        context: Dict[str, Any] = {
            "role": role,
            "vehicle_powertrain": "Battery Electric Vehicle (BEV)",
            "canonical_signals": list(CANONICAL_VSS_SIGNALS),
            "availability_status": {
                "event_evidence_status": "AVAILABLE",
                "pre_fault_baseline_status": "AVAILABLE",
                "historical_rollup_status": "UNAVAILABLE",
            },
            "active_fault_snapshot": {
                "event_id": event.get("event_id"),
                "event_timestamp_utc_s": event_time_s,
                "dtc_code": dtc_code_str,
                "dtc_reference_status": dtc_status,
                "dtc_title": dtc_title,
                "dtc_description": dtc_desc,
                "service_procedure": dtc_proc,
                "trigger_signal": trigger_sig,
                "trigger_measurement": {
                    "value": trigger_val,
                    "unit": unit,
                    "provenance": f"Measurement recorded at event timestamp {event_time_s}",
                },
                "pre_fault_24h_baseline": {
                    "window": f"24 hours preceding event timestamp ({event_time_s - 86400} to {event_time_s})",
                    "average": b_avg,
                    "min": b_min,
                    "max": b_max,
                    "unit": unit,
                },
            },
        }

        # 7. Validate and process historical rollup independently
        if historical_rollup is None:
            context["long_term_historical_rollup"] = {
                "status": "UNAVAILABLE",
                "reason": "Historical rollup was not provided.",
            }
            context["provenance_reconciliation"] = {
                "pre_fault_24h_avg": b_avg,
                "long_term_rollup_avg": None,
                "trigger_measurement": trigger_val,
                "distinction": (
                    f"The 24-hour pre-fault baseline average was {b_avg} {unit}. "
                    f"The event record contains a trigger measurement of {trigger_val} {unit}. "
                    "The long-term recorded database average is unavailable, so a comparison against "
                    "the complete recorded database window cannot be established."
                ),
            }
        else:
            if not isinstance(historical_rollup, dict):
                raise EvidenceMissingError("Historical rollup must be a dictionary if provided.")

            status = historical_rollup.get("status")
            if status != "SUCCESS":
                context["long_term_historical_rollup"] = {
                    "status": "UNAVAILABLE",
                    "reason": f"Historical rollup status is {status!r}.",
                }
                context["provenance_reconciliation"] = {
                    "pre_fault_24h_avg": b_avg,
                    "long_term_rollup_avg": None,
                    "trigger_measurement": trigger_val,
                    "distinction": (
                        f"The 24-hour pre-fault baseline average was {b_avg} {unit}. "
                        f"The event record contains a trigger measurement of {trigger_val} {unit}. "
                        "The long-term recorded database average is unavailable, so a comparison against "
                        "the complete recorded database window cannot be established."
                    ),
                }
            else:
                # Require and validate unit matches canonical unit
                r_unit = historical_rollup.get("unit")
                if r_unit != unit:
                    raise EvidenceMissingError(
                        f"Historical rollup unit mismatch: expected '{unit}', got '{r_unit}'."
                    )

                # Validate window metadata
                raw_mins = historical_rollup.get("window_minutes")
                if raw_mins is None:
                    raise EvidenceMissingError("Historical rollup missing 'window_minutes'.")
                f_mins = _require_finite_float(raw_mins, "historical_rollup.window_minutes")
                if f_mins <= 0:
                    raise EvidenceMissingError(
                        f"Historical rollup window duration must be positive: {f_mins}"
                    )
                mins = int(f_mins) if float(f_mins).is_integer() else f_mins

                # Validate aggregates presence and ordering
                aggs = historical_rollup.get("aggregates")
                if not isinstance(aggs, dict):
                    raise EvidenceMissingError("Historical rollup missing 'aggregates' dictionary.")

                for field in ("average", "min", "max"):
                    if field not in aggs:
                        raise EvidenceMissingError(f"Historical rollup aggregates missing '{field}'.")

                r_avg = _require_finite_float(aggs.get("average"), "historical_rollup.average")
                r_min = _require_finite_float(aggs.get("min"), "historical_rollup.min")
                r_max = _require_finite_float(aggs.get("max"), "historical_rollup.max")

                if not (r_min <= r_avg <= r_max):
                    raise EvidenceMissingError(
                        f"Invalid historical rollup aggregate ordering: min ({r_min}) <= avg ({r_avg}) <= max ({r_max}) violated."
                    )

                context["availability_status"]["historical_rollup_status"] = "AVAILABLE"

                rollup_entry: Dict[str, Any] = {
                    "status": "SUCCESS",
                    "window_minutes": mins,
                    "average": r_avg,
                    "min": r_min,
                    "max": r_max,
                    "unit": r_unit,
                }

                # Report actual start and end timestamps if available
                if "start_epoch_s" in historical_rollup and "end_epoch_s" in historical_rollup:
                    s_epoch = _require_finite_float(historical_rollup["start_epoch_s"], "historical_rollup.start_epoch_s")
                    e_epoch = _require_finite_float(historical_rollup["end_epoch_s"], "historical_rollup.end_epoch_s")
                    if s_epoch > e_epoch:
                        raise EvidenceMissingError(
                            f"Historical rollup start_epoch_s ({s_epoch}) cannot be greater than end_epoch_s ({e_epoch})."
                        )
                    rollup_entry["start_epoch_s"] = int(s_epoch) if float(s_epoch).is_integer() else s_epoch
                    rollup_entry["end_epoch_s"] = int(e_epoch) if float(e_epoch).is_integer() else e_epoch
                    rollup_entry["window"] = f"Recorded database window ({rollup_entry['start_epoch_s']} to {rollup_entry['end_epoch_s']}, {mins} minutes)"
                else:
                    rollup_entry["window"] = f"Full recorded database window ({mins} minutes)"

                context["long_term_historical_rollup"] = rollup_entry

                context["provenance_reconciliation"] = {
                    "pre_fault_24h_avg": b_avg,
                    "long_term_rollup_avg": r_avg,
                    "trigger_measurement": trigger_val,
                    "distinction": (
                        f"The 24-hour pre-fault baseline average was {b_avg} {unit}. "
                        f"The long-term recorded database average was {r_avg} {r_unit}. "
                        f"The event record contains a trigger measurement of {trigger_val} {unit}."
                    ),
                }

        return context
