"""Audit finding D-3: the price cache grew without bound.

Entries hold whole pandas DataFrames and /api/analyze/<query> lets any of the
~18,000 universe symbols create one. Expired entries were never evicted, only
ignored, so memory grew monotonically for the life of the process. Keys were
also un-normalised, so 'aapl' and 'AAPL' each fetched and stored their own copy.
"""
import time

from assistant.providers.cache import TTLCache   # pure stdlib, always importable

from .optional_deps import requires_yfinance


def _counter():
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return calls["n"]
    return calls, fetch


def test_a_fresh_entry_is_served_from_cache():
    cache = TTLCache()
    calls, fetch = _counter()
    assert cache.get_or_fetch("k", 60, fetch) == 1
    assert cache.get_or_fetch("k", 60, fetch) == 1
    assert calls["n"] == 1


def test_an_expired_entry_is_refetched():
    cache = TTLCache()
    calls, fetch = _counter()
    cache.get_or_fetch("k", 0.01, fetch)
    time.sleep(0.02)
    assert cache.get_or_fetch("k", 0.01, fetch) == 2


def test_expired_entries_are_evicted_not_merely_ignored():
    """The leak: stale entries stayed in the store holding their DataFrames."""
    cache = TTLCache()
    for i in range(50):
        cache.get_or_fetch(f"stale:{i}", 0.01, lambda: "payload")
    time.sleep(0.02)
    cache.get_or_fetch("fresh", 60, lambda: "payload")
    assert len(cache) == 1, "expired entries were left in the store"


def test_the_store_is_bounded_even_when_everything_is_fresh():
    cache = TTLCache(max_entries=10)
    for i in range(500):
        cache.get_or_fetch(f"sym:{i}", 3600, lambda: "payload")
    assert len(cache) == 10


def test_eviction_is_least_recently_used():
    cache = TTLCache(max_entries=3)
    for key in ("a", "b", "c"):
        cache.get_or_fetch(key, 3600, lambda: key)
    cache.get_or_fetch("a", 3600, lambda: "a")     # 'a' is now the most recent
    cache.get_or_fetch("d", 3600, lambda: "d")     # evicts 'b', the oldest

    calls, fetch = _counter()
    cache.get_or_fetch("a", 3600, fetch)
    assert calls["n"] == 0, "'a' should still be cached"
    cache.get_or_fetch("b", 3600, fetch)
    assert calls["n"] == 1, "'b' should have been evicted"


def test_each_entry_expires_on_its_own_ttl():
    """Prices live 10 minutes and macro six hours. A single sweep must not
    evict the long-lived entry using the short-lived one's ttl."""
    cache = TTLCache()
    cache.get_or_fetch("macro", 3600, lambda: "long-lived")
    cache.get_or_fetch("prices", 0.01, lambda: "short-lived")
    time.sleep(0.02)
    cache.get_or_fetch("other", 0.01, lambda: "trigger a sweep")

    calls, fetch = _counter()
    assert cache.get_or_fetch("macro", 3600, fetch) == "long-lived"
    assert calls["n"] == 0


def test_exceptions_are_not_cached():
    cache = TTLCache()
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("upstream down")
        return "recovered"

    try:
        cache.get_or_fetch("k", 60, flaky)
    except ValueError:
        pass
    assert cache.get_or_fetch("k", 60, flaky) == "recovered"


def test_clear_empties_the_store():
    cache = TTLCache()
    cache.get_or_fetch("k", 60, lambda: 1)
    cache.clear()
    assert len(cache) == 0


# --- key normalisation -------------------------------------------------------

@requires_yfinance
def test_router_cache_keys_upper_case_the_ticker():
    from assistant.providers.router import DataRouter
    assert DataRouter._key("prices", "aapl", "1y") == DataRouter._key("prices", "AAPL", "1y")
    assert DataRouter._key("prices", "aapl", "1y") == "prices:AAPL:1y"


@requires_yfinance
def test_router_cache_keys_keep_the_period_distinct():
    from assistant.providers.router import DataRouter
    assert DataRouter._key("prices", "AAPL", "1y") != DataRouter._key("prices", "AAPL", "5d")


@requires_yfinance
def test_router_cache_key_handles_a_missing_ticker():
    from assistant.providers.router import DataRouter
    assert DataRouter._key("prices", None) == "prices:"
