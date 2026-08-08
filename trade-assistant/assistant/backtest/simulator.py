"""Trade fill simulation — pure functions over bars, no I/O, no state.

This is where backtests usually lie, so the assumptions are explicit and every
one of them is deliberately pessimistic:

  * A signal computed from a bar's CLOSE cannot be traded at that close — you
    only knew it once the bar finished. Entry is the NEXT bar's open by default.
  * A gap through the stop fills at the open, not at the stop. That is what
    actually happens, and it is how a "-1R" trade becomes -3R in real life.
  * When one bar's range covers both the stop and the target, there is no
    intrabar data to say which came first, so the STOP is assumed. Every
    ambiguous trade is scored as a loss.
  * Costs (commission, slippage, UK stamp duty) come off every trade.

An optimistic backtest is worse than no backtest: it produces confidence in
rules that lose money.
"""
from ..risk import pnl as pnl_math

STOP = "stop"
TARGET = "target"
TIMEOUT = "timeout"
NO_EXIT = "still_open"

COST_DEFAULTS = {
    "commission_per_trade": 6.0,   # per side, in the instrument's currency
    "slippage_bps": 5.0,           # 0.05% against you, each way
    "stamp_duty_pct": 0.5,         # UK: charged on PURCHASES only
    "stamp_duty_suffixes": [".L"],
}


def cost_config(config):
    return {**COST_DEFAULTS, **((config or {}).get("backtest", {}).get("costs") or {})}


def cost_config_for(ticker, config):
    """Costs for ONE instrument, because they are not the same everywhere.

    UK shares carry 0.5% stamp duty on purchase and a higher flat commission;
    US shares carry neither. Applying one market's cost structure globally is
    how a UK-specific tax gets mistaken for a flaw in a strategy.

    `per_market` is keyed by ticker suffix, with '' meaning no suffix (US).
    """
    base = cost_config(config)
    per_market = base.get("per_market") or {}
    suffix = ""
    name = str(ticker).upper()
    for candidate in per_market:
        if candidate and name.endswith(candidate.upper()):
            if len(candidate) > len(suffix):
                suffix = candidate
    override = per_market.get(suffix if suffix else "", {})
    return {**base, **override}


def apply_slippage(price, direction, side, slippage_bps):
    """Move the fill against you by `slippage_bps`.

    Buying fills a little higher than you wanted and selling a little lower —
    on both the entry and the exit. Applying it in only one direction is a
    common way to make a losing strategy look breakeven.
    """
    buying = (direction == "long" and side == "entry") or \
             (direction == "short" and side == "exit")
    factor = 1 + (slippage_bps / 10_000) * (1 if buying else -1)
    return round(price * factor, 4)


def trade_costs(ticker, direction, entry_price, exit_price, shares, costs):
    """Total round-trip cost in the instrument's currency.

    Stamp duty is a UK tax on share PURCHASES. A long pays it on the way in; a
    short pays it on the buy-back that closes the position. Either way it is
    charged once, on the purchase leg.
    """
    commission = costs["commission_per_trade"] * 2      # in and out
    duty = 0.0
    suffixes = tuple(costs.get("stamp_duty_suffixes") or ())
    if suffixes and str(ticker).upper().endswith(tuple(s.upper() for s in suffixes)):
        purchase_price = entry_price if direction == "long" else exit_price
        duty = abs(shares) * purchase_price * costs["stamp_duty_pct"] / 100
    return round(commission + duty, 2)


def _hits(direction, bar, stop, target):
    """What this bar could have done to an open position, worst case first.

    Returns (exit_price, reason) or (None, None). Order matters enormously:
    checking the target first would silently convert every ambiguous bar into a
    winner, which is the single easiest way to fake a profitable backtest.
    """
    open_, high, low = float(bar["Open"]), float(bar["High"]), float(bar["Low"])

    if direction == "long":
        if open_ <= stop:                 # gapped through the stop overnight
            return open_, STOP            # ...filled at the open, worse than the stop
        if low <= stop:
            return stop, STOP
        if open_ >= target:               # gapped past the target, in your favour
            return open_, TARGET
        if high >= target:
            return target, TARGET
        return None, None

    if open_ >= stop:
        return open_, STOP
    if high >= stop:
        return stop, STOP
    if open_ <= target:
        return open_, TARGET
    if low <= target:
        return target, TARGET
    return None, None


def simulate_trailing(direction, entry_price, stop, target, future_bars, atr,
                      trail_atr=2.5, activate_at_r=1.0, max_holding_bars=40):
    """Ride the trend: replace the fixed target with a stop that follows price.

    Tests the "winners are being capped" hypothesis directly rather than
    inferring it. A fixed target caps every winner at exactly its target; a
    trailing stop lets a 3R move become 8R and gives back the last `trail_atr`
    of it in exchange.

    The trail only ACTIVATES once the trade is `activate_at_r` in profit.
    Trailing from entry would convert ordinary early noise into an immediate
    exit and destroy trades that were about to work — a common way to make a
    trailing stop look worse than it is.

    Returns (exit_price, reason, bars_held) or (None, NO_EXIT, bars).
    """
    if atr is None or atr <= 0:
        return None, NO_EXIT, 0

    long_side = direction == "long"
    risk_per_share = abs(entry_price - stop)
    activation = (entry_price + activate_at_r * risk_per_share if long_side
                  else entry_price - activate_at_r * risk_per_share)
    best = entry_price
    live_stop = stop
    limit = min(len(future_bars), max_holding_bars)

    for offset in range(limit):
        bar = future_bars.iloc[offset]
        open_, high, low = float(bar["Open"]), float(bar["High"]), float(bar["Low"])
        bars_held = offset + 1

        # The stop is checked BEFORE the trail is advanced on the same bar.
        # Advancing first would let a bar that traded through the stop escape
        # it because the same bar's high moved the stop up — using the bar's
        # own future to survive the bar.
        if long_side:
            if open_ <= live_stop:
                return open_, STOP, bars_held
            if low <= live_stop:
                return live_stop, STOP, bars_held
            best = max(best, high)
            if best >= activation:
                live_stop = max(live_stop, best - trail_atr * atr)
        else:
            if open_ >= live_stop:
                return open_, STOP, bars_held
            if high >= live_stop:
                return live_stop, STOP, bars_held
            best = min(best, low)
            if best <= activation:
                live_stop = min(live_stop, best + trail_atr * atr)

    if limit == 0:
        return None, NO_EXIT, 0
    return float(future_bars.iloc[limit - 1]["Close"]), TIMEOUT, limit


def simulate_trade(ticker, direction, entry_price, stop, target, shares, future_bars,
                   costs, max_holding_bars=40, exit_style="fixed", atr=None,
                   trail_atr=2.5, activate_at_r=1.0, scale_out_fraction=0.0):
    """Walk forward bar by bar until the stop, the target, or the time limit.

    future_bars: the bars AFTER entry, in order. The entry bar itself is
    excluded — a position opened at an open cannot also be stopped out by the
    same bar's low without intrabar data, and assuming it could would invent
    losses as freely as ignoring it invents wins.
    """
    if entry_price is None or stop is None or target is None or not shares:
        return None

    exit_price, reason, bars_held = None, NO_EXIT, 0
    limit = min(len(future_bars), max_holding_bars)
    scale_out = None

    if exit_style == "trailing":
        exit_price, reason, bars_held = simulate_trailing(
            direction, entry_price, stop, target, future_bars, atr,
            trail_atr=trail_atr, activate_at_r=activate_at_r,
            max_holding_bars=max_holding_bars)
        if exit_price is None:
            return None
    else:
        for offset in range(limit):
            bar = future_bars.iloc[offset]
            bars_held = offset + 1
            hit_price, hit_reason = _hits(direction, bar, stop, target)
            if hit_price is not None:
                exit_price, reason = hit_price, hit_reason
                break

        if exit_price is None:
            if limit == 0:
                return None                # no future data: not a trade, just an edge
            exit_price = float(future_bars.iloc[limit - 1]["Close"])
            reason = TIMEOUT
            bars_held = limit

        # Scale-out: bank part of the position at the original target, then
        # trail the rest. Keeps the certainty of the fixed target on half the
        # size while leaving the other half free to run.
        if scale_out_fraction and reason == TARGET and 0 < scale_out_fraction < 1:
            runner_price, runner_reason, runner_bars = simulate_trailing(
                direction, entry_price, stop, target,
                future_bars.iloc[bars_held:], atr, trail_atr=trail_atr,
                activate_at_r=0.0,
                max_holding_bars=max_holding_bars - bars_held)
            if runner_price is not None:
                scale_out = {
                    "banked_fraction": scale_out_fraction,
                    "banked_price": exit_price,
                    "runner_price": round(runner_price, 4),
                    "runner_reason": runner_reason,
                }
                exit_price = (exit_price * scale_out_fraction
                              + runner_price * (1 - scale_out_fraction))
                reason = f"{TARGET}+{runner_reason}"
                bars_held += runner_bars

    filled_entry = apply_slippage(entry_price, direction, "entry", costs["slippage_bps"])
    filled_exit = apply_slippage(exit_price, direction, "exit", costs["slippage_bps"])

    gross = pnl_math.realised_pnl(direction, filled_entry, filled_exit, shares)
    cost = trade_costs(ticker, direction, filled_entry, filled_exit, shares, costs)
    net = round(gross - cost, 2)

    return {
        "direction": direction,
        "planned_entry": round(float(entry_price), 4),
        "entry_price": filled_entry,
        "exit_price": filled_exit,
        "stop": stop,
        "target": target,
        "shares": shares,
        "bars_held": bars_held,
        "exit_reason": reason,
        "gross_pnl": gross,
        "costs": cost,
        "pnl": net,
        # R is measured on the PLANNED risk — the distance you accepted when you
        # entered. Measuring it on the filled prices would quietly shrink the
        # denominator every time slippage hurt you, flattering the result.
        "r_multiple": pnl_math.r_multiple(direction, entry_price, filled_exit, stop),
        "r_multiple_gross": pnl_math.r_multiple(direction, entry_price, exit_price, stop),
        "exit_style": exit_style,
        "scale_out": scale_out,
    }
