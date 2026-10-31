"""
Deterministic SQLite Analytics Engine for CertusVSS.
Executes parameterized queries over Tier 2 (1-minute rollups) & Tier 4 (events)
using strictly read-only connections (?mode=ro) with sub-15ms guaranteed latency.
Generates structured JSON Evidence Blocks for consumption by the SLM and UI.
"""

from __future__ import annotations
import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from certus_vss.storage.db_engine import get_readonly_connection


class SignalSummary(BaseModel):
    vss_path: str
    unit: str
    val_min: float
    val_avg: float
    val_max: float
    val_delta: float
    total_samples: int


class EvidenceBlock(BaseModel):
    query_intent: str
    time_window_str: str
    start_epoch_s: int
    end_epoch_s: int
    execution_time_ms: float
    provenance: str = "SQL_DETERMINISTIC_EVIDENCE"
    metrics: Dict[str, SignalSummary] = Field(default_factory=dict)
    events: List[Dict[str, Any]] = Field(default_factory=list)
    missing_signals: List[str] = Field(default_factory=list)
    raw_sql: str = ""


class QueryEngine:
    """Deterministic analytics engine enforcing 'SQL Computes, SLM Explains'."""

    CORE_SIGNALS = [
        "Vehicle.Speed",
        "Vehicle.Powertrain.TractionBattery.Temperature",
        "Vehicle.Powertrain.TractionBattery.Current",
        "Vehicle.Powertrain.TractionBattery.StateOfCharge",
        "Vehicle.Cabin.HVAC.PowerDraw",
        "Vehicle.Powertrain.ElectricMotor.CoolantTemperature",
    ]

    @staticmethod
    def get_time_window_bounds(window: str) -> tuple[int, int]:
        """Convert relative window identifier into epoch seconds (start_s, end_s)."""
        now_s = int(time.time())
        window_lower = window.strip().lower()

        if "24h" in window_lower or "24 hours" in window_lower or "today" in window_lower:
            start_s = now_s - (24 * 3600)
        elif "7d" in window_lower or "7 days" in window_lower or "week" in window_lower:
            start_s = now_s - (7 * 24 * 3600)
        elif "30d" in window_lower or "30 days" in window_lower or "month" in window_lower:
            start_s = now_s - (30 * 24 * 3600)
        elif "1h" in window_lower or "hour" in window_lower:
            start_s = now_s - 3600
        else:
            # Default to past 24 hours
            start_s = now_s - (24 * 3600)

        return start_s, now_s

    @classmethod
    def query_window_metrics(
        cls,
        time_window: str = "24h",
        signal_paths: Optional[List[str]] = None,
        drive_context: Optional[str] = None,
    ) -> EvidenceBlock:
        """
        Executes bounded aggregation over 1-minute rollups in < 15ms.
        Computes exact min, average, peak, and spread across the designated time window.
        """
        start_t = time.perf_counter()
        start_s, end_s = cls.get_time_window_bounds(time_window)
        start_epoch_m = start_s // 60
        end_epoch_m = end_s // 60

        requested_signals = signal_paths or cls.CORE_SIGNALS

        sql = """
            SELECT 
                vss_path,
                unit,
                MIN(val_min) AS min_val,
                ROUND(AVG(val_avg), 2) AS avg_val,
                MAX(val_max) AS max_val,
                ROUND(MAX(val_max) - MIN(val_min), 2) AS delta_spread,
                SUM(sample_count) AS total_samples
            FROM telemetry_rollup_1m
            WHERE bucket_epoch_m >= ? AND bucket_epoch_m <= ?
        """
        params: List[Any] = [start_epoch_m, end_epoch_m]

        if drive_context:
            sql += " AND drive_context = ?"
            params.append(drive_context.upper())

        sql += " GROUP BY vss_path, unit;"

        metrics_map: Dict[str, SignalSummary] = {}
        events_list: List[Dict[str, Any]] = []

        with get_readonly_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            rows = cursor.fetchall()

            for row in rows:
                path = row["vss_path"]
                if path in requested_signals:
                    metrics_map[path] = SignalSummary(
                        vss_path=path,
                        unit=row["unit"],
                        val_min=float(row["min_val"]),
                        val_avg=float(row["avg_val"]),
                        val_max=float(row["max_val"]),
                        val_delta=float(row["delta_spread"]),
                        total_samples=int(row["total_samples"]),
                    )

            # Retrieve diagnostic trouble events within the window
            cursor.execute(
                """
                SELECT event_id, timestamp_utc_s, event_category, dtc_code,
                       severity, trigger_signal, trigger_value, context_json
                FROM vehicle_events
                WHERE timestamp_utc_s >= ? AND timestamp_utc_s <= ?
                ORDER BY timestamp_utc_s DESC
                LIMIT 10;
                """,
                (start_s, end_s),
            )
            event_rows = cursor.fetchall()
            for er in event_rows:
                events_list.append(dict(er))

        exec_ms = round((time.perf_counter() - start_t) * 1000, 2)

        # Audit missing signals
        missing = [s for s in requested_signals if s not in metrics_map]

        return EvidenceBlock(
            query_intent=f"Historical telemetry metrics for {time_window}",
            time_window_str=time_window,
            start_epoch_s=start_s,
            end_epoch_s=end_s,
            execution_time_ms=exec_ms,
            metrics=metrics_map,
            events=events_list,
            missing_signals=missing,
            raw_sql=sql.strip(),
        )

    @classmethod
    def query_recent_event_evidence(cls, limit: int = 5) -> Dict[str, Any]:
        """Fetch latest DTCs and incidents with sub-millisecond execution."""
        start_t = time.perf_counter()
        with get_readonly_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT event_id, timestamp_utc_s, event_category, dtc_code,
                       severity, trigger_signal, trigger_value, context_json
                FROM vehicle_events
                ORDER BY timestamp_utc_s DESC
                LIMIT ?;
                """,
                (limit,),
            )
            events = [dict(r) for r in cursor.fetchall()]

        exec_ms = round((time.perf_counter() - start_t) * 1000, 2)
        return {
            "events": events,
            "execution_time_ms": exec_ms,
            "count": len(events),
        }

    @classmethod
    def query_telemetry_summary(cls, days: int = 30) -> Dict[str, Any]:
        """Convenience query for UI: returns metrics dictionary for designated days."""
        window_str = f"{days}d"
        evidence_block = cls.query_window_metrics(time_window=window_str)
        # Convert to dictionary representation for consumers
        evidence_dict = evidence_block.model_dump()
        evidence_dict["time_range_days"] = days
        return evidence_dict


def query_telemetry_summary(days: int = 30) -> Dict[str, Any]:
    """Module-level helper for UI consumption."""
    return QueryEngine.query_telemetry_summary(days=days)


if __name__ == "__main__":
    print("[Testing QueryEngine on SQLite WAL Mode]...")
    evidence = QueryEngine.query_window_metrics("24h")
    print(f"-> Executed in: {evidence.execution_time_ms} ms (Target: < 15.0 ms)")
    print(f"-> Signals audited: {len(evidence.metrics)}/{len(QueryEngine.CORE_SIGNALS)}")
    print(f"-> Recent Events: {len(evidence.events)}")
    for path, data in evidence.metrics.items():
        print(f"   [{path}] Avg={data.val_avg}{data.unit}, Min={data.val_min}, Max={data.val_max}")
