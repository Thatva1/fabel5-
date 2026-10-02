"""Build a UK and European share universe from Yahoo's screener.

    python tools/build_europe_universe.py

Writes reports/universe-europe.csv in the format tools/full_study.py reads
(STUDY_UNIVERSE). The rules are mechanical and fixed here so the list cannot be
shaped by what a strategy later does on it:

  * listed on the exchange and quoted in its own currency
  * a EUROPEAN company. Frankfurt quotes Nvidia, Apple and Amazon in euros and
    London quotes Toyota and BHP in pence; those are US, Japanese and
    Australian shares and belong to a different test
  * an operating company. Yahoo files investment trusts under the industry
    "Asset Management", so that industry is excluded whole; it costs a handful
    of genuine fund managers and removes several dozen closed-end funds whose
    prices follow a portfolio rather than a business
  * at least MIN_TRADED of average daily traded value, in millions of the
    local currency, so the names are ones a book could actually hold
  * one line per company across the whole list: where a company has two share
    classes, or a second listing in another country, the most traded line
  * then the largest QUOTA names per exchange by market capitalisation

It is today's list, so it carries survivorship bias exactly as the US study's
universe does: a company that was delisted or went bust is not in it.
"""
import csv
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yfinance as yf                      # noqa: E402
from yfinance import EquityQuery as Q      # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports",
                   "universe-europe.csv")

# region, Yahoo exchange code, ticker suffix, quoted currency, how many to keep
EXCHANGES = [
    ("gb", "LSE", ".L", "GBp", 300),
    ("de", "GER", ".DE", "EUR", 100),
    ("fr", "PAR", ".PA", "EUR", 100),
    ("ch", "EBS", ".SW", "CHF", 50),
    ("it", "MIL", ".MI", "EUR", 50),
    ("se", "STO", ".ST", "SEK", 50),
    ("es", "MCE", ".MC", "EUR", 40),
    ("nl", "AMS", ".AS", "EUR", 40),
    ("dk", "CPH", ".CO", "DKK", 25),
    ("fi", "HEL", ".HE", "EUR", 25),
    ("no", "OSL", ".OL", "NOK", 25),
    ("be", "BRU", ".BR", "EUR", 20),
    ("at", "VIE", ".VI", "EUR", 15),
    ("pt", "LIS", ".LS", "EUR", 10),
]
# Average daily traded value, in millions of the quoted currency's major unit.
MIN_TRADED = {"GBp": 1.0, "EUR": 1.0, "CHF": 1.0, "SEK": 10.0, "DKK": 7.5, "NOK": 10.0}
PAGE = 250
PAGES = 6


EUROPE = {"United Kingdom", "Ireland", "Germany", "France", "Netherlands",
          "Switzerland", "Italy", "Sweden", "Spain", "Denmark", "Finland", "Norway",
          "Belgium", "Austria", "Portugal", "Luxembourg", "Jersey", "Guernsey",
          "Isle of Man"}
# Candidates looked up per exchange before the domicile filter. Generous,
# because on some exchanges most of the largest lines are foreign.
LOOKUP_MULTIPLE = 4


def profile(symbol):
    """(symbol, country, company name, sector, industry) from Yahoo, or None."""
    for attempt in range(3):
        try:
            info = yf.Ticker(symbol).info
            name = (info.get("longName") or info.get("shortName") or "").strip()
            if name:
                return (symbol, info.get("country"), name, info.get("sector"),
                        info.get("industry"))
        except Exception:
            pass
        time.sleep(2 * (attempt + 1))
    return None


def profiles_for(symbols):
    """Company profiles, cached on disk because Yahoo throttles a burst of 1,400
    lookups and the ones it refuses are the ones asked for last — which would
    silently empty whichever exchanges happen to be at the end of the list."""
    import json
    import tempfile
    from concurrent.futures import ThreadPoolExecutor

    cache_path = os.path.join(tempfile.gettempdir(), "europe_universe_profiles.json")
    try:
        with open(cache_path) as handle:
            cache = json.load(handle)
    except (OSError, ValueError):
        cache = {}
    for attempt, workers in enumerate((6, 2, 1)):
        missing = [s for s in symbols if s not in cache]
        if not missing:
            break
        if attempt:
            print(f"  retrying {len(missing)} lookups Yahoo refused", flush=True)
            time.sleep(20)
        with ThreadPoolExecutor(workers) as pool:
            for found in pool.map(profile, missing):
                if found:
                    cache[found[0]] = list(found)
        with open(cache_path, "w") as handle:
            json.dump(cache, handle)
    return {s: cache[s] for s in symbols if s in cache}


def screen(region, exchange):
    query = Q("and", [Q("eq", ["region", region]), Q("eq", ["exchange", exchange])])
    rows = []
    for page in range(PAGES):
        for attempt in range(3):
            try:
                result = yf.screen(query, sortField="intradaymarketcap", sortAsc=False,
                                   size=PAGE, offset=page * PAGE)
                break
            except Exception:
                time.sleep(3 * (attempt + 1))
        else:
            break
        quotes = result.get("quotes") or []
        rows.extend(quotes)
        if len(quotes) < PAGE:
            break
        time.sleep(0.5)
    return rows


def main():
    shortlist = []
    for region, exchange, suffix, currency, quota in EXCHANGES:
        rows = []
        for quote in screen(region, exchange):
            symbol = quote.get("symbol") or ""
            if not symbol.endswith(suffix) or quote.get("quoteType") != "EQUITY":
                continue
            if quote.get("currency") != currency:
                continue
            price = quote.get("regularMarketPrice") or 0
            volume = quote.get("averageDailyVolume3Month") or 0
            traded = price * volume / (100.0 if currency == "GBp" else 1.0) / 1e6
            cap = quote.get("marketCap") or 0
            if traded < MIN_TRADED[currency] or cap <= 0:
                continue
            rows.append({"symbol": symbol, "asset_class": "equity", "name": "",
                         "exchange": exchange, "currency": currency,
                         "market_cap_m": round(cap / 1e6), "traded_m": round(traded, 2),
                         "quota": quota})
        rows = sorted(rows, key=lambda r: -r["market_cap_m"])[:quota * LOOKUP_MULTIPLE]
        print(f"{exchange}: {len(rows)} liquid lines to look up", flush=True)
        shortlist.extend(rows)

    profiles = profiles_for([r["symbol"] for r in shortlist])
    print(f"looked up {len(profiles)} of {len(shortlist)} companies", flush=True)

    # One line per company across every exchange: the most traded one. Traded
    # value is compared in each line's own currency, which is good enough to
    # tell a home listing from a thin foreign one and is all it is used for.
    by_company, dropped = {}, {"foreign": 0, "fund": 0, "unknown": 0, "duplicate": 0}
    for row in shortlist:
        found = profiles.get(row["symbol"])
        if not found or not found[2]:
            dropped["unknown"] += 1
            continue
        _, country, name, sector, industry = found
        if country not in EUROPE:
            dropped["foreign"] += 1
            continue
        if not sector or industry == "Asset Management":
            dropped["fund"] += 1
            continue
        row = {**row, "name": name, "country": country}
        current = by_company.get(name)
        if current is None or row["traded_m"] > current["traded_m"]:
            dropped["duplicate"] += current is not None
            by_company[name] = row
        else:
            dropped["duplicate"] += 1
    print(f"dropped: {dropped}", flush=True)

    chosen = []
    for _, exchange, _, _, quota in EXCHANGES:
        rows = sorted((r for r in by_company.values() if r["exchange"] == exchange),
                      key=lambda r: -r["market_cap_m"])[:quota]
        print(f"{exchange}: {len(rows)} kept", flush=True)
        chosen.extend(rows)

    fields = ["symbol", "asset_class", "name", "exchange", "country", "currency",
              "market_cap_m", "traded_m"]
    with open(OUT, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(chosen)
    print(f"wrote {len(chosen)} instruments to {os.path.normpath(OUT)}")


if __name__ == "__main__":
    main()
