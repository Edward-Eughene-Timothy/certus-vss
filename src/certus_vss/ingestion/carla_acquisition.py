"""Read-only scalar and metadata acquisition from CARLA."""

from __future__ import annotations

import math
import time
from typing import Any

from certus_vss.ingestion.acquisition_catalog import (
    Availability,
    Provenance,
    get_acquisition_signal,
)
from certus_vss.ingestion.acquisition_records import (
    AcquisitionRecord,
    make_acquisition_record,
)
from certus_vss.storage.acquisition_store import save_acquisition_samples

SOURCE = "carla"


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        result = float(value)
    except (ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _vector3(value: Any) -> list[float] | None:
    try:
        result = [
            float(value.x),
            float(value.y),
            float(value.z),
        ]
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None

    if not all(math.isfinite(item) for item in result):
        return None
    return result


def _rotation(value: Any) -> dict[str, float] | None:
    try:
        result = {
            "pitch": float(value.pitch),
            "yaw": float(value.yaw),
            "roll": float(value.roll),
        }
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None

    if not all(math.isfinite(item) for item in result.values()):
        return None
    return result


def _record(
    identifier: str,
    value: Any,
    timestamp_utc_s: float,
) -> AcquisitionRecord:
    spec = get_acquisition_signal(identifier)
    if spec is None:
        raise ValueError(f"Unknown acquisition signal: {identifier}")

    return make_acquisition_record(
        timestamp_utc_s=timestamp_utc_s,
        signal=identifier,
        value=value,
        unit=spec.unit,
        source=SOURCE,
        provenance=spec.provenance,
        availability=Availability.AVAILABLE,
    )


def collect_carla_observations(
    world: Any,
    vehicle: Any,
    *,
    timestamp_utc_s: float | None = None,
    snapshot: Any | None = None,
) -> tuple[list[AcquisitionRecord], dict[str, Any]]:
    """Collect available observations without changing simulator state.

    Returns records and metadata. Pass metadata's simulation_time_s and
    actor_id to save_acquisition_sample when persisting each record.
    """
    timestamp = (
        _finite(timestamp_utc_s)
        if timestamp_utc_s is not None
        else _finite(time.time())
    )
    if timestamp is None or timestamp <= 0:
        raise ValueError("timestamp_utc_s must be a positive finite number")

    if snapshot is None:
        snapshot = world.get_snapshot()

    simulation_time = None
    try:
        simulation_time = _finite(snapshot.timestamp.elapsed_seconds)
    except AttributeError:
        pass

    try:
        actor_id = int(vehicle.id)
        if actor_id < 0:
            actor_id = None
    except (AttributeError, TypeError, ValueError, OverflowError):
        actor_id = None

    observations: list[tuple[str, Any]] = []

    # Motion. Each getter is optional; missing APIs must not stop acquisition.
    def read_vector(method_name: str) -> list[float] | None:
        getter = getattr(vehicle, method_name, None)
        if not callable(getter):
            return None
        try:
            return _vector3(getter())
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return None

    velocity = read_vector("get_velocity")
    if velocity is not None:
        observations.append(("vehicle.velocity", velocity))
        speed = math.sqrt(sum(component * component for component in velocity))
        observations.append(("vehicle.speed", speed * 3.6))

    acceleration = read_vector("get_acceleration")
    if acceleration is not None:
        observations.append(("vehicle.acceleration", acceleration))

    angular_velocity = read_vector("get_angular_velocity")
    if angular_velocity is not None:
        observations.append(("vehicle.angular_velocity", angular_velocity))

    # Position and orientation.
    get_transform = getattr(vehicle, "get_transform", None)
    if callable(get_transform):
        try:
            transform = get_transform()
            position = _vector3(transform.location)
            rotation = _rotation(transform.rotation)

            if position is not None:
                observations.append(("vehicle.position", position))
            if rotation is not None:
                observations.append(("vehicle.rotation", rotation))
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass

    # Read-only control-state observations.
    get_control = getattr(vehicle, "get_control", None)
    if callable(get_control):
        try:
            control = get_control()
            for attribute, identifier in (
                ("throttle", "vehicle.control.throttle"),
                ("brake", "vehicle.control.brake"),
                ("steer", "vehicle.control.steer"),
                ("gear", "vehicle.control.gear"),
                ("hand_brake", "vehicle.control.hand_brake"),
                ("reverse", "vehicle.control.reverse"),
            ):
                if hasattr(control, attribute):
                    observations.append((identifier, getattr(control, attribute)))
        except (AttributeError, RuntimeError):
            pass

    # Read-only physics observations.
    get_physics_control = getattr(vehicle, "get_physics_control", None)
    if callable(get_physics_control):
        try:
            physics = get_physics_control()
            physics_data = {
                "mass": _finite(getattr(physics, "mass", None)),
                "drag_coefficient": _finite(
                    getattr(physics, "drag_coefficient", None)
                ),
                "use_sweep_wheel_collision": getattr(
                    physics, "use_sweep_wheel_collision", None
                ),
            }
            observations.append(("vehicle.physics", physics_data))
        except (AttributeError, RuntimeError):
            pass

    # World metadata.
    get_map = getattr(world, "get_map", None)
    if callable(get_map):
        try:
            map_name = get_map().name
            if isinstance(map_name, str) and map_name.strip():
                observations.append(("environment.map", map_name))
        except (AttributeError, RuntimeError):
            pass

    get_weather = getattr(world, "get_weather", None)
    if callable(get_weather):
        try:
            weather = get_weather()
            weather_fields = (
                "cloudiness",
                "precipitation",
                "precipitation_deposits",
                "wind_intensity",
                "sun_azimuth_angle",
                "sun_altitude_angle",
                "fog_density",
                "wetness",
            )
            weather_data = {}
            for name in weather_fields:
                value = _finite(getattr(weather, name, None))
                if value is not None:
                    weather_data[name] = value

            if weather_data:
                observations.append(("environment.weather", weather_data))
        except (AttributeError, RuntimeError):
            pass

    records = [
        _record(identifier, value, timestamp)
        for identifier, value in observations
    ]

    metadata = {
        "timestamp_utc_s": timestamp,
        "simulation_time_s": simulation_time,
        "actor_id": actor_id,
        "source": SOURCE,
    }
    return records, metadata

def collect_and_store_carla_observations(
    world: Any,
    vehicle: Any,
    *,
    timestamp_utc_s: float | None = None,
    snapshot: Any | None = None,
    db_path: Any = None,
) -> tuple[list[AcquisitionRecord], dict[str, Any], list[int]]:
    """Collect read-only CARLA observations and persist them in one batch."""
    records, metadata = collect_carla_observations(
        world,
        vehicle,
        timestamp_utc_s=timestamp_utc_s,
        snapshot=snapshot,
    )

    if db_path is None:
        inserted_ids = save_acquisition_samples(
            records,
            simulation_time_s=metadata["simulation_time_s"],
            actor_id=metadata["actor_id"],
        )
    else:
        inserted_ids = save_acquisition_samples(
            records,
            db_path,
            simulation_time_s=metadata["simulation_time_s"],
            actor_id=metadata["actor_id"],
        )

    return records, metadata, inserted_ids
