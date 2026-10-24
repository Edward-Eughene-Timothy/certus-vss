import time

import pytest

from certus_vss.inference.watchdog import Watchdog


def test_inference_timeout_stops_slow_operation():
    watchdog = Watchdog(timeout_ms=100)

    with pytest.raises(TimeoutError):
        watchdog.run(lambda: time.sleep(2))