"""10 Hz synthetic telemetry stream used to exercise the ingestion pipeline.

This generator is a development/test source, not a substitute for CARLA or
real CAN input. It emits the same normalized frame contract as other adapters.
"""
from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from typing import Any

from .ring_buffer import SIGNAL_PATHS, TelemetryRingBuffer


def iter_simulated_frames(sample_hz: float = 10.0,
                          max_samples: int | None = None,
                          start_epoch: float | None = None) -> Iterator[dict[str, Any]]:
    """Yield changing six-signal frames at the requested rate.

    The iterator does not sleep, making it useful for fast deterministic tests.
    ``start_epoch`` defaults to current UTC epoch seconds.
    """
    if not math.isfinite(sample_hz) or sample_hz <= 0:
        raise ValueError("sample_hz must be a positive finite number")
    if max_samples is not None and max_samples < 0:
        raise ValueError("max_samples must be non-negative or None")
    epoch = time.time() if start_epoch is None else float(start_epoch)
    period = 1.0 / sample_hz
    i = 0
    while max_samples is None or i < max_samples:
        phase = i * period
        speed = max(0.0, 42.0 + 12.0 * math.sin(phase / 18.0))
        soc = max(0.0, 78.0 - i * period * 0.002)
        battery_temp = 29.0 + 3.0 * math.sin(phase / 90.0)
        current = -18.0 - 7.0 * abs(math.sin(phase / 12.0))
        hvac_power = 650.0 + 150.0 * (1.0 + math.sin(phase / 30.0))
        coolant_temp = 34.0 + 2.5 * math.sin(phase / 75.0)
        values = (speed, battery_temp, current, soc, hvac_power, coolant_temp)
        yield {
            "timestamp": epoch + i * period,
            "sequence": i,
            "source": "synthetic_simulator",
            "signals": dict(zip(SIGNAL_PATHS, values)),
        }
        i += 1


def start_simulation(buffer: TelemetryRingBuffer,
                     stop_event: threading.Event | None = None,
                     sample_hz: float = 10.0,
                     max_samples: int | None = None,
                     on_frame: Callable[[dict[str, Any]], None] | None = None) -> int:
    """Stream frames into ``buffer`` at real-time rate until stopped.

    Returns the number of accepted frames. Use ``max_samples`` for bounded
    demos/tests; otherwise set ``stop_event`` from another thread to stop.
    """
    if sample_hz <= 0 or not math.isfinite(sample_hz):
        raise ValueError("sample_hz must be a positive finite number")
    if max_samples is not None and max_samples < 0:
        raise ValueError("max_samples must be non-negative or None")
    stop_event = stop_event or threading.Event()
    period = 1.0 / sample_hz
    start = time.time()
    deadline = time.monotonic()
    accepted = 0
    for frame in iter_simulated_frames(sample_hz=sample_hz, max_samples=max_samples, start_epoch=start):
        if stop_event.is_set():
            break
        if not buffer.append(frame):
            continue
        accepted += 1
        if on_frame is not None:
            on_frame(frame)
        deadline += period
        delay = deadline - time.monotonic()
        if delay > 0:
            stop_event.wait(delay)
    return accepted
