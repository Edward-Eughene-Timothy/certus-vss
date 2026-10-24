"""Persist safety events and telemetry freeze frames from the volatile ring buffer.

The event row is committed immediately. Freeze-frame capture can be called again
until the post-event window is complete; inserts are idempotent even though the
Track A schema does not declare a freeze-frame primary key.
"""
from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any, Mapping

from certus_vss.common.config import DB_PATH
from certus_vss.ingestion.ring_buffer import SIGNAL_PATHS, TelemetryRingBuffer
from certus_vss.storage.db_engine import SCHEMA_PATH, init_database
from certus_vss.storage.capture_scheduler import PostEventCaptureScheduler

# Shared background scheduler for post-event freeze-frame capture.
_POST_EVENT_SCHEDULER = PostEventCaptureScheduler()

def _finite_number(value: Any, field: str, *, default: float | None = None) -> float:
    if value is None and default is not None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be a finite number")
    return result


def _window_rows(buffer: TelemetryRingBuffer, event_timestamp: float,
                 pre_seconds: float, post_seconds: float) -> list[tuple[int, int, str, float]]:
    frames = buffer.get_window(event_timestamp - pre_seconds, event_timestamp + post_seconds)
    rows: list[tuple[int, int, str, float]] = []
    for frame in frames:
        offset_ms = round((float(frame["timestamp"]) - event_timestamp) * 1000)
        for path in SIGNAL_PATHS:
            value = frame.get("signals", {}).get(path)
            if isinstance(value, Mapping):
                value = value.get("value")
            if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            value = float(value)
            if math.isfinite(value):
                rows.append((0, offset_ms, path, value))
    return rows


def record_safety_event(
    buffer: TelemetryRingBuffer,
    event: Mapping[str, Any],
    db_path: Path = DB_PATH,
    *,
    pre_seconds: float = 120.0,
    post_seconds: float = 120.0,
    drive_context: str = "unknown",
    provenance: str = "certus_vss.event_capture",
    schema_path: Path = SCHEMA_PATH,
) -> int:
    """Persist an event and currently available freeze-frame samples.

    Call :func:`capture_event_window` again after the post-event interval to add
    later samples. The ring buffer must still retain the requested window.
    Returns the SQLite event_id.
    """
    if not isinstance(buffer, TelemetryRingBuffer):
        raise TypeError("buffer must be a TelemetryRingBuffer")
    if not isinstance(event, Mapping):
        raise ValueError("event must be a mapping")
    if not math.isfinite(pre_seconds) or pre_seconds < 0:
        raise ValueError("pre_seconds must be finite and non-negative")
    if not math.isfinite(post_seconds) or post_seconds < 0:
        raise ValueError("post_seconds must be finite and non-negative")
    timestamp = _finite_number(event.get("timestamp"), "timestamp")
    category = event.get("event_category", event.get("event_type", "safety_event"))
    severity = event.get("severity", "warning")
    trigger_signal = event.get("trigger_signal", "Vehicle.Collision")
    trigger_value = _finite_number(event.get("trigger_value", event.get("intensity")), "trigger_value", default=0.0)
    if not all(isinstance(v, str) and v.strip() for v in (category, severity, trigger_signal, drive_context, provenance)):
        raise ValueError("event category, severity, trigger signal, context, and provenance must be non-empty strings")
    context = dict(event.get("context", {}))
    for key in ("source", "other_actor_id", "intensity"):
        if key in event and key not in context:
            context[key] = event[key]
    context["drive_context"] = drive_context
    context["freeze_frame_window_seconds"] = {"before": pre_seconds, "after": post_seconds}
    context_json = json.dumps(context, sort_keys=True, separators=(",", ":"))

    db_path = Path(db_path)
    init_database(db_path=db_path, schema_path=Path(schema_path))
    with sqlite3.connect(db_path, timeout=10.0) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        cursor = conn.execute(
            """INSERT INTO vehicle_events
            (timestamp_utc_s, event_category, dtc_code, severity, trigger_signal,
             trigger_value, context_json, provenance)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (int(timestamp), category, event.get("dtc_code"), severity,
             trigger_signal, trigger_value, context_json, provenance),
        )
        event_id = int(cursor.lastrowid)
        _insert_freeze_frames(conn, event_id, buffer, timestamp, pre_seconds, post_seconds)
        conn.commit()

    # Schedule completion after the event is committed.
    # The timer runs in the background, so ingestion is not blocked.
    _POST_EVENT_SCHEDULER.schedule(
        event_id,
        buffer,
        timestamp,
        db_path,
        pre_seconds=pre_seconds,
        post_seconds=post_seconds,
    )

    return event_id

def capture_event_window(
    event_id: int,
    buffer: TelemetryRingBuffer,
    event_timestamp: float,
    db_path: Path = DB_PATH,
    *,
    pre_seconds: float = 120.0,
    post_seconds: float = 120.0,
) -> int:
    """Add any currently retained samples in an event's window, without duplicates."""
    if isinstance(event_id, bool) or not isinstance(event_id, int) or event_id <= 0:
        raise ValueError("event_id must be a positive integer")
    timestamp = _finite_number(event_timestamp, "event_timestamp")
    if not math.isfinite(pre_seconds) or pre_seconds < 0 or not math.isfinite(post_seconds) or post_seconds < 0:
        raise ValueError("window sizes must be finite and non-negative")
    db_path = Path(db_path)
    init_database(db_path=db_path)
    with sqlite3.connect(db_path, timeout=10.0) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        exists = conn.execute("SELECT 1 FROM vehicle_events WHERE event_id=?", (event_id,)).fetchone()
        if not exists:
            raise ValueError(f"unknown event_id: {event_id}")
        inserted = _insert_freeze_frames(conn, event_id, buffer, timestamp, pre_seconds, post_seconds)
        conn.commit()
        return inserted


def _insert_freeze_frames(conn: sqlite3.Connection, event_id: int,
                          buffer: TelemetryRingBuffer, timestamp: float,
                          pre_seconds: float, post_seconds: float) -> int:
    rows = _window_rows(buffer, timestamp, pre_seconds, post_seconds)
    inserted = 0
    for _, offset_ms, path, value in rows:
        cursor = conn.execute(
            """INSERT INTO fault_freeze_frames (event_id, offset_ms, vss_path, raw_value)
            SELECT ?, ?, ?, ?
            WHERE NOT EXISTS (
                SELECT 1 FROM fault_freeze_frames
                WHERE event_id=? AND offset_ms=? AND vss_path=?
            )""",
            (event_id, offset_ms, path, value, event_id, offset_ms, path),
        )
        inserted += cursor.rowcount
    return inserted
