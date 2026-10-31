"""Persist completed one-minute telemetry rollups from the volatile ring buffer.

A minute is considered complete when its end is no later than the latest
accepted frame timestamp. Existing bucket rows are immutable/idempotent.

Drive context is classified independently for each minute using raw telemetry.
The classification thresholds below are implementation heuristics, not
vehicle-specific calibrated thresholds.
"""
from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from certus_vss.common.config import DB_PATH
from certus_vss.ingestion.ring_buffer import SIGNAL_PATHS, TelemetryRingBuffer
from certus_vss.storage.db_engine import SCHEMA_PATH, init_database

UNITS = {
    "Vehicle.Speed": "km/h",
    "Vehicle.Powertrain.TractionBattery.Temperature": "celsius",
    "Vehicle.Powertrain.TractionBattery.Current": "amperes",
    "Vehicle.Powertrain.TractionBattery.StateOfCharge": "percent",
    "Vehicle.Cabin.HVAC.PowerDraw": "watts",
    "Vehicle.Powertrain.ElectricMotor.CoolantTemperature": "celsius",
}

# Drive-context classification heuristics.
STOP_SPEED_KMH = 1.0
CHARGING_CURRENT_A = -5.0
HIGHWAY_AVG_SPEED_KMH = 50.0

# Population variance, in (km/h)^2. A variance of 25 corresponds to a
# standard deviation of 5 km/h.
HIGHWAY_SPEED_VARIANCE_MAX = 25.0

# Number of transitions from moving to stopped within a one-minute bucket.
URBAN_STOP_TRANSITIONS_MIN = 2


def _signal_value(frame: dict[str, Any], path: str) -> float | None:
    """Return a finite numeric signal value, or None if unavailable."""
    value = frame.get("signals", {}).get(path)
    if isinstance(value, dict):
        value = value.get("value")

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None

    result = float(value)
    return result if math.isfinite(result) else None


def _classify_drive_context(frames: list[dict[str, Any]]) -> str:
    """Classify a minute of raw frames into a drive-context label.

    Priority:
    1. CHARGING: a stopped frame has battery current below -5 A.
    2. IDLE: all available speed samples are stopped.
    3. HIGHWAY_CRUISE: mean speed > 50 km/h and population variance <= 25.
    4. URBAN_STOP_GO: moving samples plus at least two moving-to-stopped
       transitions in the minute.
    5. UNKNOWN: insufficient data or no rule matches.
    """
    samples: list[tuple[float, float | None]] = []

    for frame in frames:
        timestamp = float(frame["timestamp"])
        speed = _signal_value(frame, "Vehicle.Speed")
        current = _signal_value(
            frame, "Vehicle.Powertrain.TractionBattery.Current"
        )
        if speed is not None:
            samples.append((timestamp, speed))
        if speed is not None and current is not None:
            if speed <= STOP_SPEED_KMH and current < CHARGING_CURRENT_A:
                return "CHARGING"

    if not samples:
        return "UNKNOWN"

    samples.sort(key=lambda item: item[0])
    speeds = [speed for _, speed in samples]

    if all(speed <= STOP_SPEED_KMH for speed in speeds):
        return "IDLE"

    mean_speed = sum(speeds) / len(speeds)
    variance = sum((speed - mean_speed) ** 2 for speed in speeds) / len(speeds)

    if (
        mean_speed > HIGHWAY_AVG_SPEED_KMH
        and variance <= HIGHWAY_SPEED_VARIANCE_MAX
    ):
        return "HIGHWAY_CRUISE"

    stop_transitions = 0
    was_moving = False

    for speed in speeds:
        is_moving = speed > STOP_SPEED_KMH
        if was_moving and not is_moving:
            stop_transitions += 1
        was_moving = is_moving

    if (
        any(speed > STOP_SPEED_KMH for speed in speeds)
        and stop_transitions >= URBAN_STOP_TRANSITIONS_MIN
    ):
        return "URBAN_STOP_GO"

    return "UNKNOWN"


def _rollup(
    frames: list[dict[str, Any]],
) -> list[tuple[Any, ...]]:
    """Aggregate frames by minute and VSS path, skipping missing values."""
    buckets: dict[tuple[int, str], list[tuple[float, float]]] = defaultdict(list)
    frames_by_minute: dict[int, list[dict[str, Any]]] = defaultdict(list)

    for frame in frames:
        timestamp = float(frame["timestamp"])
        bucket_epoch_m = math.floor(timestamp / 60)
        frames_by_minute[bucket_epoch_m].append(frame)

        for path in SIGNAL_PATHS:
            value = _signal_value(frame, path)
            if value is not None:
                buckets[(bucket_epoch_m, path)].append((timestamp, value))

    contexts = {
        minute: _classify_drive_context(minute_frames)
        for minute, minute_frames in frames_by_minute.items()
    }

    rows: list[tuple[Any, ...]] = []

    for (bucket_epoch_m, path), points in sorted(buckets.items()):
        if not points:
            continue

        points.sort(key=lambda item: item[0])
        values = [point[1] for point in points]

        rows.append((
            bucket_epoch_m,
            path,
            sum(values) / len(values),
            min(values),
            max(values),
            values[-1] - values[0],
            len(values),
            UNITS[path],
            contexts.get(bucket_epoch_m, "UNKNOWN"),
            "certus_vss.ring_buffer",
        ))

    return rows


def flush_buffer_to_rollup(
    buffer: TelemetryRingBuffer,
    db_path: Path = DB_PATH,
    *,
    drive_context: str | None = None,
    provenance: str = "certus_vss.ring_buffer",
    schema_path: Path = SCHEMA_PATH,
) -> int:
    """Write completed minute buckets and return the number of rows inserted.

    If ``drive_context`` is supplied, it overrides automatic classification.
    Otherwise, each minute is classified from its raw telemetry frames.

    Only completed buckets are persisted. Already-persisted (minute, signal)
    keys are skipped so periodic flushing remains idempotent.
    """
    if not isinstance(buffer, TelemetryRingBuffer):
        raise TypeError("buffer must be a TelemetryRingBuffer")

    if drive_context is not None and (
        not isinstance(drive_context, str) or not drive_context.strip()
    ):
        raise ValueError("drive_context must be None or a non-empty string")

    if not isinstance(provenance, str) or not provenance.strip():
        raise ValueError("provenance must be a non-empty string")

    frames = buffer.snapshot()
    if not frames:
        return 0

    latest_timestamp = max(float(frame["timestamp"]) for frame in frames)
    latest_complete_bucket = math.floor(latest_timestamp / 60) - 1

    if latest_complete_bucket < 0 and latest_timestamp < 60:
        return 0

    rows = [
        row for row in _rollup(frames)
        if row[0] <= latest_complete_bucket
    ]
    if not rows:
        return 0

    db_path = Path(db_path)
    init_database(db_path=db_path, schema_path=Path(schema_path))
    inserted = 0

    with sqlite3.connect(db_path, timeout=10.0) as conn:
        conn.execute("PRAGMA foreign_keys = ON")

        for row in rows:
            bucket_epoch_m, path = row[0], row[1]

            exists = conn.execute(
                """SELECT 1 FROM telemetry_rollup_1m
                   WHERE vss_path=? AND bucket_epoch_m=?""",
                (path, bucket_epoch_m),
            ).fetchone()

            if exists:
                continue

            context = drive_context if drive_context is not None else row[8]
            row = row[:8] + (context, provenance)

            conn.execute(
                """INSERT INTO telemetry_rollup_1m
                (bucket_epoch_m, vss_path, val_avg, val_min, val_max, val_delta,
                 sample_count, unit, drive_context, provenance)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                row,
            )
            inserted += 1

        conn.commit()

    return inserted