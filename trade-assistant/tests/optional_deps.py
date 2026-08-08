"""Skip markers for tests that need an optional dependency (audit finding D-8).

requirements.txt installs the market-data and broker libraries conditionally —
ib_insync on Python 3.9, ib_async on 3.10+, and yfinance is not needed for the
pure-arithmetic layers at all. The tests imported them unconditionally, so on a
clean checkout whole modules aborted COLLECTION and took the entire run with
them. A suite that cannot run on a fresh clone has stopped being a safety net.

Use the markers on individual tests, or `pytest.importorskip` at module scope
when every test in the file needs the dependency.
"""
import importlib.util

import pytest


def _installed(*names):
    return any(importlib.util.find_spec(name) is not None for name in names)


HAS_YFINANCE = _installed("yfinance")
HAS_IB = _installed("ib_async", "ib_insync")

requires_yfinance = pytest.mark.skipif(
    not HAS_YFINANCE,
    reason="needs yfinance (installed conditionally; see requirements.txt)")

requires_ib = pytest.mark.skipif(
    not HAS_IB,
    reason="needs an IB client library: ib_async (3.10+) or ib_insync (3.9)")
