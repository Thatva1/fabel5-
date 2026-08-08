from assistant.risk.gate import evaluate, exposure_summary

BASE_CONFIG = {
    "base_currency": "USD",
    "account": {
        "portfolio_value": 100000,
        "risk_per_trade_pct": 1.0,
        "max_position_pct": 15.0,
        "max_total_exposure_pct": 60.0,
        "max_sector_exposure_pct": 25.0,
        "max_open_positions": 8,
        "correlation_flag_threshold": 0.75,
    },
    "positions": [],
}


def _snapshot(sector=None):
    return {"ticker": "TEST", "_fundamentals": {"sector": sector}, "signals": []}


def _thesis():
    return {"source": "claude:test", "conviction": 70}


def _plan(position_value=10000, risk_amount=950, confidence=70, currency="USD"):
    return {"position_value": position_value, "risk_amount": risk_amount,
            "confidence": confidence, "currency": currency}


def _config(**overrides):
    cfg = {**BASE_CONFIG, "account": dict(BASE_CONFIG["account"]),
           "positions": list(BASE_CONFIG["positions"])}
    for key, value in overrides.items():
        if key in ("account",):
            cfg["account"].update(value)
        else:
            cfg[key] = value
    return cfg


def test_clean_pass_is_approved():
    result = evaluate(_snapshot(), _thesis(), _plan(), _config(), {})
    assert result["verdict"] == "approved_for_review"
    assert result["hard_failures"] == []
    assert result["soft_flags"] == []


def test_no_plan_is_rejected():
    result = evaluate(_snapshot(), _thesis(), None, _config(), {})
    assert result["verdict"] == "rejected"


def test_oversize_position_rejected():
    result = evaluate(_snapshot(), _thesis(), _plan(position_value=16000), _config(), {})
    assert result["verdict"] == "rejected"
    assert any("cap 15.0%" in x for x in result["hard_failures"])


def test_total_exposure_cap():
    positions = [{"ticker": "AAA", "shares": 100, "entry_price": 550, "currency": "USD"}]
    result = evaluate(_snapshot(), _thesis(), _plan(position_value=10000),
                      _config(positions=positions), {})
    # 55k existing + 10k new = 65% > 60% cap
    assert result["verdict"] == "rejected"
    assert any("Total exposure" in x for x in result["hard_failures"])


def test_max_loss_over_budget_rejected():
    result = evaluate(_snapshot(), _thesis(), _plan(risk_amount=1200), _config(), {})
    assert result["verdict"] == "rejected"
    assert any("risk budget" in x for x in result["hard_failures"])


def test_max_open_positions():
    positions = [{"ticker": f"P{i}", "shares": 1, "entry_price": 10, "currency": "USD"}
                 for i in range(8)]
    result = evaluate(_snapshot(), _thesis(), _plan(), _config(positions=positions), {})
    assert result["verdict"] == "rejected"
    assert any("max open positions" in x for x in result["hard_failures"])


def test_sector_concentration():
    positions = [{"ticker": "AAA", "shares": 50, "entry_price": 400,
                  "sector": "Technology", "currency": "USD"}]
    result = evaluate(_snapshot("Technology"), _thesis(), _plan(position_value=8000),
                      _config(positions=positions), {})
    # 20k + 8k = 28% > 25% sector cap
    assert result["verdict"] == "rejected"
    assert any("Technology exposure" in x for x in result["hard_failures"])


def test_low_confidence_soft_flag():
    result = evaluate(_snapshot(), _thesis(), _plan(confidence=40), _config(), {})
    assert result["verdict"] == "needs_more_research"
    assert any("thin" in x for x in result["soft_flags"])


def test_foreign_currency_plan_converts():
    # 9000 EUR position at 1.10 = 9900 USD -> under the 15% cap; risk 900 EUR = 990 USD ~ budget
    rates = {"EURUSD": 1.10}
    result = evaluate(_snapshot(), _thesis(),
                      _plan(position_value=9000, risk_amount=900, currency="EUR"),
                      _config(), rates)
    assert result["verdict"] == "approved_for_review"


def test_foreign_currency_over_budget_after_conversion():
    rates = {"EURUSD": 1.10}
    # 1000 EUR risk = 1100 USD > 1000 USD budget (with 5% allowance -> still over)
    result = evaluate(_snapshot(), _thesis(),
                      _plan(risk_amount=1000, currency="EUR"), _config(), rates)
    assert result["verdict"] == "rejected"


def test_missing_fx_rate_soft_flags():
    result = evaluate(_snapshot(), _thesis(),
                      _plan(position_value=9000, risk_amount=900, currency="JPY"),
                      _config(), {})
    assert any("No FX rate" in x for x in result["soft_flags"])


def test_correlation_soft_flag_with_injected_history():
    import pandas as pd
    idx = pd.date_range("2026-01-01", periods=80, freq="D")
    trend = pd.DataFrame({"Close": [100 + i + (i % 3) for i in range(80)]}, index=idx)

    def history(ticker):
        return trend  # identical series -> correlation 1.0

    positions = [{"ticker": "TWIN", "shares": 1, "entry_price": 10, "currency": "USD"}]
    result = evaluate(_snapshot(), _thesis(), _plan(),
                      _config(positions=positions), {}, price_history_fn=history)
    assert result["verdict"] == "needs_more_research"
    assert any("High correlation" in x for x in result["soft_flags"])


# ---------- Shorts must ADD to gross exposure, never subtract ----------

def test_short_position_increases_exposure():
    """Regression: IBKR reports shorts as negative share counts. A signed sum
    made a short look like negative exposure, understating risk."""
    positions = [{"ticker": "SHORT", "shares": -100, "entry_price": 50, "currency": "USD"}]
    summary = exposure_summary(positions, 100000, None, "USD", {})
    assert summary["current_value"] == 5000.0, "short exposure must be positive"
    assert summary["current_pct"] == 5.0


def test_short_does_not_cancel_out_long():
    positions = [
        {"ticker": "LONG", "shares": 100, "entry_price": 50, "currency": "USD"},
        {"ticker": "SHORT", "shares": -100, "entry_price": 50, "currency": "USD"},
    ]
    summary = exposure_summary(positions, 100000, None, "USD", {})
    assert summary["current_value"] == 10000.0, "gross exposure is the sum of both legs"
    assert summary["current_pct"] == 10.0


def test_shorts_count_toward_total_exposure_cap():
    """A big short must be able to trip the exposure cap, not mask it."""
    positions = [
        {"ticker": "A", "shares": 400, "entry_price": 100, "currency": "USD"},   # 40k
        {"ticker": "B", "shares": -300, "entry_price": 100, "currency": "USD"},  # 30k gross
    ]
    result = evaluate(_snapshot(), _thesis(), _plan(position_value=5000),
                      _config(positions=positions), {})
    assert result["verdict"] == "rejected"
    assert any("Total exposure" in x for x in result["hard_failures"])


def test_current_mark_preferred_over_cost_basis():
    """Live risk should use what the position is worth now, not what it cost."""
    positions = [{"ticker": "X", "shares": 100, "entry_price": 50,
                  "mark_price": 80, "currency": "USD"}]
    summary = exposure_summary(positions, 100000, None, "USD", {})
    assert summary["current_value"] == 8000.0


def test_short_sector_exposure_counts_gross():
    positions = [{"ticker": "S", "shares": -200, "entry_price": 100,
                  "sector": "Technology", "currency": "USD"}]   # 20k gross
    result = evaluate(_snapshot("Technology"), _thesis(), _plan(position_value=8000),
                      _config(positions=positions), {})
    assert result["verdict"] == "rejected"
    assert any("Technology exposure" in x for x in result["hard_failures"])


def test_exposure_summary_multicurrency():
    positions = [
        {"ticker": "US", "shares": 10, "entry_price": 100, "currency": "USD"},   # 1000 USD
        {"ticker": "EU", "shares": 10, "entry_price": 100, "currency": "EUR"},   # 1100 USD
    ]
    summary = exposure_summary(positions, 10000, None, "USD", {"EURUSD": 1.10})
    assert summary["current_value"] == 2100.0
    assert summary["current_pct"] == 21.0
