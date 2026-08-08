"""PaperBroker — the only broker in Layer 1. Portfolio state comes from
config.yaml; order placement is structurally refused."""
from .base import Broker, ExecutionNotEnabled


class PaperBroker(Broker):
    name = "paper"

    def __init__(self, config):
        self._config = config

    def get_account(self):
        return {
            "portfolio_value": self._config.get("account", {}).get("portfolio_value", 0),
            "base_currency": self._config.get("base_currency", "USD"),
        }

    def get_positions(self):
        return list(self._config.get("positions", []))

    def is_shortable(self, ticker):
        return {"status": "unknown", "source": "paper",
                "detail": ("No broker connection, so borrow availability is unverified. "
                           "Confirm with your broker before shorting.")}

    def place_order(self, order_intent):
        raise ExecutionNotEnabled(
            "This is the Layer 1 research build: order placement is disabled by design. "
            "Execution arrives in Layer 2 (Interactive Brokers via ib_async), and even "
            "then every order requires your explicit per-order confirmation."
        )
