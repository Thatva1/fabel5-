"""Choosing what the intraday loop watches.

The universe is 1,568 instruments and a five-minute loop can poll about sixty,
so something has to choose. These tests are mostly about the ways a selector
can look sensible and be worthless: picking things that do not move, trusting a
measurement that has no absolute scale, or letting a scale-free ratio promote
an instrument whose numerator and denominator are both nearly zero.
"""
import numpy as np
import pandas as pd
import pytest

from assistant.paper import intraday_watchlist as wl


def daily(range_pct, price=100.0, volume=1_000_000, days=90, seed=0):
    """A daily series with a controlled average high-low range."""
    rng = np.random.default_rng(seed)
    close = price + np.cumsum(rng.normal(0, price * 0.005, days))
    half = range_pct / 200
    return pd.DataFrame({
        "Open": close, "High": close * (1 + half), "Low": close * (1 - half),
        "Close": close, "Volume": [volume] * days},
        index=pd.bdate_range("2026-04-01", periods=days))


CONFIG = {"intraday": {"watchlist": {
    "size": 5, "pool": 10, "min_price": 5.0, "min_dollar_volume": 20_000_000,
    "min_range_pct": 0.8, "keep_tightest_pct": 100.0,
    "max_spread_bps": 15.0, "min_range_to_cost": 8.0}}}


def test_an_instrument_that_does_not_move_is_rejected_however_tight_it_is():
    """BIL and SHV score beautifully on travel-per-cost because BOTH numbers
    are tiny. A 0.01% daily range cannot pay a commission, whatever the ratio
    says, and a scale-free score alone would put them at the top of the list."""
    frames = {"BIL": daily(0.01), "GDX": daily(3.0, seed=1)}
    kept, rejected = wl.rank(frames, CONFIG)
    assert [row["ticker"] for row in kept] == ["GDX"]
    assert rejected["range"] == 1


def test_a_cheap_share_is_rejected_because_a_penny_is_a_wide_spread_on_it():
    frames = {"PENNY": daily(3.0, price=2.0), "REAL": daily(3.0, price=80.0, seed=2)}
    kept, rejected = wl.rank(frames, CONFIG)
    assert [row["ticker"] for row in kept] == ["REAL"]
    assert rejected["price"] == 1


def test_a_thin_instrument_is_rejected_on_turnover():
    frames = {"THIN": daily(3.0, volume=100), "LIQUID": daily(3.0, seed=3)}
    kept, rejected = wl.rank(frames, CONFIG)
    assert [row["ticker"] for row in kept] == ["LIQUID"]
    assert rejected["volume"] == 1


def test_the_daily_spread_gate_is_a_percentile_not_a_basis_point_cap():
    """Corwin-Schultz on daily bars has no trustworthy absolute scale — it
    returns a median of 63bp across this universe where the real figure is a
    few. It orders correctly, so it is used to rank and never to threshold."""
    frames = {f"T{i}": daily(3.0, seed=i) for i in range(10)}
    half = {"intraday": {"watchlist": {**CONFIG["intraday"]["watchlist"],
                                       "keep_tightest_pct": 50.0}}}
    kept, rejected = wl.rank(frames, half)
    assert len(kept) == 5
    assert rejected["spread_rank"] == 5
    # And the survivors are the tightest half, not an arbitrary five.
    dropped_widest = max(row["spread_bps"] for row in kept)
    assert dropped_widest <= sorted(
        wl._metrics(f, 60)["spread_bps"] for f in frames.values())[5]


def test_the_pool_is_not_called_a_watchlist_and_says_why():
    frames = {f"T{i}": daily(3.0, seed=i) for i in range(20)}
    pool = wl.build(frames, CONFIG)
    assert pool["stage"] == "daily-prefilter"
    assert "re-measures on intraday bars" in pool["note"]
    assert len(pool["symbols"]) <= 10


# --- stage two ---------------------------------------------------------------

def intraday(spread_bps, bars=200, seed=0):
    """Five-minute bars whose high-low is dominated by a known spread."""
    rng = np.random.default_rng(seed)
    mid = 100 + np.cumsum(rng.normal(0, 0.01, bars))
    half = spread_bps / 2 / 10_000
    return pd.DataFrame({
        "Open": mid, "High": mid * (1 + half), "Low": mid * (1 - half),
        "Close": mid, "Volume": [10_000] * bars},
        index=pd.date_range("2026-08-21 09:30", periods=bars, freq="5min"))


def _pool(tickers, range_pct=3.0):
    return {"symbols": list(tickers),
            "detail": [{"ticker": t, "range_pct": range_pct, "price": 100.0}
                       for t in tickers]}


def test_stage_two_applies_a_real_cap_to_a_real_measurement():
    pool = _pool(["TIGHT", "WIDE"])
    frames = {"TIGHT": intraday(4), "WIDE": intraday(120, seed=1)}
    out = wl.refine(pool, frames, CONFIG)
    assert out["symbols"] == ["TIGHT"]
    assert out["dropped"]["spread"] == 1
    assert out["stage"] == "intraday-measured"


def test_an_instrument_whose_travel_cannot_pay_its_spread_is_dropped():
    """The arithmetic no rule can beat: a name offering fewer round trips of
    daily movement than it costs to trade."""
    pool = _pool(["SLOW"], range_pct=0.9)
    out = wl.refine(pool, {"SLOW": intraday(14)}, CONFIG)
    assert out["symbols"] == []
    assert out["dropped"]["range_to_cost"] == 1


def test_an_instrument_with_no_intraday_bars_is_dropped_not_assumed_tight():
    pool = _pool(["GOOD", "NOBARS"])
    out = wl.refine(pool, {"GOOD": intraday(4)}, CONFIG)
    assert out["symbols"] == ["GOOD"]
    assert out["dropped"]["no_bars"] == 1


def test_the_final_set_is_capped_at_what_the_loop_can_poll():
    pool = _pool([f"T{i}" for i in range(12)])
    frames = {f"T{i}": intraday(3, seed=i) for i in range(12)}
    out = wl.refine(pool, frames, CONFIG)
    assert out["size"] == 5           # `size`, not `pool`
    assert out["qualified"] == 12     # and it says how many it had to choose from


def test_the_measured_spread_replaces_the_daily_estimate_on_the_row():
    pool = _pool(["T"])
    out = wl.refine(pool, {"T": intraday(6)}, CONFIG)
    row = out["detail"][0]
    assert 0 < row["intraday_spread_bps"] <= 15.0
    assert row["range_to_cost"] == pytest.approx(
        row["range_pct"] / (row["intraday_spread_bps"] / 100), rel=0.01)


# --- persistence -------------------------------------------------------------

def test_a_stale_working_set_is_not_returned(tmp_path):
    """A spread measured six weeks ago is not this morning's spread, and a live
    loop trading yesterday's liquidity profile is the failure this whole module
    exists to avoid."""
    path = str(tmp_path / "wl.json")
    wl.save({"built_at": 0, "symbols": ["OLD"], "detail": []}, path)
    assert wl.load(path) is None
    assert wl.load(path, max_age_hours=0) is not None      # 0 = no limit


def test_a_fresh_working_set_round_trips(tmp_path):
    import time
    path = str(tmp_path / "wl.json")
    wl.save({"built_at": time.time(), "symbols": ["NEW"], "detail": []}, path)
    assert wl.load(path)["symbols"] == ["NEW"]


def test_a_missing_file_is_none_rather_than_an_error(tmp_path):
    assert wl.load(str(tmp_path / "nope.json")) is None
