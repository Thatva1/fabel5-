"""Tradable symbol universe — every US-listed symbol, not a curated watchlist.

The full list (~30k symbols) comes from Finnhub's free `stock/symbol` endpoint
and is cached to disk, so autocomplete is instant and offline-capable rather
than hitting the network on every keystroke.

The watchlist is now only "the symbols I've chosen to scan". Searching is not
limited to it — you can look up anything in the universe.
"""
import json
import os
import time

from ..core.config import DATA_DIR
from .base import ProviderUnavailable

CACHE_PATH = os.path.join(DATA_DIR, "symbols_us.json")
CACHE_MAX_AGE = 7 * 24 * 3600          # refresh weekly; listings change slowly
# Ordinary tradable equities and funds. Excludes warrants/rights/units, which
# would otherwise clutter autocomplete for a retail equity trader.
KEEP_TYPES = {"Common Stock", "ETP", "ETF", "ADR", "REIT", "Equity"}


def normalize_symbol(symbol):
    """Class shares are 'BRK.B' at Finnhub but 'BRK-B' at Yahoo, and price
    lookups fail silently on the wrong form. Store the Yahoo form so a symbol
    picked from autocomplete actually resolves."""
    return (symbol or "").strip().upper().replace(".", "-")


class SymbolUniverse:
    def __init__(self, finnhub_provider):
        self.finnhub = finnhub_provider
        self._symbols = None

    # ---- loading ----

    def _load_cache(self):
        try:
            if not os.path.exists(CACHE_PATH):
                return None
            if time.time() - os.path.getmtime(CACHE_PATH) > CACHE_MAX_AGE:
                return None
            with open(CACHE_PATH) as f:
                rows = json.load(f)
            # A cache written before exchange codes were kept is missing the one
            # field that makes a wide screen affordable. Treat it as stale
            # rather than silently returning rows the caller cannot filter.
            if rows and "mic" not in rows[0]:
                return None
            return rows
        except Exception:
            return None

    def _save_cache(self, rows):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(CACHE_PATH, "w") as f:
                json.dump(rows, f)
        except Exception:
            pass

    def refresh(self):
        """Pull the full US symbol list from Finnhub and cache it."""
        raw = self.finnhub._get("stock/symbol", {"exchange": "US"})
        if not isinstance(raw, list) or not raw:
            raise ProviderUnavailable("finnhub: empty symbol list")
        rows = []
        for s in raw:
            sym, desc = s.get("symbol"), s.get("description")
            if not sym or not desc:
                continue
            # The type filter alone removes preferreds/warrants/units
            # (USB.PRP, BWIV.WS, BEBE.U) while keeping legitimate class shares
            # like BRK.A / BRK.B, which are Common Stock.
            if s.get("type") and s["type"] not in KEEP_TYPES:
                continue
            # `mic` is the exchange. Keeping it costs nothing and is the
            # difference between a tradable universe and a 17,608-symbol
            # over-the-counter tail: screening the full list without it spends
            # the entire rate limit on foreign OTC shells nobody can trade.
            rows.append({"symbol": normalize_symbol(sym), "name": desc.title(),
                         "type": s.get("type") or "Equity",
                         "mic": s.get("mic") or ""})
        rows.sort(key=lambda r: r["symbol"])
        self._symbols = rows
        self._save_cache(rows)
        return rows

    def symbols(self):
        if self._symbols is None:
            self._symbols = self._load_cache()
        if self._symbols is None:
            try:
                self.refresh()
            except Exception:
                self._symbols = []          # degrade to yfinance-only search
        return self._symbols or []

    # ---- search ----

    # Ordinary shares outrank funds/ETFs for a name query: searching "goldman
    # sachs" means the company, not its gold ETF.
    _PRIMARY_TYPES = {"Common Stock", "Equity", "ADR", "REIT"}

    @staticmethod
    def _compact(text):
        """Strip spaces and punctuation so 'jp morgan' can match 'JPMORGAN'
        and 'coca cola' can match 'COCA-COLA'."""
        return "".join(c for c in text if c.isalnum())

    def _score(self, row, q, q_compact=None):
        """Lower is better. None = not a match."""
        sym, name = row["symbol"], row["name"].upper()
        is_fund = row.get("type") not in self._PRIMARY_TYPES
        # Shorter symbols and shorter names indicate the primary listing
        # (AAPL "Apple Inc" should beat AAPI "Apple Isport Group Inc").
        tiebreak = len(sym) * 2 + min(len(name), 60) / 60
        fund_penalty = 40 if is_fund else 0

        if sym == q:
            return 0 + tiebreak * 0.01
        if sym.startswith(q):
            return 100 + fund_penalty * 0.25 + tiebreak
        if name.startswith(q):
            return 200 + fund_penalty + tiebreak
        # Word-boundary hit ranks above a mid-word substring
        if len(q) >= 3:
            if any(w.startswith(q) for w in name.split()):
                return 300 + fund_penalty + tiebreak
            # Punctuation/space-insensitive prefix ("jp morgan" -> "JPMORGAN")
            if q_compact and len(q_compact) >= 3:
                if self._compact(name).startswith(q_compact):
                    return 210 + fund_penalty + tiebreak
            if q in name:
                return 400 + fund_penalty + tiebreak
            if q_compact and len(q_compact) >= 4 and q_compact in self._compact(name):
                return 420 + fund_penalty + tiebreak
        return None

    def search(self, query, limit=8):
        """Rank matches for type-ahead: exact ticker first, then ticker prefix,
        then name matches — with ordinary shares preferred over funds so a
        company name finds the company."""
        q = (query or "").strip().upper()
        if not q:
            return []
        q_compact = self._compact(q)
        scored = []
        for r in self.symbols():
            s = self._score(r, q, q_compact)
            if s is not None:
                scored.append((s, r))
        scored.sort(key=lambda t: t[0])
        return [r for _, r in scored[:limit]]
