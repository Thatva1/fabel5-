"""Idea journal — SQLite log of every idea, your decision on it, and its
eventual outcome, so hit-rate can be tracked over time."""
import json
import os
import sqlite3
from datetime import datetime, timezone

from .core.config import DATA_DIR
from .risk import pnl as pnl_math

DB_PATH = os.path.join(DATA_DIR, "journal.db")

ORDERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idea_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL,            -- pending_confirmation | submitted | cancelled | error
    side TEXT NOT NULL,
    ticker TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    limit_price REAL NOT NULL,
    stop_price REAL,
    currency TEXT NOT NULL,
    max_loss REAL,
    ib_order_id INTEGER,
    detail TEXT
);
"""

# Columns added after the orders table shipped; applied on every connect so an
# existing journal.db upgrades in place.
ORDER_MIGRATIONS = (
    ("stop_price", "ALTER TABLE orders ADD COLUMN stop_price REAL"),
)

# Same in-place upgrade for ideas: strategy/regime tagging arrived after the
# journal already held history. Old rows keep NULLs and are reported as
# 'untagged' rather than being back-filled with a guess.
IDEA_MIGRATIONS = (
    ("strategy", "ALTER TABLE ideas ADD COLUMN strategy TEXT"),
    ("strategy_label", "ALTER TABLE ideas ADD COLUMN strategy_label TEXT"),
    ("regime", "ALTER TABLE ideas ADD COLUMN regime TEXT"),
    ("status", "ALTER TABLE ideas ADD COLUMN status TEXT"),
    # risk_amount is in the INSTRUMENT's currency, so summing it across a
    # portfolio of UK, US and European names produces a meaningless number.
    # risk_base is the same figure converted to your base currency at the time
    # the idea was created, which is the only version safe to add up.
    ("risk_base", "ALTER TABLE ideas ADD COLUMN risk_base REAL"),
    # Realised outcome. pnl_instrument is in the instrument's currency and
    # pnl_base is the same figure converted at the rate on the day you closed —
    # the rate is stored alongside so the number can be audited later, since
    # re-converting at today's rate would silently rewrite past history.
    ("pnl_instrument", "ALTER TABLE ideas ADD COLUMN pnl_instrument REAL"),
    ("pnl_base", "ALTER TABLE ideas ADD COLUMN pnl_base REAL"),
    ("exit_fx_rate", "ALTER TABLE ideas ADD COLUMN exit_fx_rate REAL"),
    ("r_multiple", "ALTER TABLE ideas ADD COLUMN r_multiple REAL"),
    ("entry_used", "ALTER TABLE ideas ADD COLUMN entry_used REAL"),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS ideas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    ticker TEXT NOT NULL,
    direction TEXT,
    verdict TEXT NOT NULL,
    decision TEXT NOT NULL DEFAULT 'pending',
    confidence INTEGER,
    entry REAL, stop REAL, target REAL,
    shares INTEGER, risk_amount REAL,
    thesis_summary TEXT,
    payload TEXT,
    outcome TEXT NOT NULL DEFAULT 'open',
    outcome_price REAL,
    outcome_notes TEXT
);
"""


def _connect():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(SCHEMA)
    conn.execute(ORDERS_SCHEMA)
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(orders)")}
    for column, ddl in ORDER_MIGRATIONS:
        if column not in existing:
            conn.execute(ddl)
    existing_ideas = {r["name"] for r in conn.execute("PRAGMA table_info(ideas)")}
    for column, ddl in IDEA_MIGRATIONS:
        if column not in existing_ideas:
            conn.execute(ddl)
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def add_idea(snapshot, thesis, plan, gate, strategy_idea=None, risk_base=None):
    """Log one idea. strategy_idea (a StrategyIdea dict) carries the strategy and
    regime tags — the whole point of tagging is that stats() can later answer
    "which rules actually work, and in which market". risk_base is the max loss
    converted to the base currency, so per-strategy totals are addable."""
    tag = strategy_idea or {}
    payload = json.dumps(
        {"snapshot": snapshot, "thesis": thesis, "plan": plan, "gate": gate,
         "strategy_idea": strategy_idea}, default=str)
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO ideas (created_at, updated_at, ticker, direction, verdict,
                   confidence, entry, stop, target, shares, risk_amount,
                   thesis_summary, payload, strategy, strategy_label, regime, status,
                   risk_base)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                _now(), _now(), snapshot["ticker"],
                (plan or {}).get("direction") or tag.get("direction"),
                gate["verdict"],
                (plan or {}).get("confidence"),
                (plan or {}).get("entry"), (plan or {}).get("stop"), (plan or {}).get("target"),
                (plan or {}).get("shares"), (plan or {}).get("risk_amount"),
                thesis.get("thesis_summary"),
                payload,
                tag.get("strategy"), tag.get("strategy_label"), tag.get("regime"),
                tag.get("status"), risk_base,
            ),
        )
        return cur.lastrowid


def list_ideas(limit=100):
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM ideas ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    ideas = []
    for row in rows:
        idea = dict(row)
        idea["payload"] = json.loads(idea["payload"]) if idea["payload"] else {}
        ideas.append(idea)
    return ideas


def set_decision(idea_id, decision):
    assert decision in ("approved", "needs_research", "rejected", "pending")
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE ideas SET decision = ?, updated_at = ? WHERE id = ?",
            (decision, _now(), idea_id))
        return cur.rowcount == 1   # False when the idea doesn't exist


def record_outcome(idea_id, outcome, price=None, notes=None, realised=None):
    """Record how an idea actually turned out.

    realised: the dict from risk/pnl.close_out plus pnl_base/exit_fx_rate, when
    an exit price was supplied. Reopening an idea (outcome='open') clears the
    realised figures rather than leaving stale P&L attached to a live position.
    """
    assert outcome in ("open", "win", "loss", "scratch")
    r = realised or {}
    if outcome == "open":
        r = {}
    with _connect() as conn:
        cur = conn.execute(
            """UPDATE ideas SET outcome = ?, outcome_price = ?, outcome_notes = ?,
                   pnl_instrument = ?, pnl_base = ?, exit_fx_rate = ?,
                   r_multiple = ?, entry_used = ?, updated_at = ?
               WHERE id = ?""",
            (outcome, price if outcome != "open" else None, notes,
             r.get("pnl_instrument"), r.get("pnl_base"), r.get("exit_fx_rate"),
             r.get("r_multiple"), r.get("entry_used"), _now(), idea_id))
        return cur.rowcount == 1


def get_idea(idea_id):
    with _connect() as conn:
        row = conn.execute("SELECT * FROM ideas WHERE id = ?", (idea_id,)).fetchone()
    if row is None:
        return None
    idea = dict(row)
    idea["payload"] = json.loads(idea["payload"]) if idea["payload"] else {}
    return idea


def create_order_ticket(idea_id, side, ticker, quantity, limit_price, currency, max_loss,
                        stop_price=None):
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO orders (idea_id, created_at, updated_at, status, side,
                   ticker, quantity, limit_price, stop_price, currency, max_loss)
               VALUES (?,?,?,'pending_confirmation',?,?,?,?,?,?,?)""",
            (idea_id, _now(), _now(), side, ticker, quantity, limit_price, stop_price,
             currency, max_loss))
        return cur.lastrowid


def get_order(order_id):
    with _connect() as conn:
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    return dict(row) if row else None


ORDER_STATUSES = ("pending_confirmation", "submitting", "submitted", "accepted",
                  "filled", "rejected", "cancelled", "error")


def claim_order_for_submission(order_id):
    """Atomically move a ticket pending_confirmation -> submitting.

    Returns True for the ONE caller that won the claim, False for everyone else.
    This is what makes double-submission impossible: two concurrent confirms
    (double click, retry, two tabs) both pass the earlier validation checks, so
    the database transition — not the read — must be the gate. SQLite applies
    the UPDATE atomically, so exactly one caller sees rowcount == 1.
    """
    with _connect() as conn:
        cur = conn.execute(
            """UPDATE orders SET status='submitting', updated_at=?
               WHERE id=? AND status='pending_confirmation'""",
            (_now(), order_id))
        return cur.rowcount == 1


def release_order_claim(order_id):
    """Undo a claim when we bail out before contacting the broker."""
    with _connect() as conn:
        conn.execute(
            """UPDATE orders SET status='pending_confirmation', updated_at=?
               WHERE id=? AND status='submitting'""",
            (_now(), order_id))


def update_order(order_id, status, ib_order_id=None, detail=None):
    assert status in ORDER_STATUSES
    with _connect() as conn:
        conn.execute(
            "UPDATE orders SET status=?, ib_order_id=?, detail=?, updated_at=? WHERE id=?",
            (status, ib_order_id, detail, _now(), order_id))


def list_orders(limit=50):
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


CONFIDENCE_BUCKETS = ((0, 55, "<55"), (55, 70, "55-69"), (70, 85, "70-84"), (85, 101, "85+"))


def _win_rate(wins, losses):
    closed = wins + losses
    return round(wins / closed * 100, 1) if closed else None


def stats():
    with _connect() as conn:
        rows = conn.execute(
            "SELECT outcome, COUNT(*) AS n FROM ideas WHERE decision='approved' GROUP BY outcome"
        ).fetchall()
        graded = conn.execute(
            """SELECT verdict, confidence, outcome FROM ideas
               WHERE decision='approved' AND outcome IN ('win','loss')"""
        ).fetchall()
        avg_conf = conn.execute(
            "SELECT AVG(confidence) AS c FROM ideas WHERE decision='approved'").fetchone()["c"]
        monthly = conn.execute(
            """SELECT substr(created_at,1,7) AS month, outcome, COUNT(*) AS n
               FROM ideas WHERE decision='approved' AND outcome != 'open'
               GROUP BY month, outcome ORDER BY month"""
        ).fetchall()
        tagged = conn.execute(
            """SELECT strategy, strategy_label, regime, outcome, risk_base,
                      pnl_base, r_multiple
               FROM ideas
               WHERE decision='approved' AND outcome IN ('win','loss','scratch')"""
        ).fetchall()
        produced = conn.execute(
            """SELECT strategy, strategy_label, regime, COUNT(*) AS n
               FROM ideas GROUP BY strategy, regime"""
        ).fetchall()

    counts = {r["outcome"]: r["n"] for r in rows}
    wins, losses = counts.get("win", 0), counts.get("loss", 0)

    # Win rate split by the risk gate's original verdict — does the gate's
    # judgement actually predict outcomes?
    by_verdict = {}
    for row in graded:
        entry = by_verdict.setdefault(row["verdict"] or "unknown", {"wins": 0, "losses": 0})
        entry["wins" if row["outcome"] == "win" else "losses"] += 1
    for entry in by_verdict.values():
        entry["win_rate_pct"] = _win_rate(entry["wins"], entry["losses"])

    # ...and by the confidence score, to see whether the score means anything.
    by_confidence = {label: {"wins": 0, "losses": 0} for *_, label in CONFIDENCE_BUCKETS}
    for row in graded:
        conf = row["confidence"] or 0
        for low, high, label in CONFIDENCE_BUCKETS:
            if low <= conf < high:
                by_confidence[label]["wins" if row["outcome"] == "win" else "losses"] += 1
                break
    for entry in by_confidence.values():
        entry["win_rate_pct"] = _win_rate(entry["wins"], entry["losses"])

    by_month = {}
    for row in monthly:
        by_month.setdefault(row["month"], {})[row["outcome"]] = row["n"]

    return {
        "approved_total": sum(counts.values()),
        "open": counts.get("open", 0),
        "wins": wins,
        "losses": losses,
        "scratches": counts.get("scratch", 0),
        "avg_confidence": round(avg_conf, 1) if avg_conf else None,
        "by_verdict": by_verdict,
        "by_confidence_bucket": by_confidence,
        "by_month": by_month,
        "win_rate_pct": _win_rate(wins, losses),
        "by_strategy": _group_outcomes(tagged, lambda r: r["strategy"],
                                       label_of=lambda r: r["strategy_label"]),
        "by_regime": _group_outcomes(tagged, lambda r: r["regime"]),
        "by_strategy_regime": _group_outcomes(
            tagged, lambda r: f"{r['strategy'] or 'untagged'} · {r['regime'] or 'untagged'}",
            label_of=lambda r: (f"{r['strategy_label'] or 'Untagged'} in "
                                f"{(r['regime'] or 'UNKNOWN').replace('_', ' ').lower()}")),
        "ideas_produced": [
            {"strategy": r["strategy"] or "untagged",
             "strategy_label": r["strategy_label"] or "Untagged (pre-strategy-library)",
             "regime": r["regime"] or "untagged",
             "count": r["n"]}
            for r in produced
        ],
    }


def _group_outcomes(rows, key_of, label_of=None):
    """Wins / losses / win rate / total risked, grouped by whatever key_of returns.

    This is the feedback loop the whole journal exists for: it is what tells you
    that (say) mean-reversion works for you in sideways markets and momentum
    does not, so you can cut one and size up the other. 'untagged' rows are
    ideas logged before the strategy library existed — kept, never guessed at.
    """
    groups = {}
    for row in rows:
        key = key_of(row) or "untagged"
        entry = groups.setdefault(key, {
            "label": (label_of(row) if label_of else None) or key,
            "wins": 0, "losses": 0, "scratches": 0,
            "risked": 0.0, "risked_known": 0,
            "pnl": 0.0, "pnl_known": 0, "gross_profit": 0.0, "gross_loss": 0.0,
            "r_values": [],
        })
        if row["outcome"] == "win":
            entry["wins"] += 1
        elif row["outcome"] == "loss":
            entry["losses"] += 1
        else:
            entry["scratches"] += 1

        # Only ideas logged with a base-currency figure can be summed. Ideas
        # from before that column existed contribute to the counts but not the
        # total, and the total is reported as partial rather than as truth.
        if row["risk_base"] is not None:
            entry["risked"] += float(row["risk_base"])
            entry["risked_known"] += 1
        if row["pnl_base"] is not None:
            value = float(row["pnl_base"])
            entry["pnl"] += value
            entry["pnl_known"] += 1
            if value >= 0:
                entry["gross_profit"] += value
            else:
                entry["gross_loss"] += -value
        if row["r_multiple"] is not None:
            entry["r_values"].append(float(row["r_multiple"]))

    for entry in groups.values():
        graded = entry["wins"] + entry["losses"]
        entry["closed"] = graded + entry["scratches"]
        entry["win_rate_pct"] = _win_rate(entry["wins"], entry["losses"])
        entry["risked"] = round(entry["risked"], 2) if entry["risked_known"] else None
        entry["risked_partial"] = entry["risked_known"] < entry["closed"]

        entry["pnl"] = round(entry["pnl"], 2) if entry["pnl_known"] else None
        entry["pnl_known_count"] = entry["pnl_known"]
        entry["pnl_partial"] = entry["pnl_known"] < entry["closed"]
        # Expectancy: average R per closed trade. THE number for deciding
        # whether a strategy earns its place — it is size-independent, so a
        # small-sizing strategy is not penalised against a large-sizing one.
        entry["expectancy_r"] = pnl_math.expectancy(entry["r_values"])
        # Profit factor: gross profit / gross loss. Above 1.0 makes money.
        # Stays None when there are no losses yet — an infinite profit factor is
        # not valid JSON, and "no losses so far" is a sample-size fact rather
        # than a performance result worth printing as a ratio.
        entry["profit_factor"] = (round(entry["gross_profit"] / entry["gross_loss"], 2)
                                  if entry["gross_loss"] > 0 else None)
        entry["no_losses_yet"] = entry["gross_loss"] == 0 and entry["pnl_known"] > 0
        entry["gross_profit"] = round(entry["gross_profit"], 2)
        entry["gross_loss"] = round(entry["gross_loss"], 2)
        del entry["r_values"], entry["pnl_known"], entry["risked_known"]
    return dict(sorted(groups.items(), key=lambda kv: -kv[1]["closed"]))
