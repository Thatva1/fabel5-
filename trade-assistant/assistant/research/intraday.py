"""Does an edge exist at intraday horizons? A test, not a trading system.

WHY THIS EXISTS

The strategy library ranks on daily bars: a trailing year of returns, a
200-day average, a three-year reversal. Those are not slow by accident — they
are the horizons the underlying research was conducted at, and running them on
five-minute bars would not produce more signals of the same kind, it would
produce different signals that no evidence supports.

The reasonable question underneath "why not five-minute bars" is therefore not
"can we run these faster" but "is there a separate edge at a faster horizon,
worth building separate strategies for". That is an empirical question, it is
cheap to answer, and answering it costs nothing but compute — whereas the live
data subscription it would justify is a recurring bill. Research first.

WHAT DECIDES THE ANSWER

Costs. Almost every intraday edge that exists gross is smaller than the spread
plus commission it must pay, and a per-trade edge that looks respectable at
0.05% is a losing system at 0.10%. So the headline output here is not a return
figure. It is the BREAKEVEN COST: the round-trip cost at which the strategy
stops making money. If the breakeven is below what the account actually pays,
the edge is not tradable no matter how good the hit rate looks.

WHAT THIS CANNOT DO

Not tradable on this account as it stands. IBKR here serves historical
intraday bars but no live quote (reqMktData returns NaN — no streaming
subscription), so signals arrive roughly 10-15 minutes late. That is fatal to a
five-minute strategy and is a reason to treat everything here as research into
whether a subscription would be worth buying, rather than as a system waiting
to be switched on.

No overnight positions, no borrowing, and every strategy here is intentionally
simple. A simple rule that survives costs is a finding; a complex one that
survives costs is usually a curve fit.
"""
import math
from collections import defaultdict


def _sessions(frame):
    """Split an intraday frame into one group per trading day.

    Every strategy here is flat overnight, so the session is the natural unit:
    a gap between two days is not a price move any of these rules could have
    traded through.
    """
    for day, rows in frame.groupby(frame.index.date):
        if len(rows) >= 6:          # too few bars to form an opening range
            yield str(day), rows


# --- strategies -----------------------------------------------------------
#
# Each returns a list of trades: {entry_time, entry, exit_time, exit, direction}
# Gross only. Costs are applied afterwards, in one place, so a strategy cannot
# quietly assume a friendlier fill than its neighbour.

def opening_range_break(rows, *, range_bars=6):
    """Buy a break above the first N bars' high; sell a break below the low.

    The classic intraday momentum test. If intraday trends exist at all, this
    is the crudest instrument that would detect them.
    """
    if len(rows) <= range_bars + 1:
        return []
    opening = rows.iloc[:range_bars]
    high, low = float(opening["High"].max()), float(opening["Low"].min())
    rest = rows.iloc[range_bars:]

    for i in range(len(rest) - 1):
        bar = rest.iloc[i]
        if float(bar["Close"]) > high:
            return [{"direction": "long", "entry": float(bar["Close"]),
                     "entry_time": str(rest.index[i]),
                     "exit": float(rest.iloc[-1]["Close"]),
                     "exit_time": str(rest.index[-1])}]
        if float(bar["Close"]) < low:
            return [{"direction": "short", "entry": float(bar["Close"]),
                     "entry_time": str(rest.index[i]),
                     "exit": float(rest.iloc[-1]["Close"]),
                     "exit_time": str(rest.index[-1])}]
    return []


def vwap_reversion(rows, *, threshold_pct=0.4):
    """Fade a stretch away from the session VWAP, exit when it returns.

    The mean-reversion counterpart to the breakout above. Between them they
    cover both directions an intraday edge could take, so a result where
    NEITHER works is informative rather than a gap in the test.
    """
    typical = (rows["High"] + rows["Low"] + rows["Close"]) / 3
    volume = rows["Volume"].replace(0, 1)
    vwap = (typical * volume).cumsum() / volume.cumsum()

    trades, open_trade = [], None
    for i in range(len(rows)):
        price = float(rows["Close"].iloc[i])
        level = float(vwap.iloc[i])
        if not level:
            continue
        stretch = (price / level - 1) * 100

        if open_trade is None:
            if stretch <= -threshold_pct:
                open_trade = {"direction": "long", "entry": price,
                              "entry_time": str(rows.index[i])}
            elif stretch >= threshold_pct:
                open_trade = {"direction": "short", "entry": price,
                              "entry_time": str(rows.index[i])}
        else:
            back = (stretch >= 0) if open_trade["direction"] == "long" else (stretch <= 0)
            if back or i == len(rows) - 1:
                open_trade.update({"exit": price, "exit_time": str(rows.index[i])})
                trades.append(open_trade)
                open_trade = None
    return trades


def first_hour_continuation(rows, *, hour_bars=12):
    """If the first hour was up, hold long to the close; if down, hold short.

    Tests whether the day's early direction predicts the rest of it — one
    decision per day, so it is the cheapest possible intraday rule and the one
    most likely to survive costs.
    """
    if len(rows) <= hour_bars + 1:
        return []
    first = rows.iloc[:hour_bars]
    move = float(first["Close"].iloc[-1]) - float(first["Open"].iloc[0])
    if move == 0:
        return []
    rest = rows.iloc[hour_bars:]
    return [{"direction": "long" if move > 0 else "short",
             "entry": float(rest["Open"].iloc[0]),
             "entry_time": str(rest.index[0]),
             "exit": float(rest["Close"].iloc[-1]),
             "exit_time": str(rest.index[-1])}]


STRATEGIES = {
    "opening_range_break": opening_range_break,
    "vwap_reversion": vwap_reversion,
    "first_hour_continuation": first_hour_continuation,
}


# --- evaluation -----------------------------------------------------------

def _returns(trades, cost_bps):
    """Net percentage return per trade, after a round-trip cost.

    Charged as a fraction of notional on BOTH sides, which is how a spread
    actually behaves. Halving it to "one side" is the single most common way an
    intraday backtest flatters itself.
    """
    out = []
    for t in trades:
        entry, exit_price = t["entry"], t["exit"]
        if not entry:
            continue
        gross = (exit_price / entry - 1) * (1 if t["direction"] == "long" else -1)
        out.append(gross * 100 - cost_bps / 100.0)
    return out


def _stats(returns):
    n = len(returns)
    if not n:
        return {"trades": 0}
    mean = sum(returns) / n
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]
    variance = sum((r - mean) ** 2 for r in returns) / n if n > 1 else 0.0
    sd = math.sqrt(variance)
    return {
        "trades": n,
        "mean_pct": round(mean, 4),
        "total_pct": round(sum(returns), 2),
        "win_rate_pct": round(len(wins) / n * 100, 1),
        "avg_win_pct": round(sum(wins) / len(wins), 4) if wins else None,
        "avg_loss_pct": round(sum(losses) / len(losses), 4) if losses else None,
        "sd_pct": round(sd, 4),
        # Per-trade t-statistic. Below ~2 the mean is not distinguishable from
        # zero at this sample size, whatever the total looks like.
        "t_stat": round(mean / (sd / math.sqrt(n)), 2) if sd and n > 1 else None,
        "profit_factor": (round(sum(wins) / abs(sum(losses)), 2)
                          if losses and sum(losses) else None),
    }


def breakeven_cost_bps(trades):
    """Round-trip cost, in basis points, at which this strategy stops earning.

    The number the whole exercise turns on. A gross edge is easy to find at
    five-minute resolution; one that clears the spread is not. If this comes
    back below what the account actually pays, the edge is real and untradable,
    which is a finding rather than a disappointment.
    """
    gross = _returns(trades, cost_bps=0.0)
    if not gross:
        return None
    mean_gross_pct = sum(gross) / len(gross)
    return round(mean_gross_pct * 100, 2)      # percent -> basis points


def evaluate(frames, *, cost_bps=10.0, strategies=None):
    """Run every strategy over every instrument and report what survives.

    frames: {ticker: intraday OHLCV DataFrame}
    cost_bps: assumed ROUND-TRIP cost in basis points. 10 bps (0.10%) is a
      realistic retail all-in figure for liquid US equities once commission and
      half-spread on both sides are counted.
    """
    chosen = strategies or list(STRATEGIES)
    per_strategy = defaultdict(list)
    per_ticker = defaultdict(lambda: defaultdict(list))
    sessions_seen = 0

    for ticker, frame in frames.items():
        if frame is None or len(frame) == 0:
            continue
        for _day, rows in _sessions(frame):
            sessions_seen += 1
            for name in chosen:
                for trade in STRATEGIES[name](rows):
                    trade["ticker"] = ticker
                    per_strategy[name].append(trade)
                    per_ticker[name][ticker].append(trade)

    results = {}
    for name in chosen:
        trades = per_strategy[name]
        net = _stats(_returns(trades, cost_bps))
        gross = _stats(_returns(trades, 0.0))
        breakeven = breakeven_cost_bps(trades)
        results[name] = {
            "net": net, "gross": gross,
            "breakeven_cost_bps": breakeven,
            "tradable_at_cost": (breakeven is not None and breakeven > cost_bps),
            "by_ticker": {
                ticker: _stats(_returns(ts, cost_bps))
                for ticker, ts in per_ticker[name].items()
            },
        }

    return {
        "cost_bps": cost_bps,
        "instruments": len([f for f in frames.values() if f is not None and len(f)]),
        "sessions": sessions_seen,
        "results": results,
        "verdict": verdict(results, cost_bps),
    }


def verdict(results, cost_bps):
    """A plain answer, with the reason attached.

    Deliberately hard to pass. The default reading of an intraday study is "no
    edge" — that is the base rate — and a result only overturns it by clearing
    costs AND showing a mean that is statistically distinguishable from zero.
    """
    survivors = []
    for name, r in results.items():
        net, breakeven = r["net"], r["breakeven_cost_bps"]
        if not net.get("trades"):
            continue
        t = net.get("t_stat")
        if (net.get("mean_pct", 0) > 0 and breakeven and breakeven > cost_bps
                and t is not None and t >= 2.0):
            survivors.append({"strategy": name, "mean_pct": net["mean_pct"],
                              "t_stat": t, "breakeven_cost_bps": breakeven,
                              "trades": net["trades"]})

    if not survivors:
        best = max(
            ((n, r) for n, r in results.items() if r["net"].get("trades")),
            key=lambda kv: kv[1]["breakeven_cost_bps"] or -99, default=None)
        detail = ""
        if best:
            name, r = best
            detail = (f" The closest was {name}, which needed round-trip costs "
                      f"below {r['breakeven_cost_bps']} bps to break even "
                      f"against the {cost_bps} bps assumed.")
        return {
            "edge_found": False,
            "message": ("No intraday edge survived costs at a sample size worth "
                        "acting on." + detail + " On this evidence, buying an "
                        "intraday data subscription is not justified."),
            "survivors": [],
        }

    survivors.sort(key=lambda s: -s["t_stat"])
    return {
        "edge_found": True,
        "message": (f"{len(survivors)} strategy/strategies cleared costs with a "
                    f"mean distinguishable from zero (t >= 2). This is a reason "
                    f"to investigate further, NOT a reason to trade: the test is "
                    f"in-sample, ignores market impact, and this account's data "
                    f"arrives 10-15 minutes late."),
        "survivors": survivors,
    }


def fetch(symbols, config, bar_size="5 mins", duration=None, progress_cb=None):
    """Intraday bars for a list of symbols, over one held-open connection."""
    from ..providers.base import ProviderUnavailable
    from ..providers.ibkr_provider import IBKRDataProvider

    provider = IBKRDataProvider(config)
    if not provider.is_available():
        raise ProviderUnavailable(
            "IBKR is not reachable, so intraday bars cannot be fetched. Start IB "
            "Gateway or TWS and log in.")

    frames = {}
    for index, ticker in enumerate(dict.fromkeys(symbols), start=1):
        try:
            frames[ticker] = provider.intraday_history(
                ticker, bar_size=bar_size, duration=duration)
        except Exception:
            frames[ticker] = None
        if progress_cb:
            progress_cb(index, len(symbols), ticker)
    return frames
