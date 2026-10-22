"""Optional read-only SocketCAN/vcan ingestion adapter.

The adapter only listens for frames. DBC decoding is intentionally injected by
callers because the repository does not yet include a vehicle-specific DBC.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from typing import Any


def iter_can_frames(channel: str = "vcan0", bustype: str = "socketcan",
                    decode: Callable[[Any], Mapping[str, Any]] | None = None,
                    stop_event: Any = None) -> Iterator[dict[str, Any]]:
    """Yield decoded VSS frames from a CAN interface in listen-only mode.

    Requires python-can and a caller-supplied decoder that maps a CAN message to
    ``{"timestamp": epoch_seconds, "signals": {vss_path: numeric_value}}``.
    This code does not transmit CAN messages and is not a hardware data diode.
    """
    try:
        import can
    except ImportError as exc:  # pragma: no cover - depends on optional system package
        raise RuntimeError("python-can is required for CAN ingestion") from exc
    if decode is None:
        raise ValueError("a vehicle-specific DBC/frame decoder must be supplied")
    bus = can.Bus(channel=channel, interface=bustype, receive_own_messages=False)
    try:
        while stop_event is None or not stop_event.is_set():
            message = bus.recv(timeout=0.25)
            if message is None:
                continue
            decoded = decode(message)
            if not isinstance(decoded, Mapping) or "timestamp" not in decoded or "signals" not in decoded:
                continue
            yield {
                "timestamp": decoded["timestamp"],
                "sequence": getattr(message, "sequence", 0),
                "source": f"{bustype}:{channel}",
                "signals": dict(decoded["signals"]),
            }
    finally:
        bus.shutdown()


def listen_can(channel: str = "vcan0", bustype: str = "socketcan",
               decode: Callable[[Any], Mapping[str, Any]] | None = None,
               buffer: Any = None, stop_event: Any = None) -> int:
    """Listen to CAN frames and optionally append decoded frames to a buffer."""
    count = 0
    for frame in iter_can_frames(channel, bustype, decode, stop_event):
        if buffer is not None and not buffer.append(frame):
            continue
        count += 1
    return count
