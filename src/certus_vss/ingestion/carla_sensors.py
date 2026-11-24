
"""Read-only CARLA sensor callback helpers.

Sensor actor creation and listening are deliberately caller-managed.
This module stores payloads through an injected sink and persists only
the returned external reference and frame metadata in SQLite.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from typing import Any

from certus_vss.ingestion.acquisition_catalog import (
    Availability,
    Provenance,
    get_acquisition_signal,
)
from certus_vss.storage.acquisition_store import save_sensor_frame_reference


SENSOR_SIGNAL_MAP = {
    "sensor.camera.rgb": "sensor.rgb",
    "sensor.camera.depth": "sensor.depth",
    "sensor.camera.semantic_segmentation": "sensor.semantic_segmentation",
    "sensor.lidar.ray_cast": "sensor.lidar",
    "sensor.other.radar": "sensor.radar",
}


def _finite_timestamp(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return result


def make_sensor_callback(
    sensor_type: str,
    *,
    payload_sink: Callable[[Any], str],
    timestamp_provider: Callable[[Any], float],
    simulation_time_provider: Callable[[Any], float | None] | None = None,
    actor_id: int | None = None,
    metadata_provider: Callable[[Any], Mapping[str, Any]] | None = None,
    db_path: Any = None,
    on_saved: Callable[[int, str], None] | None = None,
) -> Callable[[Any], None]:
    """Build a callback for one CARLA sensor blueprint.

    payload_sink must persist the raw measurement outside SQLite and
    return a non-empty reference. The callback never spawns or controls
    CARLA actors.
    """
    if sensor_type not in SENSOR_SIGNAL_MAP:
        raise ValueError(f"Unsupported CARLA sensor type: {sensor_type}")

    signal_id = SENSOR_SIGNAL_MAP[sensor_type]
    signal = get_acquisition_signal(signal_id)
    if signal is None:
        raise ValueError(f"Sensor signal is missing from catalog: {signal_id}")

    if not callable(payload_sink):
        raise TypeError("payload_sink must be callable")
    if not callable(timestamp_provider):
        raise TypeError("timestamp_provider must be callable")
    if simulation_time_provider is not None and not callable(
        simulation_time_provider
    ):
        raise TypeError("simulation_time_provider must be callable")
    if metadata_provider is not None and not callable(metadata_provider):
        raise TypeError("metadata_provider must be callable")

    if actor_id is not None and (
        isinstance(actor_id, bool) or not isinstance(actor_id, int) or actor_id < 0
    ):
        raise ValueError("actor_id must be a non-negative integer or None")

    def callback(measurement: Any) -> None:
        timestamp = _finite_timestamp(
            timestamp_provider(measurement), "timestamp_utc_s"
        )

        simulation_time = None
        if simulation_time_provider is not None:
            raw_simulation_time = simulation_time_provider(measurement)
            if raw_simulation_time is not None:
                simulation_time = _finite_timestamp(
                    raw_simulation_time, "simulation_time_s"
                )

        payload_reference = payload_sink(measurement)
        if not isinstance(payload_reference, str) or not payload_reference.strip():
            raise ValueError("payload_sink must return a non-empty reference")

        metadata = (
            metadata_provider(measurement)
            if metadata_provider is not None
            else {}
        )
        if not isinstance(metadata, Mapping):
            raise ValueError("metadata_provider must return a mapping")

        kwargs = {
            "timestamp_utc_s": timestamp,
            "sensor_type": sensor_type,
            "payload_reference": payload_reference,
            "source": "carla",
            "provenance": signal.provenance.value,
            "simulation_time_s": simulation_time,
            "actor_id": actor_id,
            "metadata": dict(metadata),
        }
        if db_path is not None:
            kwargs["db_path"] = db_path

        frame_id = save_sensor_frame_reference(**kwargs)
        if on_saved is not None:
            on_saved(frame_id, payload_reference)

    return callback
