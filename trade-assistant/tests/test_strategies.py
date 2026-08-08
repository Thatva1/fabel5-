"""Strategy library, registry and regime router.

The important assertions here are the NEGATIVE ones. Any rule set will fire on
something; what makes these strategies worth running is what they refuse —
chasing an extended breakout, shorting into a strong uptrend, calling three
random bars a "range".
"""
import numpy as np
import pandas as pd
import pytest

from assistant.research import regime, scanner
from assistant.strategies import registry, router
from assistant.strategies.base import STATUS_ACTIONABLE, STATUS_WATCH, Strategy, StrategyContext
from assistant.strategies.mean_reversion import MeanReversionStrategy
from assistant.strategies.momentum import MomentumStrategy
from assistant.strategies.range_trading import RangeTradingStrategy
from assistant.strategies.squeeze import SqueezeStrategy


def _frame(closes, volumes=None, spread=0.01):
    closes = pd.Series([float(c) for c in closes])
    volumes = volumes or [1_000_000] * len(closes)
    return pd.DataFrame({
        "Open": closes.shift(1).fillna(closes.iloc[0]),
        "High": closes * (1 + spread),
        "Low": closes * (1 - spread),
        "Close": closes,
        "Volume": [float(v) for v in volumes],
    })


def _ctx(strategy, closes, volumes=None, snapshot_overrides=None, config=None):
    df = _frame(closes, volumes)
    snap = scanner.scan_ticker("TEST", df, {"min_history_days": 60})
    snap.update(snapshot_overrides or {})
    config = config or {}
    return StrategyContext(ticker="TEST", df=df, snapshot=snap,
                           regime=regime.classify(snap, config),
                           params=strategy.params_for(config), config=config)


def _walk(n, start, drift, noise=0.012, seed=24):
    """A seeded random walk with drift — deterministic, but with the pullbacks
    real trends have. A smooth formula series has no down days at all, which
    pins RSI at 100 and gets correctly refused as overextended, so it cannot
    exercise the strategies at all."""
    rng = np.random.default_rng(seed)
    price, out = start, []
    for step in rng.normal(drift, noise, n):
        price *= 1 + step
        out.append(price)
    return out


def _uptrend(n=300, start=100.0, daily=0.004):
    return _walk(n, start, daily)


def _downtrend(n=300, start=300.0, daily=0.004):
    return _walk(n, start, -daily)


def _flat(n=300, level=100.0, amplitude=5.0, period=25):
    return [level + amplitude * np.sin(2 * np.pi * i / period) for i in range(n)]


# --- momentum ----------------------------------------------------------------

def _breakout_series():
    """A clean uptrend that just edged above its 20-day high on heavy volume.

    The final bar is computed from the actual prior extreme rather than a fixed
    multiplier, so the fixture stays a *marginal* break — which is the case
    momentum should take — however the trend fixture is later retuned.
    """
    closes = _uptrend(280)
    prior_high = max(closes[-20:]) * 1.01      # the High column is close * 1.01
    closes += [prior_high * 1.005]
    volumes = [1_000_000] * 280 + [2_000_000]
    return closes, volumes


def _breakdown_series():
    """The mirror: a downtrend cracking just below its 20-day low."""
    closes = _downtrend(280)
    prior_low = min(closes[-20:]) * 0.99       # the Low column is close * 0.99
    closes += [prior_low * 0.995]
    volumes = [1_000_000] * 280 + [2_000_000]
    return closes, volumes


def test_momentum_fires_on_a_confirmed_breakout_in_an_uptrend():
    strategy = MomentumStrategy()
    closes, volumes = _breakout_series()
    ideas = strategy.detect(_ctx(strategy, closes, volumes))
    assert len(ideas) == 1
    idea = ideas[0]
    assert idea.direction == "long"
    assert idea.status == STATUS_ACTIONABLE
    assert idea.stop < idea.entry < idea.target
    assert idea.strategy == "momentum"
    assert idea.regime == "TRENDING_UP"


def test_momentum_refuses_to_chase_an_extended_move():
    """Same breakout, but price has already run 4 ATR past the level. The stop
    would have to be absurdly wide, so there is no defined-risk trade left."""
    strategy = MomentumStrategy()
    closes = _uptrend(280)
    closes += [closes[-1] * 1.35]
    volumes = [1_000_000] * 280 + [3_000_000]
    assert strategy.detect(_ctx(strategy, closes, volumes)) == []


def test_momentum_requires_volume_confirmation():
    strategy = MomentumStrategy()
    closes, _ = _breakout_series()
    quiet = [1_000_000] * 280 + [500_000]
    assert strategy.detect(_ctx(strategy, closes, quiet)) == []


def test_momentum_rejects_an_already_overbought_breakout():
    strategy = MomentumStrategy()
    closes, volumes = _breakout_series()
    ctx = _ctx(strategy, closes, volumes, snapshot_overrides={"rsi": 88.0})
    assert strategy.detect(ctx) == []


def test_momentum_does_not_run_outside_a_trend():
    strategy = MomentumStrategy()
    ctx = _ctx(strategy, _flat())
    assert ctx.regime["regime"] == regime.SIDEWAYS
    assert strategy.detect(ctx) == []


def test_momentum_shorts_a_breakdown_in_a_downtrend():
    strategy = MomentumStrategy()
    ideas = strategy.detect(_ctx(strategy, *_breakdown_series()))
    assert len(ideas) == 1
    assert ideas[0].direction == "short"
    assert ideas[0].target < ideas[0].entry < ideas[0].stop


def test_momentum_relative_strength_can_be_made_mandatory():
    strategy = MomentumStrategy()
    closes, volumes = _breakout_series()
    config = {"strategies": {"momentum": {"min_relative_strength_pct": 0}}}
    lagging = _ctx(strategy, closes, volumes,
                   snapshot_overrides={"relative_strength_pct": -5.0}, config=config)
    assert strategy.detect(lagging) == []
    leading = _ctx(strategy, closes, volumes,
                   snapshot_overrides={"relative_strength_pct": 8.0}, config=config)
    assert len(strategy.detect(leading)) == 1


# --- mean reversion ----------------------------------------------------------

def test_mean_reversion_shorts_a_stalled_overbought_extreme():
    strategy = MeanReversionStrategy()
    # ...spiked to 119, then closed back down at 118: the push has stopped.
    closes = _flat(298) + [119.0, 118.0]
    ctx = _ctx(strategy, closes, snapshot_overrides={
        "rsi": 78.0, "price": 118.0, "bb_upper": 106.0, "bb_middle": 100.0,
        "atr": 3.0, "swing_high_20d": 119.0,
    })
    ideas = [i for i in strategy.detect(ctx) if i.direction == "short"]
    assert len(ideas) == 1
    idea = ideas[0]
    assert idea.target < idea.entry < idea.stop
    assert idea.target == pytest.approx(100.0, abs=0.01)   # back to the 20-day average


def test_mean_reversion_will_not_short_a_move_that_is_still_accelerating():
    """RSI at an extreme AND still making new highs is a falling knife in
    reverse. The stall condition is what stops this being a guess."""
    strategy = MeanReversionStrategy()
    closes = _flat(280) + [130.0, 135.0, 140.0]     # accelerating up into the close
    ctx = _ctx(strategy, closes, snapshot_overrides={
        "rsi": 82.0, "price": 140.0, "bb_upper": 110.0, "bb_middle": 100.0, "atr": 3.0,
    })
    assert [i for i in strategy.detect(ctx) if i.direction == "short"] == []


def test_mean_reversion_requires_price_outside_the_band():
    strategy = MeanReversionStrategy()
    ctx = _ctx(strategy, _flat(), snapshot_overrides={
        "rsi": 75.0, "price": 104.0, "bb_upper": 112.0, "bb_middle": 100.0, "atr": 3.0,
    })
    assert [i for i in strategy.detect(ctx) if i.direction == "short"] == []


def test_mean_reversion_is_not_allowed_to_run_in_a_strong_trend():
    """The single most expensive mistake this strategy could make is shorting
    strength in an uptrend, so the router must never hand it one."""
    assert MeanReversionStrategy() .name not in [
        s.name for s in registry.strategies_for_regime(regime.TRENDING_UP, {})]


# --- range trading -----------------------------------------------------------

def test_range_trading_buys_the_floor_of_a_respected_band():
    strategy = RangeTradingStrategy()
    closes = _flat(300, level=100.0, amplitude=6.0, period=30)
    ctx = _ctx(strategy, closes, snapshot_overrides={"price": 94.5, "atr": 1.2})
    ideas = [i for i in strategy.detect(ctx) if i.direction == "long"]
    assert len(ideas) == 1
    idea = ideas[0]
    assert idea.stop < idea.entry < idea.target
    assert idea.meta["lower_touches"] >= 2


def test_range_trading_rejects_a_band_too_wide_to_be_a_range():
    strategy = RangeTradingStrategy()
    ctx = _ctx(strategy, _flat(300, amplitude=6.0), snapshot_overrides={"atr": 0.2})
    assert strategy.detect(ctx) == []       # 60 ATR tall is a trend, not a range


def test_range_trading_rejects_a_band_thinner_than_the_noise():
    strategy = RangeTradingStrategy()
    ctx = _ctx(strategy, _flat(300, amplitude=6.0), snapshot_overrides={"atr": 20.0})
    assert strategy.detect(ctx) == []


def test_range_trading_does_nothing_mid_channel():
    strategy = RangeTradingStrategy()
    ctx = _ctx(strategy, _flat(300, level=100.0, amplitude=6.0, period=30),
               snapshot_overrides={"price": 100.0, "atr": 1.2})
    assert strategy.detect(ctx) == []


# --- squeeze -----------------------------------------------------------------

def test_squeeze_waits_instead_of_guessing_direction():
    strategy = SqueezeStrategy()
    ctx = _ctx(strategy, _flat(), snapshot_overrides={
        "bb_width_rank": 4.0, "price": 100.0, "bb_upper": 106.0, "bb_lower": 94.0,
    })
    ideas = strategy.detect(ctx)
    assert len(ideas) == 1
    assert ideas[0].status == STATUS_WATCH
    assert ideas[0].direction is None
    assert ideas[0].entry is None and ideas[0].stop is None


def test_squeeze_break_on_weak_volume_stays_a_watch_item():
    strategy = SqueezeStrategy()
    closes = _flat(280) + [120.0]
    ctx = _ctx(strategy, closes, volumes=[1_000_000] * 280 + [400_000],
               snapshot_overrides={"bb_width_rank": 4.0, "price": 120.0,
                                   "bb_upper": 106.0, "bb_lower": 94.0})
    ideas = strategy.detect(ctx)
    assert ideas[0].status == STATUS_WATCH
    assert "unconvincing volume" in ideas[0].headline


def test_squeeze_hands_off_to_momentum_on_a_confirmed_break():
    strategy = SqueezeStrategy()
    closes, volumes = _breakout_series()
    ctx = _ctx(strategy, closes, volumes, snapshot_overrides={
        "bb_width_rank": 4.0,
        "bb_upper": scanner.scan_ticker("T", _frame(*_breakout_series()),
                                        {"min_history_days": 60})["price"] * 0.99,
        "bb_lower": 1.0,
    })
    ideas = strategy.detect(ctx)
    assert len(ideas) == 1
    idea = ideas[0]
    assert idea.status == STATUS_ACTIONABLE
    assert idea.strategy == "momentum"                  # journal measures momentum
    assert idea.meta["handoff_from"] == "squeeze"       # provenance is kept
    assert idea.regime == "VOLATILITY_SQUEEZE"          # the market was in a squeeze


# --- interface guarantees ----------------------------------------------------

def test_a_strategy_cannot_emit_a_long_with_an_upside_down_stop():
    """base.build() is the last line of defence against arithmetic mistakes in a
    strategy — a negative risk-per-share would produce a nonsense position size."""
    strategy = MomentumStrategy()
    ctx = _ctx(strategy, _uptrend())
    assert strategy.build(ctx, "long", entry=100, stop=110, target=120, reasons=[]) is None
    assert strategy.build(ctx, "long", entry=100, stop=90, target=95, reasons=[]) is None
    assert strategy.build(ctx, "short", entry=100, stop=90, target=80, reasons=[]) is None
    good = strategy.build(ctx, "long", entry=100, stop=95, target=115, reasons=[])
    assert good.risk_per_share == 5.0 and good.reward_risk == 3.0


def test_every_idea_is_tagged_with_its_strategy_and_regime():
    strategy = MomentumStrategy()
    closes, volumes = _breakout_series()
    idea = strategy.detect(_ctx(strategy, closes, volumes))[0].to_dict()
    assert idea["strategy"] and idea["strategy_label"] and idea["regime"]
    assert idea["reasons"], "an idea must explain itself"


def test_config_can_disable_a_strategy():
    config = {"strategies": {"momentum": {"enabled": False}}}
    names = [s.name for s in registry.enabled_strategies(config)]
    assert "momentum" not in names
    assert "mean_reversion" in names


def test_config_can_restrict_a_strategy_to_one_direction():
    """Shorting carries costs longs do not — borrow fees, stamp duty on the
    closing purchase, unlimited theoretical loss — so a rule set can be
    profitable long and lose money short. This is how you switch one side off."""
    df = _frame(_flat(300, level=100.0, amplitude=6.0, period=30))
    snap = scanner.scan_ticker("TEST", df, {"min_history_days": 60})
    # Regime inputs pinned to an unambiguous SIDEWAYS (low ADX, wide bands),
    # since this test is about the router's direction filter, not classification.
    snap.update({"price": 94.5, "atr": 1.2, "bb_width_rank": 80.0, "adx": 12.0})

    both = router.route("TEST", df, snap, {})
    assert any(i.direction == "long" for i in both["ideas"])

    config = {"strategies": {"range_trading": {"directions": ["short"]}}}
    longs_off = router.route("TEST", df, snap, config)
    assert not any(i.strategy == "range_trading" and i.direction == "long"
                   for i in longs_off["ideas"])


def test_direction_filter_never_drops_a_watch_item():
    """A squeeze watch item has no direction yet; filtering it out would hide
    the warning entirely."""
    df = _frame(_flat())
    snap = scanner.scan_ticker("TEST", df, {"min_history_days": 60})
    snap.update({"bb_width_rank": 4.0, "price": 100.0, "bb_upper": 106.0, "bb_lower": 94.0})
    config = {"strategies": {"squeeze": {"directions": ["long"]}}}
    ideas = router.route("TEST", df, snap, config)["ideas"]
    assert any(i.status == STATUS_WATCH for i in ideas)


def test_config_can_move_a_strategy_to_a_different_regime():
    config = {"strategies": {"mean_reversion": {"regimes": ["TRENDING_UP"]}}}
    names = [s.name for s in registry.strategies_for_regime("TRENDING_UP", config)]
    assert "mean_reversion" in names


def test_config_overrides_a_single_setting_without_losing_the_rest():
    config = {"strategies": {"momentum": {"rsi_max_long": 65.0}}}
    params = MomentumStrategy().params_for(config)
    assert params["rsi_max_long"] == 65.0
    assert params["min_volume_ratio"] == MomentumStrategy.defaults["min_volume_ratio"]


def test_every_registered_strategy_declares_its_regimes_and_a_description():
    for strategy in registry.all_strategies():
        assert strategy.name and strategy.label and strategy.description
        assert strategy.regimes, f"{strategy.name} must declare which regimes it runs in"
        for r in strategy.regimes:
            assert r in regime.ALL_REGIMES


# --- router ------------------------------------------------------------------

def test_router_runs_only_the_strategies_matching_the_regime():
    closes, volumes = _breakout_series()
    df = _frame(closes, volumes)
    snap = scanner.scan_ticker("TEST", df, {"min_history_days": 60})
    result = router.route("TEST", df, snap, {})
    assert result["regime"]["regime"] == regime.TRENDING_UP
    assert result["strategies_run"] == ["momentum"]
    assert all(i.strategy in ("momentum",) for i in result["ideas"])


def test_router_runs_the_sideways_pair_in_a_sideways_market():
    df = _frame(_flat())
    snap = scanner.scan_ticker("TEST", df, {"min_history_days": 60})
    result = router.route("TEST", df, snap, {})
    assert set(result["strategies_run"]) == {"mean_reversion", "range_trading"}


def test_router_produces_nothing_without_enough_history():
    snap = {"ticker": "TEST", "price": 10, "adx": 30}
    result = router.route("TEST", None, snap, {})
    assert result["ideas"] == []
    assert result["regime"]["regime"] == regime.UNKNOWN


def test_router_survives_a_strategy_that_raises():
    """One broken strategy must not take down the whole scan."""
    class Exploding(Strategy):
        name, label, description = "boom", "Boom", "always fails"
        regimes = (regime.SIDEWAYS,)

        def detect(self, ctx):
            raise RuntimeError("kaboom")

    original = registry.BUILTIN
    registry.BUILTIN = original + (Exploding,)
    try:
        df = _frame(_flat())
        snap = scanner.scan_ticker("TEST", df, {"min_history_days": 60})
        result = router.route("TEST", df, snap, {})
        assert any("kaboom" in note for note in result["notes"])
        assert "mean_reversion" in result["strategies_run"]
    finally:
        registry.BUILTIN = original


def test_router_explains_itself_when_nothing_fires():
    """Silence must be explained. "Momentum ran and found nothing" and "nothing
    ran at all" look identical on a dashboard unless the router says which."""
    df = _frame(_uptrend())          # trending, but no breakout today
    snap = scanner.scan_ticker("TEST", df, {"min_history_days": 60})
    snap["bb_width_rank"] = 80.0     # rule out a squeeze so the regime is the trend
    result = router.route("TEST", df, snap, {})
    assert result["regime"]["regime"] == regime.TRENDING_UP
    assert result["ideas"] == []
    assert any("no setup met its full rule set" in n for n in result["notes"])
