"""Purchase taxes: UK stamp duty, French and Italian FTT, charged on the buying
leg only, and only on companies registered where the tax applies."""
from assistant.backtest import simulator

COSTS = {"commission_per_trade": 4.0, "stamp_duty_pct": 0.5, "stamp_duty_suffixes": [".L"],
         "purchase_taxes": {".PA": {"pct": 0.4, "domiciles": ["France"]},
                            ".MI": {"pct": 0.1, "domiciles": ["Italy"]}}}


def test_rates_by_market_and_company():
    assert simulator.purchase_tax_pct("HSBA.L", COSTS) == 0.5
    assert simulator.purchase_tax_pct("MC.PA", COSTS) == 0.4      # LVMH, French
    assert simulator.purchase_tax_pct("ENI.MI", COSTS) == 0.1     # Eni, Italian
    assert simulator.purchase_tax_pct("AIR.PA", COSTS) == 0.0     # Airbus, Dutch
    assert simulator.purchase_tax_pct("STLAM.MI", COSTS) == 0.0   # Stellantis, Dutch
    assert simulator.purchase_tax_pct("SAP.DE", COSTS) == 0.0
    assert simulator.purchase_tax_pct("AAPL", COSTS) == 0.0


def test_an_unknown_company_is_charged_rather_than_forgotten():
    assert simulator.purchase_tax_pct("NOTINLIST.PA", COSTS) == 0.4


def test_only_the_buying_leg_pays():
    assert simulator.purchase_tax("MC.PA", "long", "entry", 100.0, 10, COSTS) == 4.0
    assert simulator.purchase_tax("MC.PA", "long", "exit", 100.0, 10, COSTS) == 0.0
    assert simulator.purchase_tax("MC.PA", "short", "exit", 100.0, 10, COSTS) == 4.0


def test_round_trip_includes_commission_and_tax_once():
    # 2 x 4.0 commission + 0.4% of 10 x 100 bought
    assert simulator.trade_costs("MC.PA", "long", 100.0, 110.0, 10, COSTS) == 12.0
    # UK unchanged from before: 0.5% on the purchase leg
    assert simulator.trade_costs("HSBA.L", "long", 10.0, 9.0, 100, COSTS) == 13.0
