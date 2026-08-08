"""Symbol universe + type-ahead ranking.

Regression context: searching "goldman sachs" in the watchlist box returned
"invalid ticker symbol" because the field rejected spaces, and class shares
(BRK.B) were filtered out entirely.
"""
import pytest

from assistant.providers.universe import SymbolUniverse, normalize_symbol

ROWS = [
    {"symbol": "GS", "name": "Goldman Sachs Group Inc", "type": "Common Stock"},
    {"symbol": "AAAU", "name": "Goldman Sachs Physical Gold Etf", "type": "ETP"},
    {"symbol": "GSBD", "name": "Goldman Sachs Bdc Inc", "type": "Common Stock"},
    {"symbol": "AAPL", "name": "Apple Inc", "type": "Common Stock"},
    {"symbol": "AAPI", "name": "Apple Isport Group Inc", "type": "Common Stock"},
    {"symbol": "JPM", "name": "Jpmorgan Chase & Co", "type": "Common Stock"},
    {"symbol": "JCPI", "name": "Jp Morgan Inflation Mgd Bond Etf", "type": "ETP"},
    {"symbol": "KO", "name": "Coca-Cola Co/The", "type": "Common Stock"},
    {"symbol": "BRK-B", "name": "Berkshire Hathaway Inc-Cl B", "type": "Common Stock"},
    {"symbol": "NVDA", "name": "Nvidia Corp", "type": "Common Stock"},
    {"symbol": "NVDQ", "name": "T-Rex 2X Inverse Nvidia Daily", "type": "ETP"},
]


@pytest.fixture
def uni():
    u = SymbolUniverse(finnhub_provider=None)
    u._symbols = ROWS
    return u


def top(uni, q):
    res = uni.search(q, limit=5)
    return res[0]["symbol"] if res else None


@pytest.mark.parametrize("query,expected", [
    ("goldman sachs", "GS"),      # the company, not its gold ETF
    ("goldman", "GS"),
    ("GS", "GS"),                 # exact ticker wins
    ("apple", "AAPL"),            # primary listing, not AAPI
    ("jp morgan", "JPM"),         # space-insensitive: name is "JPMORGAN"
    ("jpmorgan", "JPM"),
    ("coca cola", "KO"),          # punctuation-insensitive: "COCA-COLA"
    ("berkshire", "BRK-B"),       # class shares are not filtered out
    ("nvid", "NVDA"),             # ordinary share beats leveraged ETP
])
def test_ranking_puts_the_obvious_answer_first(uni, query, expected):
    assert top(uni, query) == expected


def test_empty_query_returns_nothing(uni):
    assert uni.search("") == [] and uni.search("   ") == []


def test_no_match_returns_empty(uni):
    assert uni.search("zzzznotacompany") == []


def test_limit_is_respected(uni):
    assert len(uni.search("g", limit=2)) <= 2


def test_common_stock_outranks_fund_on_equal_match(uni):
    """A leveraged ETP must not outrank the company it tracks."""
    syms = [r["symbol"] for r in uni.search("nvidia", limit=5)]
    if "NVDA" in syms and "NVDQ" in syms:
        assert syms.index("NVDA") < syms.index("NVDQ")


@pytest.mark.parametrize("raw,expected", [
    ("BRK.B", "BRK-B"),     # Yahoo uses dashes; a dot silently returns no data
    ("BF.A", "BF-A"),
    ("aapl", "AAPL"),
    (" gs ", "GS"),
    (None, ""),
])
def test_symbol_normalization(raw, expected):
    assert normalize_symbol(raw) == expected


def test_refresh_filters_junk_but_keeps_class_shares():
    class FakeFinnhub:
        def _get(self, path, params):
            return [
                {"symbol": "BRK.B", "description": "BERKSHIRE HATHAWAY INC-CL B", "type": "Common Stock"},
                {"symbol": "USB.PRP", "description": "US BANCORP PREFERRED", "type": "PUBLIC"},
                {"symbol": "BWIV.WS", "description": "BLUE WATER WARRANT", "type": "Equity WRT"},
                {"symbol": "BEBE.U", "description": "TGE VALUE UNIT", "type": "Unit"},
                {"symbol": "AAPL", "description": "APPLE INC", "type": "Common Stock"},
            ]

    u = SymbolUniverse(FakeFinnhub())
    u._save_cache = lambda rows: None      # don't touch disk in tests
    rows = u.refresh()
    syms = {r["symbol"] for r in rows}
    assert syms == {"BRK-B", "AAPL"}, "preferreds/warrants/units must be excluded"
