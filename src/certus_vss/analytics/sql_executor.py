"""
Read-only SQL executor for approved vehicle diagnostic queries.
"""

from typing import Any

from certus_vss.storage.db_engine import get_readonly_connection


MAX_RESULTS = 500

# SQL statements are fixed in code. User input is never interpolated into SQL.
QUERIES = {
    "telemetry": """
        SELECT bucket_epoch_m, vss_path, val_avg, val_min, val_max,
               val_delta, sample_count, unit, drive_context, provenance
        FROM telemetry_rollup_1m
        WHERE bucket_epoch_m >= ?
          AND (? IS NULL OR vss_path = ?)
        ORDER BY bucket_epoch_m DESC, vss_path
        LIMIT ?
    """,
    "diagnostic_events": """
        SELECT event_id, timestamp_utc_s, event_category, dtc_code,
               severity, trigger_signal, trigger_value, context_json,
               provenance
        FROM vehicle_events
        WHERE timestamp_utc_s >= ?
        ORDER BY timestamp_utc_s DESC
        LIMIT ?
    """,
    "safety_events": """
        SELECT event_id, timestamp_utc_s, event_category, dtc_code,
               severity, trigger_signal, trigger_value, context_json,
               provenance
        FROM vehicle_events
        WHERE timestamp_utc_s >= ?
          AND severity IN ('WARNING', 'CRITICAL')
        ORDER BY timestamp_utc_s DESC
        LIMIT ?
    """,
    "freeze_frames": """
        SELECT f.event_id, f.offset_ms, f.vss_path, f.raw_value,
               e.timestamp_utc_s, e.event_category, e.severity,
               e.provenance
        FROM fault_freeze_frames AS f
        JOIN vehicle_events AS e ON e.event_id = f.event_id
        WHERE e.timestamp_utc_s >= ?
        ORDER BY e.timestamp_utc_s DESC, f.event_id, f.offset_ms
        LIMIT ?
    """,
}


def execute_sql(
    intent: dict[str, Any],
    *,
    now_epoch_s: int | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """
    Execute an approved intent against SQLite using a read-only connection.

    `now_epoch_s` can be supplied by tests or callers for deterministic
    time filtering. Results include the selected intent and row count.
    """
    import time

    if not isinstance(intent, dict):
        raise ValueError("intent must be a dictionary")

    query_type = intent.get("intent")
    if query_type not in QUERIES:
        raise ValueError(f"Unsupported intent: {query_type!r}")

    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be a positive integer")

    limit = min(limit, MAX_RESULTS)

    period_seconds = intent.get("period_seconds", 3600)
    if (
        not isinstance(period_seconds, int)
        or isinstance(period_seconds, bool)
        or period_seconds < 0
    ):
        raise ValueError("period_seconds must be a non-negative integer")

    if now_epoch_s is None:
        now_epoch_s = int(time.time())

    if not isinstance(now_epoch_s, int) or isinstance(now_epoch_s, bool):
        raise ValueError("now_epoch_s must be an integer")

    cutoff_epoch_s = now_epoch_s - period_seconds
    signal = intent.get("signal")

    with get_readonly_connection() as conn:
        if query_type == "telemetry":
            rows = conn.execute(
                QUERIES[query_type],
                (cutoff_epoch_s // 60, signal, signal, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                QUERIES[query_type],
                (cutoff_epoch_s, limit),
            ).fetchall()

    records = [dict(row) for row in rows]

    return {
        "intent": query_type,
        "signal": signal if query_type == "telemetry" else None,
        "cutoff_epoch_s": cutoff_epoch_s,
        "row_count": len(records),
        "results": records,
    }
