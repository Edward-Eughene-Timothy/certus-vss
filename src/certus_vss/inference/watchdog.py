"""
1500ms hard circuit-breaker to abort stalled inference requests.
"""
import time

class Watchdog:
    def __init__(self, timeout_ms: int = 1500):
        self.timeout_ms = timeout_ms
