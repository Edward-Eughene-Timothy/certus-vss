"""Read-only normalization and persistence of CARLA events."""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from typing import Any, Callable

from certus_vss.ingestion.acquisition_catalog import (
    Provenance,
    get_acquisition_signal,
)
from certus_vss.storage.acquisition_store import save_acquisition_event


SOURCE = "carla"


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        result = float(value)
    except (ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _vector(value: Any) -> dict[str, float] | None:
    try:
        result = {
            "x": float(value.x),
            "y": float(value.y),
            "z": float(value.z),
        }
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None

    if not all(math.isfinite(component) for component in result.values()):
        return None
    return result


def normalize_collision_event(
    event: Any,
    *,
    timestamp_utc_s: float | None = None,
    simulation_time_s: float | None = None,
    actor_id: int | None = None,
) -> dict[str, Any]:
    """Normalize a CARLA collision callback payload."""
    timestamp = _number(timestamp_utc_s if timestamp_utc_s is not None else time.time())
    if timestamp is None or timestamp <= 0:
        raise ValueError("timestamp_utc_s must be positive and finite")

    simulation_time = _number(simulation_time_s)
    if simulation_time is not None and simulation_time < 0:
        raise ValueError("simulation_time_s cannot be negative")

    if actor_id is not None and (
        isinstance(actor_id, bool) or not isinstance(actor_id, int) or actor_id < 0
    ):
        raise ValueError("actor_id must be a non-negative integer or None")

    impulse = _vector(getattr(event, "normal_impulse", None))
    other_actor = getattr(event, "other_actor", None)
    other_actor_id = getattr(other_actor, "id", None)

    if isinstance(other_actor_id, bool) or not isinstance(other_actor_id, int):
        other_actor_id = None

    intensity = None
    if impulse is not None:
        intensity = math.sqrt(sum(value * value for value in impulse.values()))

    return {
        "timestamp_utc_s": timestamp,
        "simulation_time_s": simulation_time,
        "actor_id": actor_id,
        "event_type": "collision",
        "event": {
            "other_actor_id": other_actor_id,
            "normal_impulse": impulse,
            "impulse_magnitude": intensity,
        },
        "source": SOURCE,
        "provenance": Provenance.CARLA_MEASURED.value,
    }


def normalize_lane_invasion_event(
    event: Any,
    *,
    timestamp_utc_s: float | None = None,
    simulation_time_s: float | None = None,
    actor_id: int | None = None,
) -> dict[str, Any]:
    """Normalize a CARLA lane-invasion callback payload."""
    timestamp = _number(timestamp_utc_s if timestamp_utc_s is not None else time.time())
    if timestamp is None or timestamp <= 0:
        raise ValueError("timestamp_utc_s must be positive and finite")

    simulation_time = _number(simulation_time_s)
    if simulation_time is not None and simulation_time < 0:
        raise ValueError("simulation_time_s cannot be negative")

    if actor_id is not None and (
        isinstance(actor_id, bool) or not isinstance(actor_id, int) or actor_id < 0
    ):
        raise ValueError("actor_id must be a non-negative integer or None")

    markings = getattr(event, "crossed_lane_markings", None)
    marking_types = []

    if markings is not None:
        try:
            for marking in markings:
                marking_type = getattr(marking, "type", None)
                if marking_type is not None:
                    marking_types.append(str(marking_type))
        except TypeError:
            marking_types = []

    return {
        "timestamp_utc_s": timestamp,
        "simulation_time_s": simulation_time,
        "actor_id": actor_id,
        "event_type": "lane_invasion",
        "event": {
            "crossed_lane_markings": marking_types,
        },
        "source": SOURCE,
        "provenance": Provenance.CARLA_MEASURED.value,
    }


def make_event_callback(
    event_kind: str,
    *,
    actor_id: int | None = None,
    simulation_time_provider: Callable[[], float | None] | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    db_path: Any = None,
) -> Callable[[Any], None]:
    """Create an explicit CARLA sensor callback.

    The callback only normalizes and stores event data. Sensor creation and
    attachment are intentionally left to the caller.
    """
    if event_kind == "collision":
        normalizer = normalize_collision_event
        catalog_id = "event.collision"
    elif event_kind == "lane_invasion":
        normalizer = normalize_lane_invasion_event
        catalog_id = "event.lane_invasion"
    else:
        raise ValueError(f"Unsupported CARLA event kind: {event_kind}")

    catalog_entry = get_acquisition_signal(catalog_id)
    if catalog_entry is None:
        raise ValueError(f"Missing acquisition catalog entry: {catalog_id}")

    def callback(event: Any) -> None:
        simulation_time = (
            simulation_time_provider()
            if simulation_time_provider is not None
            else None
        )
        normalized = normalizer(
            event,
            simulation_time_s=simulation_time,
            actor_id=actor_id,
        )

        save_kwargs = {
            "timestamp_utc_s": normalized["timestamp_utc_s"],
            "event_type": normalized["event_type"],
            "event": normalized["event"],
            "source": normalized["source"],
            "provenance": normalized["provenance"],
        }
        if db_path is not None:
            save_kwargs["db_path"] = db_path

        save_acquisition_event(
            **save_kwargs,
            simulation_time_s=normalized["simulation_time_s"],
            actor_id=normalized["actor_id"],
        )

        if on_event is not None:
            on_event(normalized)

    return callback
