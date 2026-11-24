"""Persistence for broad, read-only CARLA acquisition data."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from certus_vss.storage.db_engine import DB_PATH, get_writer_connection, init_database


def _optional_finite(value: Any, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric or None")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def _optional_actor_id(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("actor_id must be a non-negative integer or None")
    return value

def _json_or_null(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("value must be JSON-serializable") from exc

def initialize_acquisition_storage(db_path: Path = DB_PATH) -> None:
    """Initialize acquisition tables once during application startup."""
    init_database(db_path=Path(db_path))

def save_acquisition_sample(
    record: Any,
    db_path: Path = DB_PATH,
    *,
    simulation_time_s: float | None = None,
    actor_id: int | None = None,
) -> int:
    """Persist one validated AcquisitionRecord; None values remain SQL NULL."""
    from certus_vss.ingestion.acquisition_records import AcquisitionRecord

    if not isinstance(record, AcquisitionRecord):
        raise TypeError("record must be an AcquisitionRecord")

    simulation_time_s = _optional_finite(
        simulation_time_s, "simulation_time_s"
    )
    actor_id = _optional_actor_id(actor_id)

    value_json = _json_or_null(record.value)
    created_at = time.time()

    with get_writer_connection(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO acquisition_samples (
                timestamp_utc_s, simulation_time_s, actor_id, signal,
                value_json, unit, source, provenance, availability,
                created_at_utc_s
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.timestamp_utc_s,
                simulation_time_s,
                actor_id,
                record.signal,
                value_json,
                record.unit,
                record.source,
                record.provenance.value,
                record.availability.value,
                created_at,
            ),
        )
        return int(cursor.lastrowid)


def save_acquisition_event(
    *,
    timestamp_utc_s: float,
    event_type: str,
    event: Mapping[str, Any] | None,
    source: str,
    provenance: str,
    db_path: Path = DB_PATH,
    simulation_time_s: float | None = None,
    actor_id: int | None = None,
) -> int:
    """Persist a general event without requiring a diagnostic trigger value."""
    timestamp = _optional_finite(timestamp_utc_s, "timestamp_utc_s")
    simulation_time_s = _optional_finite(
        simulation_time_s, "simulation_time_s"
    )
    actor_id = _optional_actor_id(actor_id)

    if timestamp is None or timestamp <= 0:
        raise ValueError("timestamp_utc_s must be positive")
    if not isinstance(event_type, str) or not event_type.strip():
        raise ValueError("event_type is required")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("source is required")
    if not isinstance(provenance, str) or not provenance.strip():
        raise ValueError("provenance is required")
    if event is not None and not isinstance(event, Mapping):
        raise ValueError("event must be a mapping or None")

    event_json = _json_or_null(dict(event) if event is not None else None)

    with get_writer_connection(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO acquisition_events (
                timestamp_utc_s, simulation_time_s, actor_id,
                event_type, event_json, source, provenance
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp, simulation_time_s, actor_id, event_type,
                event_json, source, provenance,
            ),
        )
        return int(cursor.lastrowid)


def save_sensor_frame_reference(
    *,
    timestamp_utc_s: float,
    sensor_type: str,
    payload_reference: str,
    source: str,
    provenance: str,
    db_path: Path = DB_PATH,
    simulation_time_s: float | None = None,
    actor_id: int | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> int:
    """Persist sensor metadata and an external payload reference, not the payload."""
    timestamp = _optional_finite(timestamp_utc_s, "timestamp_utc_s")
    simulation_time_s = _optional_finite(
        simulation_time_s, "simulation_time_s"
    )
    actor_id = _optional_actor_id(actor_id)

    if timestamp is None or timestamp <= 0:
        raise ValueError("timestamp_utc_s must be positive")

    for name, value in (
        ("sensor_type", sensor_type),
        ("payload_reference", payload_reference),
        ("source", source),
        ("provenance", provenance),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} is required")

    if metadata is not None and not isinstance(metadata, Mapping):
        raise ValueError("metadata must be a mapping or None")

    metadata_json = _json_or_null(
        dict(metadata) if metadata is not None else None
    )

    with get_writer_connection(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO acquisition_sensor_frames (
                timestamp_utc_s, simulation_time_s, actor_id,
                sensor_type, payload_reference, metadata_json,
                source, provenance
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp, simulation_time_s, actor_id, sensor_type,
                payload_reference, metadata_json, source, provenance,
            ),
        )
        return int(cursor.lastrowid)


def save_acquisition_samples(
    records: list[Any],
    db_path: Path = DB_PATH,
    *,
    simulation_time_s: float | None = None,
    actor_id: int | None = None,
) -> list[int]:
    """Persist multiple acquisition samples using one connection."""
    from certus_vss.ingestion.acquisition_records import AcquisitionRecord

    if not isinstance(records, list):
        raise TypeError("records must be a list")

    if not records:
        return []

    if any(not isinstance(record, AcquisitionRecord) for record in records):
        raise TypeError("every item must be an AcquisitionRecord")

    simulation_time_s = _optional_finite(
        simulation_time_s, "simulation_time_s"
    )
    actor_id = _optional_actor_id(actor_id)

    created_at = time.time()
    rows = []

    for record in records:
        rows.append(
            (
                record.timestamp_utc_s,
                simulation_time_s,
                actor_id,
                record.signal,
                _json_or_null(record.value),
                record.unit,
                record.source,
                record.provenance.value,
                record.availability.value,
                created_at,
            )
        )

    inserted_ids = []

    with get_writer_connection(db_path) as conn:
        for row in rows:
            cursor = conn.execute(
                """
                INSERT INTO acquisition_samples (
                    timestamp_utc_s, simulation_time_s, actor_id, signal,
                    value_json, unit, source, provenance, availability,
                    created_at_utc_s
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                row,
            )
            inserted_ids.append(int(cursor.lastrowid))

    return inserted_ids
