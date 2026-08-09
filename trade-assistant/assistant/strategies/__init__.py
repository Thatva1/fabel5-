"""Pluggable trading strategy library.

Each strategy is a self-contained module implementing the Strategy interface in
base.py. The router (router.py) reads the market regime for a ticker and runs
only the strategies that apply to it.

The library holds factor strategies: monthly-rebalanced rules whose edge comes
from a rank or a sign held for weeks, not from a chart pattern on today's bar.

    xs_momentum   buy the universe's twelve-month leaders, monthly
    ts_momentum   long what has risen over its own year, short what has fallen
    low_beta      tilt toward the calm names that are still trending

Shared machinery lives in factors.py (rebalance calendar, ATR levels,
volatility) and cross_section.py (universe-wide ranking, look-ahead-safe).

To add a strategy: write a module here, subclass Strategy, and register it in
registry.py. Nothing else in the codebase needs to change.
"""
