import time

import pytest

from certus_vss.inference.watchdog import Watchdog


def test_watchdog_accepts_fast_operation():
    watchdog = Watchdog(timeout_ms=500)

    result = watchdog.run(lambda: "ok")

    assert result == "ok"


def test_watchdog_times_out_slow_operation():
    watchdog = Watchdog(timeout_ms=50)

    with pytest.raises(TimeoutError):
        watchdog.run(lambda: time.sleep(1))