"""
query_engine.py: Deterministic SQL analytics engine for CertusVSS.
Executes parameterized bytecode queries on read-only SQLite handles.
Generates structured JSON Evidence Blocks for UI cards and SLM context.
"""

import json
import time
from typing import Any, Dict, List, Optional
from certus_vss.storage.db_engine import get_readonly_connection


class DeterministicQueryEngine:
    """Read-only analytics engine with sub-15ms execution targets."""

    def __init__(self) -> None:
        pass

    def get_signal_metrics(
        self,
        vss_path: str,
        start_epoch_s: int,
        end_epoch_s: int,
        context_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Calculates mathematical aggregates for a signal over a time window.
        Uses minute-epoch indexing on telemetry_rollup_1m.
        """
        t_start = time.perf_counter()
        start_m = start_epoch_s // 60
        end_m = end_epoch_s // 60

        query = """
            SELECT 
                COUNT(*) AS interval_count,
                ROUND(AVG(val_avg), 2) AS overall_avg,
                ROUND(MIN(val_min), 2) AS overall_min,
                ROUND(MAX(val_max), 2) AS overall_max,
                ROUND(MAX(val_max) - MIN(val_min), 2) AS overall_spread,
                unit
            FROM telemetry_rollup_1m
            WHERE vss_path = ?
              AND bucket_epoch_m BETWEEN ? AND ?
        """
        params: List[Any] = [vss_path, start_m, end_m]

        if context_filter:
            query += " AND drive_context = ?"
            params.append(context_filter)

        with get_readonly_connection() as conn:
            row = conn.execute(query, params).fetchone()

        elapsed_ms = (time.perf_counter() - t_start) * 1000.0

        if not row or row["interval_count"] == 0:
            return {
                "status": "NO_DATA",
                "execution_ms": round(elapsed_ms, 3),
                "vss_path": vss_path,
            }

        return {
            "status": "SUCCESS",
            "vss_path": vss_path,
            "unit": row["unit"],
            "window_minutes": row["interval_count"],
            "aggregates": {
                "average": row["overall_avg"],
                "min": row["overall_min"],
                "max": row["overall_max"],
                "spread": row["overall_spread"],
            },
            "context_filter": context_filter,
            "execution_ms": round(elapsed_ms, 3),
        }

    def get_recent_diagnostic_events(self, limit: int = 5) -> Dict[str, Any]:
        """Extracts the most recent DTC diagnostic events and their contexts."""
        t_start = time.perf_counter()

        query = """
            SELECT 
                event_id,
                timestamp_utc_s,
                event_category,
                dtc_code,
                severity,
                trigger_signal,
                trigger_value,
                context_json
            FROM vehicle_events
            ORDER BY timestamp_utc_s DESC
            LIMIT ?;
        """

        events = []
        with get_readonly_connection() as conn:
            rows = conn.execute(query, (limit,)).fetchall()
            for r in rows:
                ev = dict(r)
                ev["context_json"] = json.loads(ev["context_json"])
                events.append(ev)

        elapsed_ms = (time.perf_counter() - t_start) * 1000.0

        return {
            "status": "SUCCESS",
            "event_count": len(events),
            "events": events,
            "execution_ms": round(elapsed_ms, 3),
        }

    def get_freeze_frame_waveform(self, event_id: int, vss_path: str) -> Dict[str, Any]:
        """
        Fetches the high-frequency 10 Hz waveform points surrounding a fault event.
        """
        t_start = time.perf_counter()

        query = """
            SELECT offset_ms, raw_value
            FROM fault_freeze_frames
            WHERE event_id = ? AND vss_path = ?
            ORDER BY offset_ms ASC;
        """

        with get_readonly_connection() as conn:
            rows = conn.execute(query, (event_id, vss_path)).fetchall()

        points = [{"offset_ms": r["offset_ms"], "val": r["raw_value"]} for r in rows]
        elapsed_ms = (time.perf_counter() - t_start) * 1000.0

        return {
            "status": "SUCCESS",
            "event_id": event_id,
            "vss_path": vss_path,
            "sample_count": len(points),
            "waveform": points,
            "execution_ms": round(elapsed_ms, 3),
        }

    def build_evidence_block(self, query_type: str, **kwargs: Any) -> str:
        """
        Builds the immutable JSON evidence block provided to the UI & SLM.
        """
        if query_type == "signal_trend":
            result = self.get_signal_metrics(
                vss_path=kwargs["vss_path"],
                start_epoch_s=kwargs["start_epoch_s"],
                end_epoch_s=kwargs["end_epoch_s"],
                context_filter=kwargs.get("context_filter"),
            )
        elif query_type == "diagnostics":
            result = self.get_recent_diagnostic_events(
                limit=kwargs.get("limit", 5)
            )
        elif query_type == "fault_analysis":
            ev = self.get_recent_diagnostic_events(limit=1)
            event_id = ev["events"][0]["event_id"] if ev["events"] else 1
            result = {
                "event_metadata": ev["events"][0] if ev["events"] else None,
                "freeze_frame": self.get_freeze_frame_waveform(
                    event_id=event_id,
                    vss_path=kwargs.get(
                        "vss_path", "Vehicle.Powertrain.TractionBattery.Temperature"
                    ),
                ),
            }
        else:
            raise ValueError(f"Unknown query_type: {query_type}")

        return json.dumps(result, indent=2)


if __name__ == "__main__":
    engine = DeterministicQueryEngine()
    now_s = int(time.time())
    seven_days_ago_s = now_s - (7 * 86400)

    print("=" * 60)
    print("1. BENCHMARK: 7-Day Battery Temperature Rollup Query")
    print("=" * 60)
    res1 = engine.get_signal_metrics(
        vss_path="Vehicle.Powertrain.TractionBattery.Temperature",
        start_epoch_s=seven_days_ago_s,
        end_epoch_s=now_s,
    )
    print(json.dumps(res1, indent=2))
    print(f"--> Latency: {res1['execution_ms']} ms (Target: < 15.0 ms)")

    print("\n" + "=" * 60)
    print("2. BENCHMARK: Diagnostic Events Query")
    print("=" * 60)
    res2 = engine.get_recent_diagnostic_events(limit=2)
    print(json.dumps(res2, indent=2))
    print(f"--> Latency: {res2['execution_ms']} ms (Target: < 15.0 ms)")

    print("\n" + "=" * 60)
    print("3. BENCHMARK: Event #1 10Hz Freeze-Frame Waveform Extraction")
    print("=" * 60)
    latest_event_id = res2['events'][0]['event_id'] if res2['events'] else 1
    # Find thermal warning event if present
    for ev in res2['events']:
        if ev['event_category'] == 'THERMAL_WARN':
            latest_event_id = ev['event_id']
            break

    res3 = engine.get_freeze_frame_waveform(
        event_id=latest_event_id,
        vss_path="Vehicle.Powertrain.TractionBattery.Temperature",
    )
    print(f"Waveform points fetched: {res3['sample_count']}")
    print(f"--> Latency: {res3['execution_ms']} ms (Target: < 15.0 ms)")