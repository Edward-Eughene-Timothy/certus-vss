"""
24MB volatile tmpfs ring buffer with priority queue shedding.
"""
from collections import deque

class TelemetryRingBuffer:
    def __init__(self, maxlen: int = 36000): # 60 mins @ 10Hz
        self.buffer = deque(maxlen=maxlen)

    def append(self, item):
        self.buffer.append(item)
