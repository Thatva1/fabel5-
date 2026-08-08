"""Shared data shapes.

The central idea: every figure that reaches the thesis engine or dashboard is a
Fact carrying its value, source, timestamp, and freshness label, so the LLM can
cite it and the user can audit it.
"""
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Optional

FRESH_LIVE = "live"
FRESH_DELAYED = "delayed"
FRESH_EOD = "eod"


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Fact:
    value: object
    source: str
    timestamp: str            # ISO 8601, when the datum is *as of*
    freshness: str            # live | delayed | eod
    unit: Optional[str] = None

    def to_dict(self):
        return {k: v for k, v in asdict(self).items() if v is not None}


def fact(value, source, freshness, timestamp=None, unit=None):
    if value is None:
        return None
    return Fact(value=value, source=source, freshness=freshness,
                timestamp=timestamp or utcnow(), unit=unit).to_dict()


@dataclass
class OrderIntent:
    """What Layer 2 would receive for a human-confirmed, human-initiated order.
    Defined now so the Broker seam is concrete; nothing in Layer 1 creates one
    except the PaperBroker refusal test."""
    ticker: str
    side: str                 # buy | sell
    quantity: int
    order_type: str = "limit"
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None   # attached protective stop (bracket child)
    currency: str = "USD"
    idea_id: Optional[int] = None
    note: str = ""
    created_at: str = field(default_factory=utcnow)

    def to_dict(self):
        return asdict(self)
