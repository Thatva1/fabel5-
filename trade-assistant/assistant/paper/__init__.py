"""Automatic paper trader — the rules trading forward, with nobody intervening.

The project could already replay history (backtest/) and could already propose
ideas for a human to judge (pipeline + journal). It could not answer the
question in between: what would this system have DONE since the day I switched
it on, with no one picking which signals to take?

That gap matters because the two existing answers flatter in opposite
directions. A backtest only sees instruments that still exist today. A journal
only records the trades a person chose to enter, which quietly edits out the
ones they lost their nerve on. Trading forward on a screened universe with the
decisions made by rule is the honest middle.

  screen.py   which instruments are tradable at all (28,192 symbols is not a
              universe until the untradeable ones are removed)
  book.py     persisted cash, positions and equity curve
  session.py  one day's work: mark, take exits, rebalance if due

No module in this package imports a broker. Orders cannot be placed from here,
not because a flag is off but because the code to do it is absent.
"""
