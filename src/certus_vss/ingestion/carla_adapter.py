"""Optional read-only telemetry adapter for a running CARLA simulator.

CARLA exposes vehicle kinematics but does not provide battery/HVAC telemetry
out of the box. Those catalog entries are emitted as null until a project
specific sensor/vehicle model supplies them. This adapter never commands the
vehicle or changes its controls.
"""
from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from typing import Any

from .ring_buffer import SIGNAL_PATHS, TelemetryRingBuffer


def _xyz(vector: Any) -> tuple[float, float, float]:
    return float(vector.x), float(vector.y), float(vector.z)


def make_carla_frame(vehicle: Any, timestamp: float | None = None,
                     sequence: int = 0,
                     extra_signals: Mapping[str, float | None] | None = None) -> dict[str, Any]:
    """Convert a CARLA vehicle actor's velocity into the configured VSS frame.

    CARLA velocity is in metres/second; Vehicle.Speed is normalized to km/h.
    Optional extra signals can fill fields provided by a custom vehicle model.
    """
    vx, vy, vz = _xyz(vehicle.get_velocity())
    speed_kmh = math.sqrt(vx * vx + vy * vy + vz * vz) * 3.6
    signals: dict[str, float | None] = {path: None for path in SIGNAL_PATHS}
    if "Vehicle.Speed" not in signals:
        raise ValueError("Vehicle.Speed is missing from the configured VSS catalog")
    signals["Vehicle.Speed"] = speed_kmh
    if extra_signals:
        unknown = set(extra_signals) - set(SIGNAL_PATHS)
        if unknown:
            raise ValueError(f"extra_signals contains unknown VSS paths: {sorted(unknown)}")
        signals.update(extra_signals)
    frame = {
        "timestamp": time.time() if timestamp is None else float(timestamp),
        "sequence": int(sequence),
        "source": "carla",
        "signals": signals,
    }
    if not math.isfinite(frame["timestamp"]):
        raise ValueError("timestamp must be finite")
    return frame


def iter_carla_frames(vehicle: Any, sample_hz: float = 10.0,
                      max_samples: int | None = None,
                      stop_event: threading.Event | None = None,
                      clock: Callable[[], float] = time.time,
                      sleeper: Callable[[float], Any] = time.sleep,
                      extra_signal_provider: Callable[[Any], Mapping[str, float | None]] | None = None
                      ) -> Iterator[dict[str, Any]]:
    """Poll a CARLA actor at a target rate and yield normalized frames.

    This function accepts a CARLA actor object, so CARLA itself is an optional
    runtime dependency and the sampling logic can be tested with a fake actor.
    """
    if not math.isfinite(sample_hz) or sample_hz <= 0:
        raise ValueError("sample_hz must be a positive finite number")
    if max_samples is not None and max_samples < 0:
        raise ValueError("max_samples must be non-negative or None")
    stop_event = stop_event or threading.Event()
    period = 1.0 / sample_hz
    deadline = time.monotonic()
    sequence = 0
    while max_samples is None or sequence < max_samples:
        if stop_event.is_set():
            break
        extras = extra_signal_provider(vehicle) if extra_signal_provider else None
        yield make_carla_frame(vehicle, timestamp=clock(), sequence=sequence, extra_signals=extras)
        sequence += 1
        deadline += period
        delay = deadline - time.monotonic()
        if delay > 0:
            sleeper(delay)


def listen_carla(vehicle: Any, buffer: TelemetryRingBuffer,
                 sample_hz: float = 10.0, max_samples: int | None = None,
                 stop_event: threading.Event | None = None,
                 extra_signal_provider: Callable[[Any], Mapping[str, float | None]] | None = None) -> int:
    """Sample a CARLA actor into the ring buffer; return accepted frame count."""
    accepted = 0
    for frame in iter_carla_frames(vehicle, sample_hz, max_samples, stop_event,
                                   extra_signal_provider=extra_signal_provider):
        if buffer.append(frame):
            accepted += 1
    return accepted


def attach_collision_sensor(world: Any, vehicle: Any,
                            on_collision: Callable[[dict[str, Any]], None]) -> Any:
    """Attach CARLA's collision sensor and report collision events read-only.

    The caller owns the returned sensor and should call ``sensor.stop()`` and
    ``sensor.destroy()`` during teardown. Event records are callback payloads,
    not telemetry frames; wire them to the project's freeze-frame/event store.
    """
    if not callable(on_collision):
        raise ValueError("on_collision callback is required")
    library = world.get_blueprint_library()
    blueprint = library.find("sensor.other.collision")
    sensor = world.spawn_actor(blueprint, _identity_transform(world), attach_to=vehicle)

    def _handle(event: Any) -> None:
        impulse = getattr(event, "normal_impulse", None)
        intensity = None
        if impulse is not None:
            ix, iy, iz = _xyz(impulse)
            intensity = math.sqrt(ix * ix + iy * iy + iz * iz)
        on_collision({
            "timestamp": time.time(),
            "event_type": "collision",
            "source": "carla",
            "other_actor_id": getattr(getattr(event, "other_actor", None), "id", None),
            "intensity": intensity,
        })

    sensor.listen(_handle)
    return sensor


def _identity_transform(world: Any) -> Any:
    """Return a CARLA identity transform without importing CARLA globally."""
    try:
        import carla
        return carla.Transform()
    except ImportError as exc:  # pragma: no cover - CARLA runtime only
        raise RuntimeError("CARLA Python API is required to attach a collision sensor") from exc
