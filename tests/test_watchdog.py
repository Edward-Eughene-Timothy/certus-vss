import time

import pytest

from certus_vss.inference.watchdog import Watchdog


def return_ok():
    """Return a simple result for the watchdog test."""
    return "ok"


def sleep_for_one_second():
    """Simulate an operation that exceeds the watchdog deadline."""
    time.sleep(1)


def test_watchdog_accepts_fast_operation():
    watchdog = Watchdog(timeout_ms=2000)

    result = watchdog.run(return_ok)

    assert result == "ok"


def test_watchdog_times_out_slow_operation():
    watchdog = Watchdog(timeout_ms=100)

    with pytest.raises(TimeoutError):
        watchdog.run(sleep_for_one_second)