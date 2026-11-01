"""Non-blocking scheduler for post-event telemetry capture."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from certus_vss.ingestion.ring_buffer import TelemetryRingBuffer

logger = logging.getLogger(__name__)


class PostEventCaptureScheduler:
    """Schedule freeze-frame completion without blocking telemetry ingestion."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._timers: dict[int, threading.Timer] = {}

    def schedule(
        self,
        event_id: int,
        buffer: TelemetryRingBuffer,
        event_timestamp: float,
        db_path: str | Path,
        *,
        pre_seconds: float = 120.0,
        post_seconds: float = 120.0,
    ) -> threading.Timer:
        """Capture the completed event window after the post-event delay."""
        if post_seconds < 0:
            raise ValueError("post_seconds must be non-negative")

        timer = threading.Timer(
            post_seconds,
            self._capture,
            kwargs={
                "event_id": event_id,
                "buffer": buffer,
                "event_timestamp": event_timestamp,
                "db_path": db_path,
                "pre_seconds": pre_seconds,
                "post_seconds": post_seconds,
            },
        )
        timer.daemon = True

        with self._lock:
            previous = self._timers.pop(event_id, None)
            if previous is not None:
                previous.cancel()

            self._timers[event_id] = timer

        timer.start()
        return timer

    def _capture(
        self,
        *,
        event_id: int,
        buffer: TelemetryRingBuffer,
        event_timestamp: float,
        db_path: str | Path,
        pre_seconds: float,
        post_seconds: float,
    ) -> None:
        """Run the database capture in the background."""
        try:
            # Import here to avoid a circular import.
            from certus_vss.storage.event_capture import capture_event_window

            capture_event_window(
                event_id,
                buffer,
                event_timestamp,
                db_path,
                pre_seconds=pre_seconds,
                post_seconds=post_seconds,
            )
        except Exception:
            logger.exception(
                "Post-event capture failed for event_id=%s",
                event_id,
            )
        finally:
            with self._lock:
                current = self._timers.get(event_id)
                if current is threading.current_thread():
                    self._timers.pop(event_id, None)

    def cancel(self, event_id: int) -> bool:
        """Cancel a pending capture; return whether one was found."""
        with self._lock:
            timer = self._timers.pop(event_id, None)

        if timer is None:
            return False

        timer.cancel()
        return True

    def shutdown(self) -> None:
        """Cancel all scheduled captures."""
        with self._lock:
            timers = list(self._timers.values())
            self._timers.clear()

        for timer in timers:
            timer.cancel()