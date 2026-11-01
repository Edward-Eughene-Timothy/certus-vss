"""
evidence_builder.py: Fail-closed deterministic telemetry evidence extractor.
"""
import math
from typing import Any, Dict, List, Optional
from certus_vss.storage.db_engine import get_readonly_connection
from certus_vss.analytics.query_engine import DeterministicQueryEngine


class EvidenceMissingError(ValueError):
    """Raised when required telemetry or event metadata is missing or non-finite."""
    pass


def _require_finite_float(val: Any, name: str) -> float:
    if val is None or isinstance(val, bool):
        raise EvidenceMissingError(f"Missing required numeric field: {name}")
    try:
        f_val = float(val)
    except (ValueError, TypeError) as err:
        raise EvidenceMissingError(f"Invalid numeric field {name}: {val}") from err
    if not math.isfinite(f_val):
        raise EvidenceMissingError(f"Non-finite numeric value for {name}: {f_val}")
    return f_val


class EvidenceBuilder:
    def __init__(self) -> None:
        self.engine = DeterministicQueryEngine()

    def build_event_evidence(self, event_id: int) -> Dict[str, Any]:
        with get_readonly_connection() as conn:
            row = conn.execute(
                "SELECT event_id, timestamp_utc_s, event_category, dtc_code, "
                "severity, trigger_signal, trigger_value "
                "FROM vehicle_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()

            if not row:
                raise EvidenceMissingError(f"Event ID {event_id} not found.")
            event = dict(row)

        for req_str in ("event_category", "severity", "trigger_signal"):
            val = event.get(req_str)
            if not isinstance(val, str) or not val.strip():
                raise EvidenceMissingError(f"Missing required string field: {req_str}")

        raw_dtc = event.get("dtc_code")
        dtc_code: Optional[str] = raw_dtc.strip() if isinstance(raw_dtc, str) and raw_dtc.strip() else None

        trigger_path = event["trigger_signal"].strip()
        trigger_val = _require_finite_float(event.get("trigger_value"), "trigger_value")
        event_time_s = int(_require_finite_float(event.get("timestamp_utc_s"), "timestamp_utc_s"))

        ff_res = self.engine.get_freeze_frame_waveform(event_id=event_id, vss_path=trigger_path)
        if ff_res.get("status") != "SUCCESS":
            raise EvidenceMissingError(f"Freeze-frame query failed for event {event_id}")

        raw_points: List[Any] = ff_res.get("waveform") or []
        if not raw_points:
            raise EvidenceMissingError(f"Empty freeze-frame waveform for event {event_id}")

        values: List[float] = []
        for idx, pt in enumerate(raw_points):
            if isinstance(pt, dict):
                raw_v = pt.get("val") if "val" in pt else pt.get("raw_value", pt.get("value"))
            elif isinstance(pt, (list, tuple)) and len(pt) > 0:
                raw_v = pt[-1]
            else:
                raw_v = None
            values.append(_require_finite_float(raw_v, f"waveform[{idx}]"))

        waveform = {
            "sample_count": len(values),
            "min": round(min(values), 2),
            "max": round(max(values), 2),
            "trigger": round(trigger_val, 2),
        }

        metrics = self.engine.get_signal_metrics(
            vss_path=trigger_path,
            start_epoch_s=event_time_s - 86400,
            end_epoch_s=event_time_s,
        )
        aggs = metrics.get("aggregates")
        if metrics.get("status") != "SUCCESS" or not isinstance(aggs, dict):
            raise EvidenceMissingError(f"Baseline unavailable for {trigger_path}")

        baseline = {
            "avg": round(_require_finite_float(aggs.get("average"), "baseline.average"), 2),
            "min": round(_require_finite_float(aggs.get("min"), "baseline.min"), 2),
            "max": round(_require_finite_float(aggs.get("max"), "baseline.max"), 2),
            "spread": round(_require_finite_float(aggs.get("spread"), "baseline.spread"), 2),
        }

        return {
            "evidence_tier": "CERTUS_DETERMINISTIC_V1",
            "event": {
                "event_id": int(event["event_id"]),
                "timestamp_utc_s": event_time_s,
                "dtc_code": dtc_code,
                "category": event["event_category"].strip(),
                "severity": event["severity"].strip(),
                "trigger_signal": trigger_path,
                "trigger_value": round(trigger_val, 2),
            },
            "waveform": waveform,
            "baseline": baseline,
        }
