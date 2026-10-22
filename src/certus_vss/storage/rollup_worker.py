"""Persist completed one-minute telemetry rollups from the volatile ring buffer.

A minute is considered complete when its end is no later than the latest
accepted frame timestamp. Existing bucket rows are immutable/idempotent: a
repeated flush will not double-count a bucket already written to SQLite.
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


def _rollup(frames: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    """Aggregate frames by minute and VSS path, skipping missing values."""
    buckets: dict[tuple[int, str], list[tuple[float, float]]] = defaultdict(list)
    for frame in frames:
        timestamp = float(frame["timestamp"])
        bucket_epoch_m = math.floor(timestamp / 60)
        signals = frame.get("signals", {})
        for path in SIGNAL_PATHS:
            value = signals.get(path)
            if value is None:
                continue
            if isinstance(value, dict):
                value = value.get("value")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            value = float(value)
            if math.isfinite(value):
                buckets[(bucket_epoch_m, path)].append((timestamp, value))

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
            "unknown",
            "certus_vss.ring_buffer",
        ))
    return rows


def flush_buffer_to_rollup(
    buffer: TelemetryRingBuffer,
    db_path: Path = DB_PATH,
    *,
    drive_context: str = "unknown",
    provenance: str = "certus_vss.ring_buffer",
    schema_path: Path = SCHEMA_PATH,
) -> int:
    """Write completed minute buckets and return the number of rows inserted.

    ``drive_context`` and ``provenance`` are explicit metadata fields from the
    existing Track A schema. Only buckets that have ended by the latest frame
    are persisted. Already-persisted (minute, signal) keys are skipped so a
    periodic flush is safe to repeat.
    """
    if not isinstance(buffer, TelemetryRingBuffer):
        raise TypeError("buffer must be a TelemetryRingBuffer")
    if not drive_context or not provenance:
        raise ValueError("drive_context and provenance must be non-empty")

    frames = buffer.snapshot()
    if not frames:
        return 0
    latest_timestamp = max(float(frame["timestamp"]) for frame in frames)
    latest_complete_bucket = math.floor(latest_timestamp / 60) - 1
    if latest_complete_bucket < 0:
        # Epoch-zero test/simulation timestamps may not have completed a bucket.
        if latest_timestamp < 60:
            return 0

    rows = [row for row in _rollup(frames) if row[0] <= latest_complete_bucket]
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
                "SELECT 1 FROM telemetry_rollup_1m WHERE vss_path=? AND bucket_epoch_m=?",
                (path, bucket_epoch_m),
            ).fetchone()
            if exists:
                continue
            row = row[:8] + (drive_context, provenance)
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
