"""Compact, bounded rolling buffer for normalized vehicle telemetry.

The default buffer stores 36,000 frames (60 minutes at 10 Hz) for the six
signals configured in ``configs/vss_signals.json``. Frames are stored in a
fixed-width bytearray, rather than Python dictionaries, so the backing payload
is predictable and independent of Python object overhead per frame.

This is volatile Tier-1 storage only. It does not persist telemetry to disk.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import math
import struct
import threading
from typing import Any


SIGNAL_PATHS: tuple[str, ...] = (
    "Vehicle.Speed",
    "Vehicle.Powertrain.TractionBattery.Temperature",
    "Vehicle.Powertrain.TractionBattery.Current",
    "Vehicle.Powertrain.TractionBattery.StateOfCharge",
    "Vehicle.Cabin.HVAC.PowerDraw",
    "Vehicle.Powertrain.ElectricMotor.CoolantTemperature",
)

# timestamp (float64), sequence (uint64), six float64 signal values, valid mask.
_RECORD = struct.Struct("<dQ6dB")
_RECORD_BYTES = _RECORD.size
_DEFAULT_MAXLEN = 36_000
_DEFAULT_MAX_BYTES = 24 * 1024 * 1024
_RETENTION_SECONDS = 60 * 60


class TelemetryRingBuffer:
    """Thread-safe fixed-record ring buffer for the six configured VSS signals.

    ``max_bytes`` limits the allocated record storage, not a JSON serialization.
    Snapshot dictionaries are created only when callers retrieve data. Input
    frames must have ``timestamp`` and a ``signals`` mapping. Signal values may
    be numeric scalars or mappings containing a numeric ``value`` member.

    Timestamps must be nondecreasing, as expected from a single ordered stream.
    Out-of-order frames are rejected rather than silently corrupting retention
    and chronological-window semantics.
    """

    def __init__(self, maxlen: int = _DEFAULT_MAXLEN,
                 max_bytes: int = _DEFAULT_MAX_BYTES,
                 retention_seconds: int = _RETENTION_SECONDS):
        if maxlen <= 0:
            raise ValueError("maxlen must be greater than zero")
        if max_bytes <= 0:
            raise ValueError("max_bytes must be greater than zero")
        if retention_seconds <= 0:
            raise ValueError("retention_seconds must be greater than zero")
        if maxlen * _RECORD_BYTES > max_bytes:
            raise ValueError(
                f"maxlen requires {maxlen * _RECORD_BYTES} bytes, exceeding max_bytes={max_bytes}"
            )

        self.maxlen = int(maxlen)
        self.max_bytes = int(max_bytes)
        self.retention_seconds = int(retention_seconds)
        self._storage = bytearray(self.maxlen * _RECORD_BYTES)
        self._start = 0
        self._count = 0
        self._next_sequence = 0
        self._latest_timestamp: float | None = None
        self._lock = threading.RLock()
        self.evicted_frames = 0
        self.expired_frames = 0
        self.rejected_out_of_order = 0
        self.rejected_invalid = 0

    @staticmethod
    def _timestamp_value(value: Any) -> float:
        """Convert epoch seconds or ISO-8601 text to epoch seconds."""
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            result = float(value)
        elif isinstance(value, str):
            normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
            dt = datetime.fromisoformat(normalized)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            result = dt.timestamp()
        else:
            raise ValueError("timestamp must be epoch seconds or ISO-8601 text")
        if not math.isfinite(result):
            raise ValueError("timestamp must be finite")
        return result

    @staticmethod
    def _signal_value(value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, Mapping):
            value = value.get("value")
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("signal values must be numeric, null, or contain a numeric 'value'")
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("signal values must be finite")
        return result

    def _decode_at(self, physical_index: int) -> dict[str, Any]:
        offset = physical_index * _RECORD_BYTES
        unpacked = _RECORD.unpack_from(self._storage, offset)
        timestamp, sequence, *tail = unpacked
        values = tail[:6]
        valid_mask = tail[6]
        signals = {
            path: values[i] if valid_mask & (1 << i) else None
            for i, path in enumerate(SIGNAL_PATHS)
        }
        return {"timestamp": timestamp, "sequence": sequence, "signals": signals}

    def _expire_before_locked(self, cutoff: float) -> int:
        expired = 0
        while self._count:
            timestamp = _RECORD.unpack_from(self._storage, self._start * _RECORD_BYTES)[0]
            if timestamp >= cutoff:
                break
            self._start = (self._start + 1) % self.maxlen
            self._count -= 1
            expired += 1
        self.expired_frames += expired
        return expired

    def append(self, item: Mapping[str, Any]) -> bool:
        """Append one normalized frame; return False for rejected frames."""
        try:
            if not isinstance(item, Mapping) or "timestamp" not in item:
                raise ValueError("frame must be a mapping containing timestamp")
            timestamp = self._timestamp_value(item["timestamp"])
            raw_signals = item.get("signals")
            if not isinstance(raw_signals, Mapping):
                raise ValueError("frame must contain a signals mapping")
            unknown = set(raw_signals) - set(SIGNAL_PATHS)
            if unknown:
                raise ValueError(f"unknown VSS signal(s): {', '.join(sorted(unknown))}")
            sequence = item.get("sequence", self._next_sequence)
            if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
                raise ValueError("sequence must be a non-negative integer")
            values: list[float] = []
            valid_mask = 0
            for index, path in enumerate(SIGNAL_PATHS):
                value = self._signal_value(raw_signals.get(path))
                values.append(0.0 if value is None else value)
                if value is not None:
                    valid_mask |= 1 << index
        except (TypeError, ValueError, OverflowError):
            with self._lock:
                self.rejected_invalid += 1
            return False

        with self._lock:
            if self._latest_timestamp is not None and timestamp < self._latest_timestamp:
                self.rejected_out_of_order += 1
                return False

            # Retention is measured against the latest accepted stream timestamp.
            self._expire_before_locked(timestamp - self.retention_seconds)
            if self._count == self.maxlen:
                self._start = (self._start + 1) % self.maxlen
                self._count -= 1
                self.evicted_frames += 1

            physical_index = (self._start + self._count) % self.maxlen
            packed = _RECORD.pack(timestamp, sequence, *values, valid_mask)
            offset = physical_index * _RECORD_BYTES
            self._storage[offset:offset + _RECORD_BYTES] = packed
            self._count += 1
            self._next_sequence = max(self._next_sequence + 1, sequence + 1)
            self._latest_timestamp = timestamp
            return True

    def expire(self, now: str | int | float) -> int:
        """Explicitly expire frames older than ``now - retention_seconds``.

        Call this from a timer if records must expire even while ingestion is
        paused. The timestamp domain must match the stream's timestamp domain.
        """
        now_s = self._timestamp_value(now)
        with self._lock:
            return self._expire_before_locked(now_s - self.retention_seconds)

    def snapshot(self) -> list[dict[str, Any]]:
        """Return a chronological, detached list of retained frames."""
        with self._lock:
            return [self._decode_at((self._start + i) % self.maxlen)
                    for i in range(self._count)]

    def get_since(self, timestamp: str | int | float) -> list[dict[str, Any]]:
        cutoff = self._timestamp_value(timestamp)
        with self._lock:
            return [frame for frame in (
                self._decode_at((self._start + i) % self.maxlen)
                for i in range(self._count)
            ) if frame["timestamp"] >= cutoff]

    def get_window(self, start: str | int | float,
                   end: str | int | float) -> list[dict[str, Any]]:
        start_s = self._timestamp_value(start)
        end_s = self._timestamp_value(end)
        if end_s < start_s:
            raise ValueError("end must be greater than or equal to start")
        with self._lock:
            return [frame for frame in (
                self._decode_at((self._start + i) % self.maxlen)
                for i in range(self._count)
            ) if start_s <= frame["timestamp"] <= end_s]

    def clear(self) -> None:
        with self._lock:
            self._start = 0
            self._count = 0
            self._latest_timestamp = None
            self._next_sequence = 0
            self.evicted_frames = 0
            self.expired_frames = 0
            self.rejected_out_of_order = 0
            self.rejected_invalid = 0

    def __len__(self) -> int:
        with self._lock:
            return self._count

    @property
    def payload_bytes(self) -> int:
        """Allocated fixed-record storage in bytes (not process RSS)."""
        return len(self._storage)

    @property
    def utilization(self) -> float:
        """Fraction of frame slots currently occupied."""
        with self._lock:
            return self._count / self.maxlen

    @property
    def record_bytes(self) -> int:
        return _RECORD_BYTES

    @property
    def latest_timestamp(self) -> float | None:
        with self._lock:
            return self._latest_timestamp
