"""
answer_formatter.py: Deterministic conversion of structured persona views
into human-readable conversational text reports.
"""
from __future__ import annotations

from typing import Any, Dict


def format_driver_answer(view: Dict[str, Any]) -> str:
    """Formats driver persona view into clean, accessible prose."""
    level = view.get("alert_level", "ALERT")
    val_obj = view.get("measured_value", {})
    val = val_obj.get("value", "N/A")
    unit = val_obj.get("unit", "")
    summary = view.get("summary", "")
    action = view.get("action", "")

    lines = [
        f"[{level}] {summary}",
        f"Observed reading: {val} {unit}".strip(),
        f"Notice: {action}",
    ]
    return "\n".join(lines)


def format_technician_answer(view: Dict[str, Any]) -> str:
    """Formats technician persona view into structured diagnostic telemetry blocks."""
    dtc = view.get("dtc_code", "UNAVAILABLE")
    path = view.get("vss_path", "UNKNOWN")
    trig = view.get("observed_trigger", {})
    wf = view.get("waveform_bounds", {})
    bl = view.get("baseline_stats", {})
    procedure = view.get("service_procedure", "UNAVAILABLE")

    trig_str = f"{trig.get('value')} {trig.get('unit', '')}".strip()
    wf_min_str = f"{wf.get('min', {}).get('value')} {wf.get('min', {}).get('unit', '')}".strip()
    wf_max_str = f"{wf.get('max', {}).get('value')} {wf.get('max', {}).get('unit', '')}".strip()
    bl_avg_str = f"{bl.get('avg', {}).get('value')} {bl.get('avg', {}).get('unit', '')}".strip()

    lines = [
        f"Diagnostic Trouble Code: {dtc}",
        f"VSS Signal Path:         {path}",
        f"Trigger Measurement:     {trig_str}",
        f"Waveform Bounds:         Min {wf_min_str} | Max {wf_max_str}",
        f"Historical Baseline Avg: {bl_avg_str}",
        f"Service Procedure Ref:   {procedure}",
    ]
    return "\n".join(lines)


def format_fleet_answer(view: Dict[str, Any]) -> str:
    """Formats fleet manager persona view into an operational triage block."""
    status = view.get("asset_status", "UNKNOWN")
    priority = view.get("maintenance_priority", "ASSESSMENT_REQUIRED")
    impact = view.get("operational_impact", "UNKNOWN")
    summary = view.get("diagnostic_summary", "")
    val_obj = view.get("measured_value", {})
    val_str = f"{val_obj.get('value')} {val_obj.get('unit', '')}".strip()

    lines = [
        f"Fleet Asset Status:      {status}",
        f"Maintenance Priority:    {priority}",
        f"Operational Impact:      {impact}",
        f"Telemetry Summary:       {summary} ({val_str})",
    ]
    return "\n".join(lines)


def render_persona_report(persona_view: Dict[str, Any]) -> str:
    """Dispatches to the appropriate role renderer."""
    if not isinstance(persona_view, dict):
        raise ValueError("persona_view must be a dictionary")

    role = persona_view.get("role")
    if role == "driver":
        return format_driver_answer(persona_view)
    elif role == "technician":
        return format_technician_answer(persona_view)
    elif role == "fleet_operator":
        return format_fleet_answer(persona_view)
    else:
        raise ValueError(f"Unsupported or missing role in persona view: {role!r}")
