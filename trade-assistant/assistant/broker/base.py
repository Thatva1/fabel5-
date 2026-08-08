"""Broker interface — the Layer 2 seam.

Layer 1 uses only get_positions()/get_account() (read-only portfolio state).
place_order() exists so the seam is complete, but the only v1 implementation
(PaperBroker) refuses it. Layer 2 will add IBKRBroker (ib_async + IB Gateway)
implementing this same interface, and every real order will still require
explicit per-order human confirmation. Nothing in the pipeline calls
place_order — execution can only ever be human-initiated.
"""


class ExecutionNotEnabled(Exception):
    """Raised by brokers that do not support order placement."""


class Broker:
    name = "abstract"

    def get_account(self):
        """{portfolio_value, base_currency} — used by sizing and the gate."""
        raise NotImplementedError

    def get_positions(self):
        """[{ticker, shares, entry_price, sector, currency}, ...]"""
        raise NotImplementedError

    def place_order(self, order_intent):
        """Layer 2 only. order_intent: core.models.OrderIntent."""
        raise ExecutionNotEnabled(f"{self.name}: order placement is not enabled")

    def is_shortable(self, ticker, currency="USD"):
        """Read-only: can this instrument be borrowed and sold short?

        {"status": "yes" | "no" | "unknown", "source": str, "detail": str}

        `currency` is part of the interface because a broker has to resolve the
        contract before it can answer, and a London line is not a USD contract.
        Omitting it here meant IBKR resolved every symbol as USD and returned
        UNKNOWN for every non-US short — a safety feature switched off for most
        of the markets this tool is pointed at.

        The default is UNKNOWN, and that is the honest answer for any broker
        that cannot check. The risk gate treats unknown as a flag, never as
        permission — see assistant/risk/borrow.py.
        """
        return {"status": "unknown", "source": self.name,
                "detail": f"{self.name} cannot check borrow availability."}
