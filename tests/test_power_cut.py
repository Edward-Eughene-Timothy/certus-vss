import time

import pytest

from certus_vss.inference.watchdog import Watchdog


def sleep_for_two_seconds():
    """Simulate a slow inference operation."""
    time.sleep(2)


def test_inference_timeout_stops_slow_operation():
    watchdog = Watchdog(timeout_ms=100)

    with pytest.raises(TimeoutError):
        watchdog.run(sleep_for_two_seconds)