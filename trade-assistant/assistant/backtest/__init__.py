"""Historical replay of the strategy library.

The journal measures strategies going forward, which takes months before the
numbers mean anything. This replays the same strategies over years of history
in seconds, producing the same expectancy tables.

Why this is trustworthy here specifically: the strategies are pure functions
over a price DataFrame with no I/O and no LLM, so replaying them is genuinely
the same code path the live scan uses — not a reimplementation that drifts.

What it cannot test: the thesis engine. Yahoo serves today's fundamentals and
today's news, not what was known on a Tuesday in 2019, so no honest historical
replay of the narrative layer is possible. Since strategies decide the trades
and the LLM only explains them, this still tests the part that takes risk.

Read `HONEST_LIMITATIONS` before believing any number this package produces.
"""

HONEST_LIMITATIONS = [
    "Survivorship bias: only instruments that still exist today can be tested. "
    "Companies that delisted or went bust are invisible, which flatters every result.",

    "Technicals only: the thesis engine, news and fundamentals cannot be replayed "
    "historically, so these results test the strategy rules alone.",

    "No intrabar data: when a bar's range covers both the stop and the target, "
    "there is no way to know which was touched first. The stop is always assumed, "
    "so results are pessimistic rather than flattering.",

    "Fills are modelled, not real. Slippage, commission and stamp duty are "
    "estimates from config; a thin stock in a fast market can be far worse.",

    "Past behaviour is not predictive. A strategy that worked historically can "
    "stop working the moment conditions change.",
]
