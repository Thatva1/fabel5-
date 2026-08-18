"""Turn the live book into a static site.

Every figure here is read from the same files the dashboard reads, so the
public page cannot drift from the private one. Nothing is recomputed and
nothing is rounded up on the way out.
"""
import html
import json
import os
from datetime import datetime, timezone


def snapshot(config, include_positions=False):
    """Everything the public site shows, as one plain dict."""
    from ..paper import closed_archive, tradelog
    from ..paper.book import Book

    book = Book.load()
    if not book.started:
        return {"started": False,
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    summary = book.summary()
    journal = tradelog.report(book)

    out = {
        "started": True,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": summary,
        "daily": book.daily[-90:][::-1],
        "curve": book.curve[-250:],
        "journal": journal["entries"][:50],
        "breakdown": journal["breakdown"],
        "observations": journal["observations"],
        "totals": journal["totals"],
        "archive": closed_archive.stats(),
        "price_source": book.price_sources(),
        # Named on the page rather than left to be inferred. A reader who does
        # not know these are simulated could reasonably assume otherwise.
        "disclosure": {
            "simulated": True,
            "orders_placed": 0,
            "broker": "none — no order has ever been placed by this system",
        },
    }
    if include_positions:
        out["positions"] = [
            {k: p.get(k) for k in ("ticker", "direction", "shares", "entry_price",
                                   "last_price", "strategy", "entry_date")}
            for p in book.positions]
    return out


def _fmt(value, digits=2):
    if value is None:
        return "—"
    return f"{value:,.{digits}f}"


def _rows(items, columns):
    body = []
    for item in items:
        cells = []
        for key, digits, css in columns:
            raw = item.get(key)
            text = _fmt(raw, digits) if isinstance(raw, (int, float)) else html.escape(str(raw or "—"))
            cls = css
            if css == "num" and isinstance(raw, (int, float)):
                cls = "num " + ("up" if raw > 0 else "down" if raw < 0 else "")
            cells.append(f'<td class="{cls}">{text}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    return "\n".join(body)


def render_html(data, title="Trade Assistant"):
    """A single self-contained page. No scripts, no external requests.

    Deliberately inert: a static page cannot be made to place an order, leak a
    key, or run up an API bill however it is reached.
    """
    if not data.get("started"):
        return f"""<!doctype html><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
max-width:52rem;margin:4rem auto;padding:0 1.5rem;color:#1a2233;line-height:1.6}}</style>
<h1>{html.escape(title)}</h1>
<p>No paper book has been started yet, so there is nothing to report.</p>
<p><small>Generated {html.escape(data.get('generated_at', ''))}</small></p>"""

    s = data["summary"]
    t = data["totals"]
    ccy = s.get("base_currency", "USD")
    ret = s.get("return_pct")
    ret_cls = "up" if (ret or 0) > 0 else "down" if (ret or 0) < 0 else ""

    daily_rows = _rows(data["daily"], [
        ("date", 0, ""), ("change", 2, "num"), ("realised", 2, "num"),
        ("unrealised_change", 2, "num"), ("equity", 2, "num"),
        ("open_positions", 0, "num")])

    strat_rows = _rows(data["breakdown"]["by_strategy"], [
        ("key", 0, ""), ("trades", 0, "num"), ("win_rate_pct", 1, "num"),
        ("pnl", 2, "num"), ("avg_r", 2, "num")])

    journal_html = "".join(f"""
      <article class="trade">
        <header>
          <b>{html.escape(str(e.get('ticker')))}</b>
          <span class="tag">{html.escape(str(e.get('direction') or '').upper())}</span>
          <span class="tag">{html.escape(str(e.get('strategy') or ''))}</span>
          <span class="pnl {'up' if e.get('won') else 'down'}">{_fmt(e.get('pnl'))} {html.escape(ccy)}
            {f"· {e['r_multiple']}R" if e.get('r_multiple') is not None else ""}</span>
        </header>
        <div class="dates">{html.escape(str(e.get('entry_date') or ''))} →
          {html.escape(str(e.get('exit_date') or ''))} · {html.escape(str(e.get('outcome_title') or ''))}</div>
        <div class="why">
          <div><h4>Why it was opened</h4><ul>{''.join(f'<li>{html.escape(x)}</li>' for x in e.get('why_entered') or [])}</ul></div>
          <div><h4>Why this outcome</h4><ul>{''.join(f'<li>{html.escape(x)}</li>' for x in e.get('why_this_outcome') or [])}</ul></div>
        </div>
      </article>""" for e in data["journal"])

    observations = "".join(
        f"<li>{html.escape(o)}</li>" for o in data.get("observations") or [])

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>{html.escape(title)}</title>
<style>
  :root {{ --ink:#141b2d; --muted:#5d6b85; --line:#e3e8f0; --bg:#fbfcfe;
           --up:#1f7a4d; --down:#b03a2e; --accent:#1e2761; }}
  * {{ box-sizing:border-box }}
  body {{ font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
         margin:0; background:var(--bg); color:var(--ink); line-height:1.55 }}
  .wrap {{ max-width:70rem; margin:0 auto; padding:2.5rem 1.5rem 5rem }}
  h1 {{ font-size:1.9rem; margin:0 0 .2rem }}
  h2 {{ font-size:1.15rem; margin:2.5rem 0 .75rem; letter-spacing:.02em }}
  h4 {{ font-size:.78rem; text-transform:uppercase; letter-spacing:.09em;
        color:var(--muted); margin:0 0 .3rem }}
  .sub {{ color:var(--muted); margin:0 0 2rem }}
  .stats {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(9rem,1fr));
            gap:1rem; margin:1.5rem 0 }}
  .stat {{ background:#fff; border:1px solid var(--line); border-radius:8px; padding:.9rem 1rem }}
  .stat .v {{ font-size:1.45rem; font-weight:600; font-variant-numeric:tabular-nums }}
  .stat .l {{ font-size:.72rem; text-transform:uppercase; letter-spacing:.08em; color:var(--muted) }}
  table {{ width:100%; border-collapse:collapse; font-size:.88rem; background:#fff;
           border:1px solid var(--line); border-radius:8px; overflow:hidden }}
  th {{ text-align:left; font-size:.72rem; text-transform:uppercase; letter-spacing:.07em;
        color:var(--muted); padding:.6rem .7rem; border-bottom:1px solid var(--line) }}
  td {{ padding:.55rem .7rem; border-bottom:1px solid var(--line);
        font-variant-numeric:tabular-nums }}
  td.num, th.num {{ text-align:right }}
  .up {{ color:var(--up) }} .down {{ color:var(--down) }}
  .scroll {{ overflow-x:auto }}
  .trade {{ background:#fff; border:1px solid var(--line); border-radius:8px;
            padding:1rem 1.1rem; margin-bottom:.8rem }}
  .trade header {{ display:flex; gap:.55rem; align-items:baseline; flex-wrap:wrap }}
  .tag {{ font-size:.72rem; background:#eef2f9; border-radius:99px; padding:.1rem .55rem; color:var(--muted) }}
  .pnl {{ margin-left:auto; font-weight:600; font-variant-numeric:tabular-nums }}
  .dates {{ font-size:.78rem; color:var(--muted); margin:.3rem 0 .7rem }}
  .why {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(17rem,1fr)); gap:1.2rem }}
  .why ul {{ margin:0; padding-left:1.1rem; font-size:.85rem }}
  .notice {{ background:#fff8ec; border:1px solid #f0dcb8; border-radius:8px;
             padding:1rem 1.1rem; margin:1.5rem 0 }}
  footer {{ margin-top:3.5rem; padding-top:1.2rem; border-top:1px solid var(--line);
            color:var(--muted); font-size:.8rem }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --ink:#e7ecf5; --muted:#93a1bd; --line:#27304a; --bg:#0f1526;
             --up:#5fbf8d; --down:#e0705f; }}
    .stat, table, .trade {{ background:#161d33 }}
    th {{ border-bottom-color:var(--line) }}
    .tag {{ background:#212a45 }}
    .notice {{ background:#2a2415; border-color:#4a3d1e }}
  }}
</style></head><body><div class="wrap">

<h1>{html.escape(title)}</h1>
<p class="sub">Systematic paper trading — the record of what the rules did.</p>

<div class="notice">
  <b>Simulated results.</b> This is a paper book. No order has ever been placed
  by this system, and no broker account is connected to this page. Past
  behaviour is not predictive. Nothing here is financial advice, or an offer or
  solicitation to buy or sell any security.
</div>

<div class="stats">
  <div class="stat"><div class="v">{_fmt(s.get('equity'))}</div><div class="l">Equity ({html.escape(ccy)})</div></div>
  <div class="stat"><div class="v {ret_cls}">{_fmt(ret)}%</div><div class="l">Return</div></div>
  <div class="stat"><div class="v">{s.get('open_positions', 0)}</div><div class="l">Open positions</div></div>
  <div class="stat"><div class="v">{t.get('closed', 0)}</div><div class="l">Closed trades</div></div>
  <div class="stat"><div class="v">{_fmt(t.get('win_rate_pct'), 1)}%</div><div class="l">Win rate</div></div>
  <div class="stat"><div class="v">{_fmt(s.get('max_drawdown_pct'), 2)}%</div><div class="l">Max drawdown</div></div>
</div>

<h2>Daily profit and loss</h2>
<p class="sub" style="margin:0 0 .8rem">Realised is money banked by trades that closed
that day. Unrealised is the drift on positions still open. They are never added
together — a day can be up while every trade it closed lost money.</p>
<div class="scroll"><table>
<thead><tr><th>Date</th><th class="num">Day P&amp;L</th><th class="num">Realised</th>
<th class="num">Unrealised</th><th class="num">Equity</th><th class="num">Open</th></tr></thead>
<tbody>{daily_rows or '<tr><td colspan="6">No days recorded yet.</td></tr>'}</tbody></table></div>

<h2>Where the money is made and lost</h2>
<div class="scroll"><table>
<thead><tr><th>Strategy</th><th class="num">Trades</th><th class="num">Win %</th>
<th class="num">P&amp;L</th><th class="num">Avg R</th></tr></thead>
<tbody>{strat_rows or '<tr><td colspan="5">No closed trades yet.</td></tr>'}</tbody></table></div>
{f'<ul class="sub" style="margin-top:1rem">{observations}</ul>' if observations else ''}

<h2>Trade journal</h2>
<p class="sub" style="margin:0 0 1rem">Entry reasons are quoted from what the strategy
recorded before each trade was opened. Outcomes are mechanical — which level was
touched. Nothing is narrated after the fact.</p>
{journal_html or '<p class="sub">No trades have closed yet.</p>'}

<footer>
  Generated {html.escape(data.get('generated_at', ''))} ·
  priced by {html.escape(", ".join(data.get("price_source") or []) or "—")} ·
  {data['archive'].get('trades', 0)} trades in the archive.<br>
  Research and decision-support only. Simulated results. Not financial advice,
  and not an offer or solicitation to buy or sell any security.
</footer>
</div></body></html>"""


def write(out_dir, config, include_positions=False, title="Trade Assistant"):
    """Write index.html and data.json into `out_dir`. Returns the paths."""
    data = snapshot(config, include_positions=include_positions)
    os.makedirs(out_dir, exist_ok=True)

    html_path = os.path.join(out_dir, "index.html")
    json_path = os.path.join(out_dir, "data.json")
    with open(html_path, "w") as handle:
        handle.write(render_html(data, title=title))
    with open(json_path, "w") as handle:
        json.dump(data, handle, indent=2, default=str)
    return {"html": html_path, "json": json_path, "data": data}
