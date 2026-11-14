"""
persona_formatter.py: Deterministic role-based persona explanation layer.

Consumes validated SQLite evidence and certified SLM diagnostics.
Execution order:
1. Role check & container types
2. Diagnostic certification check
3. Evidence structure & numeric validation (raises IncompleteEvidenceError)
4. Boundary diagnostic parity re-verification (raises UncertifiedDiagnosticError)
5. Persona generation
"""
from __future__ import annotations

import math
from typing import Any, Dict, Literal, Optional

from certus_vss.catalog.signals import get_signal_unit
from certus_vss.inference.validator import ParityMismatchError, verify_diagnostic_parity

RoleType = Literal["driver", "technician", "fleet_operator"]

VALID_ROLES = {"driver", "technician", "fleet_operator"}
VALID_SEVERITIES = {"CRITICAL", "WARNING", "ADVISORY"}

# Descriptive, non-prescriptive advisory alerts
_DRIVER_ALERTS = {
    "CRITICAL": {
        "THERMAL": "Critical thermal threshold reached. Refer to vehicle safety guidance.",
        "BRAKE": "Critical braking anomaly detected. Refer to vehicle safety guidance.",
        "POWERTRAIN": "Critical powertrain fault detected. Refer to vehicle safety guidance.",
        "DEFAULT": "Critical system event detected. Refer to vehicle safety guidance.",
    },
    "WARNING": {
        "THERMAL": "Elevated thermal reading observed above typical operating levels.",
        "DEFAULT": "System warning recorded. Diagnostic follow-up recommended.",
    },
    "ADVISORY": {
        "DEFAULT": "Diagnostic notice recorded for telemetry monitoring.",
    },
}

_FLEET_PRIORITIES = {
    "CRITICAL": "IMMEDIATE_ACTION",
    "WARNING": "SCHEDULED_WINDOW",
    "ADVISORY": "MONITOR_ONLY",
}


class UncertifiedDiagnosticError(ValueError):
    """Raised when diagnostic output fails parity validation or lacks verification."""
    pass


class IncompleteEvidenceError(ValueError):
    """Raised when required schema fields or numeric values are missing or malformed."""
    pass


def require_non_empty_str(data: Dict[str, Any], key: str) -> str:
    """Extracts a non-empty string or raises IncompleteEvidenceError."""
    if not isinstance(data, dict):
        raise IncompleteEvidenceError(f"Expected dict block, received: {type(data).__name__}")
    val = data.get(key)
    if not isinstance(val, str) or not val.strip():
        raise IncompleteEvidenceError(f"Field {key!r} must be a non-empty string.")
    return val.strip()


def require_number(data: Dict[str, Any], key: str) -> float:
    """Strictly extracts a finite numeric float or raises IncompleteEvidenceError."""
    if not isinstance(data, dict):
        raise IncompleteEvidenceError(f"Expected dict block, received: {type(data).__name__}")
    value = data.get(key)
    if value is None or isinstance(value, bool):
        raise IncompleteEvidenceError(f"Missing required numeric evidence: {key}")
    try:
        val_float = float(value)
    except (TypeError, ValueError) as exc:
        raise IncompleteEvidenceError(f"Invalid non-numeric evidence for {key}: {value!r}") from exc
    if not math.isfinite(val_float):
        raise IncompleteEvidenceError(f"Invalid non-finite evidence for {key}: {value!r}")
    return round(val_float, 2)


def format_persona_view(
    evidence: Dict[str, Any],
    diagnostic: Dict[str, Any],
    role: RoleType,
    service_manual_lookup: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Format deterministic role-based persona views after validating diagnostic parity.
    """
    if role not in VALID_ROLES:
        raise ValueError(f"Invalid role: {role!r}. Must be one of {sorted(VALID_ROLES)}")

    if not isinstance(evidence, dict) or not isinstance(diagnostic, dict):
        raise IncompleteEvidenceError("Evidence and diagnostic payloads must be dictionaries.")

    # 1. Diagnostic verification flag check
    if diagnostic.get("evidence_status") != "VERIFIED":
        raise UncertifiedDiagnosticError(
            "Diagnostic output lacks 'evidence_status' == 'VERIFIED' status flag."
        )

    # 2. Evidence structure check
    ev = evidence.get("event")
    wf = evidence.get("waveform")
    bl = evidence.get("baseline")

    if not isinstance(ev, dict) or not isinstance(wf, dict) or not isinstance(bl, dict):
        raise IncompleteEvidenceError("Incomplete evidence: 'event', 'waveform', and 'baseline' blocks are required.")

    # 3. Numeric bounds validation (fail-closed BEFORE parity checks)
    trigger_val = require_number(ev, "trigger_value")
    wf_min = require_number(wf, "min")
    wf_max = require_number(wf, "max")
    bl_avg = require_number(bl, "avg")
    bl_min = require_number(bl, "min")
    bl_max = require_number(bl, "max")

    # 4. Strict string fields
    trigger_signal_raw = ev.get("trigger_signal") or ev.get("vss_path")
    if not isinstance(trigger_signal_raw, str) or not trigger_signal_raw.strip():
        raise IncompleteEvidenceError("Missing or empty 'trigger_signal' / 'vss_path' in event evidence.")
    trigger_signal = trigger_signal_raw.strip()

    summary = require_non_empty_str(diagnostic, "summary")

    # 5. Boundary Parity Gate (Run only after evidence structure is guaranteed complete)
    try:
        verify_diagnostic_parity(diagnostic, evidence)
    except ParityMismatchError as exc:
        raise UncertifiedDiagnosticError(f"Boundary diagnostic parity verification failed: {exc}") from exc

    # 6. Unit Resolution via Public Catalog
    explicit_unit = ev.get("unit")
    if isinstance(explicit_unit, str) and explicit_unit.strip():
        unit = explicit_unit.strip()
    else:
        unit = get_signal_unit(trigger_signal) or "UNKNOWN"

    # 7. Severity Classification
    raw_severity = ev.get("severity")
    if raw_severity is not None and str(raw_severity).strip().upper() in VALID_SEVERITIES:
        severity = str(raw_severity).strip().upper()
    else:
        severity = "UNCLASSIFIED"

    category = str(ev.get("event_category", ev.get("category", "SYSTEM"))).strip().upper()
    dtc = ev.get("dtc_code")
    dtc_code = str(dtc).strip() if dtc else None

    trigger_measurement = {"value": trigger_val, "unit": unit}

    # 8. Persona Formatting
    if role == "driver":
        if severity == "UNCLASSIFIED":
            alert_msg = "An unclassified telemetry event was recorded. Refer to vehicle service documentation."
        else:
            cat_key = "THERMAL" if ("TEMPERATURE" in trigger_signal.upper() or "THERMAL" in category) else category
            policy_group = _DRIVER_ALERTS.get(severity, {})
            alert_msg = policy_group.get(cat_key, policy_group.get("DEFAULT", "Refer to vehicle service documentation."))

        return {
            "role": "driver",
            "alert_level": severity,
            "signal": trigger_signal,
            "measured_value": trigger_measurement,
            "summary": summary,
            "action": alert_msg,
        }

    elif role == "technician":
        inspection_ref = None
        if service_manual_lookup and dtc_code:
            inspection_ref = service_manual_lookup.get(dtc_code)

        return {
            "role": "technician",
            "dtc_code": dtc_code if dtc_code else "UNAVAILABLE",
            "vss_path": trigger_signal,
            "observed_trigger": trigger_measurement,
            "waveform_bounds": {
                "min": {"value": wf_min, "unit": unit},
                "max": {"value": wf_max, "unit": unit},
            },
            "baseline_stats": {
                "avg": {"value": bl_avg, "unit": unit},
                "min": {"value": bl_min, "unit": unit},
                "max": {"value": bl_max, "unit": unit},
            },
            "service_procedure": inspection_ref if inspection_ref else "UNAVAILABLE",
        }

    elif role == "fleet_operator":
        maint_priority = _FLEET_PRIORITIES.get(severity, "ASSESSMENT_REQUIRED")
        asset_status = (
            "FLAGGED_FOR_INSPECTION" if severity in {"CRITICAL", "WARNING"}
            else "ASSESSMENT_REQUIRED" if severity == "UNCLASSIFIED"
            else "MONITORING"
        )

        return {
            "role": "fleet_operator",
            "asset_status": asset_status,
            "maintenance_priority": maint_priority,
            "operational_impact": "UNKNOWN - Vehicle scheduling and dispatch assignment data unavailable",
            "diagnostic_summary": summary,
            "trigger_signal": trigger_signal,
            "measured_value": trigger_measurement,
        }
