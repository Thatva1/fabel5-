"""In-memory TTL cache + a simple per-provider rate limiter.

Free-tier APIs have quotas (Finnhub: 60 calls/min); a 27-ticker scan without
caching would burn through them fast.
"""
import threading
import time
from collections import OrderedDict, deque

# Entries hold whole pandas DataFrames, and /api/analyze/<query> lets any of the
# ~18,000 universe symbols create one. Without a bound, memory grew for the life
# of the process: expired entries were never evicted, only ignored.
DEFAULT_MAX_ENTRIES = 512


class TTLCache:
    """TTL cache with an LRU bound.

    Two evictions, doing different jobs: expired entries are dropped on write
    (cheap, keeps the store honest), and the LRU cap bounds the worst case when
    everything in it is still fresh.
    """

    def __init__(self, max_entries=DEFAULT_MAX_ENTRIES):
        self._store = OrderedDict()
        self._lock = threading.Lock()
        self._max_entries = max_entries

    def get_or_fetch(self, key, ttl_seconds, fetch_fn):
        """Return cached value if fresh, else call fetch_fn and cache the result.
        Exceptions from fetch_fn propagate and nothing is cached."""
        now = time.time()
        with self._lock:
            hit = self._store.get(key)
            if hit and now - hit[0] < ttl_seconds:
                self._store.move_to_end(key)      # mark as recently used
                return hit[2]
        value = fetch_fn()
        with self._lock:
            # Each entry carries its OWN ttl. Prices live 10 minutes and macro
            # six hours; sweeping with whichever ttl the current caller happened
            # to pass would throw away the long-lived entries early.
            self._store[key] = (now, ttl_seconds, value)
            self._store.move_to_end(key)
            self._evict(now)
        return value

    def _evict(self, now):
        """Caller holds the lock."""
        for key in [k for k, (stamp, ttl, _) in self._store.items()
                    if now - stamp >= ttl]:
            del self._store[key]
        while len(self._store) > self._max_entries:
            self._store.popitem(last=False)       # drop the least recently used

    def __len__(self):
        with self._lock:
            return len(self._store)

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
