"""The Strategy interface every strategy module implements.

Contract, in one sentence: a strategy is handed measured numbers and returns
zero or more StrategyIdeas, each carrying its own entry, stop and target and a
plain-English list of the conditions that were met.

Rules a strategy must obey:

  * It computes NOTHING about money. No share counts, no position values, no
    risk amounts. It proposes price LEVELS; assistant/risk/sizing.py turns those
    into a size and assistant/risk/gate.py decides whether it is allowed.
  * It never calls an API. Everything it needs arrives in the context object,
    so every strategy is testable with a dict and a DataFrame.
  * Every idea explains itself. `reasons` is what the user reads to understand
    why the setup fired; it is not decoration.
  * No stop, no idea. An idea without a valid stop is dropped, because an
    undefined-risk trade cannot be sized or gated.
"""
from dataclasses import asdict, dataclass, field
from typing import List, Optional

LONG = "long"
SHORT = "short"

# An actionable idea has a direction and levels and can become a trade plan.
# A watch idea is a heads-up with no direction yet (a squeeze before it breaks);
# it goes in the journal and the dashboard but never becomes a plan.
STATUS_ACTIONABLE = "actionable"
STATUS_WATCH = "watch"


@dataclass
class StrategyContext:
    """Everything a strategy is allowed to see."""
    ticker: str
    df: object                    # price history DataFrame
    snapshot: dict                # scanner.scan_ticker output
    regime: dict                  # regime.classify output
    params: dict = field(default_factory=dict)   # this strategy's settings
    config: dict = field(default_factory=dict)   # full config, read-only
    # The rest of the universe, for strategies whose signal is a RANK rather
    # than a level (see cross_section.py). None when only one instrument is
    # being looked at — analysing a single ticker on the dashboard, or a
    # single-name backtest. A cross-sectional strategy must produce nothing in
    # that case rather than substitute an absolute threshold, which would be a
    # different strategy answering to the same name.
    cross_section: object = None
    # The configured benchmark's close series, for beta and for the market-trend
    # overlay. Already truncated to this bar by the backtest; strategies
    # truncate it again themselves so the guarantee is local to where it matters.
    benchmark_closes: object = None

    @property
    def price(self):
        return self.snapshot.get("price")

    @property
    def atr(self):
        return self.snapshot.get("atr")


@dataclass
class StrategyIdea:
    """One setup, tagged with what produced it.

    strategy + regime are carried all the way into the journal so hit rate can
    later be reported per strategy per regime — the feedback loop that shows
    which rules to keep, cut, or size up.
    """
    ticker: str
    strategy: str
    strategy_label: str
    regime: str
    direction: Optional[str] = None
    status: str = STATUS_ACTIONABLE
    entry: Optional[float] = None
    entry_zone: Optional[List[float]] = None
    stop: Optional[float] = None
    target: Optional[float] = None
    risk_per_share: Optional[float] = None
    reward_risk: Optional[float] = None
    headline: str = ""
    reasons: List[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


class Strategy:
    """Subclass this, set the class attributes, implement detect()."""

    name = ""            # stable machine id, used in config and the journal
    label = ""           # short human name for the dashboard
    description = ""     # one plain-English sentence: what this strategy does
    regimes = ()         # regimes in which this strategy is allowed to run
    defaults = {}        # tunable settings, overridable per strategy in config

    def params_for(self, config):
        """This strategy's settings: defaults with the user's config.yaml
        `strategies.<name>` block layered on top."""
        block = ((config or {}).get("strategies") or {}).get(self.name) or {}
        overrides = {k: v for k, v in block.items() if k != "enabled"}
        return {**self.defaults, **overrides}

    def enabled(self, config):
        block = ((config or {}).get("strategies") or {}).get(self.name) or {}
        return bool(block.get("enabled", True))

    def detect(self, ctx: StrategyContext) -> List[StrategyIdea]:
        raise NotImplementedError

    # -- helpers shared by every strategy ---------------------------------

    def build(self, ctx, direction, entry, stop, target, reasons,
              headline="", entry_zone=None, meta=None):
        """Assemble a validated idea, or None if the levels don't make sense.

        Validation is deliberate and strict: a long whose stop sits above entry,
        or whose target sits below it, is an arithmetic mistake somewhere in the
        strategy, and silently passing it downstream would produce a negative
        risk-per-share and a nonsense position size.
        """
        levels = _validate_levels(direction, entry, stop, target)
        if levels is None:
            return None
        entry, stop, target, risk_per_share = levels
        return StrategyIdea(
            ticker=ctx.ticker,
            strategy=self.name,
            strategy_label=self.label,
            regime=ctx.regime.get("regime"),
            direction=direction,
            status=STATUS_ACTIONABLE,
            entry=entry,
            entry_zone=[round(float(v), 2) for v in entry_zone] if entry_zone else [entry, entry],
            stop=stop,
            target=target,
            risk_per_share=risk_per_share,
            reward_risk=round(abs(target - entry) / risk_per_share, 2),
            headline=headline or f"{self.label}: {direction}",
            reasons=_dedupe(reasons),
            meta=dict(meta or {}),
        )

    def watch(self, ctx, headline, reasons, meta=None):
        """A heads-up with no direction yet. Never becomes a trade plan."""
        return StrategyIdea(
            ticker=ctx.ticker,
            strategy=self.name,
            strategy_label=self.label,
            regime=ctx.regime.get("regime"),
            direction=None,
            status=STATUS_WATCH,
            headline=headline,
            reasons=_dedupe(reasons),
            meta=dict(meta or {}),
        )


def _dedupe(reasons):
    """Drop repeated lines while keeping the original order.

    A strategy's reasons start with the regime's own explanation and then add
    its checks, so a strategy that restates something the regime already said
    (the prevailing trend bias, typically) would print it twice. The user reads
    this list as a checklist; a duplicate line makes it look like two separate
    pieces of evidence when there is only one.
    """
    seen, out = set(), []
    for reason in reasons:
        key = " ".join(str(reason).split()).lower()
        if key and key not in seen:
            seen.add(key)
            out.append(reason)
    return out


def _validate_levels(direction, entry, stop, target):
    """Return (entry, stop, target, risk_per_share) rounded, or None if invalid."""
    try:
        entry, stop, target = float(entry), float(stop), float(target)
    except (TypeError, ValueError):
        return None
    if not all(v > 0 for v in (entry, stop, target)):
        return None

    entry, stop, target = round(entry, 2), round(stop, 2), round(target, 2)
    if direction == LONG:
        if not (stop < entry < target):
            return None
        risk_per_share = round(entry - stop, 2)
    elif direction == SHORT:
        if not (target < entry < stop):
            return None
        risk_per_share = round(stop - entry, 2)
    else:
        return None

    # A sub-penny stop distance makes position size explode; treat it as no setup.
    if risk_per_share <= 0:
        return None
    return entry, stop, target, risk_per_share
