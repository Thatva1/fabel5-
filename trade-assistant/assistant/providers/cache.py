"""In-memory TTL cache + a simple per-provider rate limiter.

Free-tier APIs have quotas (Finnhub: 60 calls/min); a 27-ticker scan without
caching would burn through them fast.
"""
import threading
import time
from collections import deque


class TTLCache:
    def __init__(self):
        self._store = {}
        self._lock = threading.Lock()

    def get_or_fetch(self, key, ttl_seconds, fetch_fn):
        """Return cached value if fresh, else call fetch_fn and cache the result.
        Exceptions from fetch_fn propagate and nothing is cached."""
        now = time.time()
        with self._lock:
            hit = self._store.get(key)
            if hit and now - hit[0] < ttl_seconds:
                return hit[1]
        value = fetch_fn()
        with self._lock:
            self._store[key] = (now, value)
        return value

    def clear(self):
        with self._lock:
            self._store.clear()


class RateLimiter:
    """Blocks until a call slot is free within a rolling one-minute window."""

    def __init__(self, max_calls_per_min):
        self.max_calls = max_calls_per_min
        self._calls = deque()
        self._lock = threading.Lock()

    def acquire(self):
        while True:
            with self._lock:
                now = time.time()
                while self._calls and now - self._calls[0] > 60:
                    self._calls.popleft()
                if len(self._calls) < self.max_calls:
                    self._calls.append(now)
                    return
                wait = 60 - (now - self._calls[0]) + 0.1
            time.sleep(min(wait, 5))
