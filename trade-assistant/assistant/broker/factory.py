"""Broker selection — the one place Layer 1 and Layer 2 meet.

Research reads (get_account/get_positions) prefer IBKR when execution is
enabled AND the Gateway is reachable, so the risk gate checks your REAL
portfolio. Any failure falls back to PaperBroker (config.yaml positions) with
a note, so research never breaks because the Gateway is closed.
"""
from .paper import PaperBroker


def get_broker(config):
    """Returns (broker, note). Never raises."""
    if config.get("execution", {}).get("enabled"):
        try:
            from .ibkr import IBKRBroker
            broker = IBKRBroker(config)
            broker.ping()
            return broker, None
        except Exception as exc:
            return PaperBroker(config), (
                f"IBKR enabled but unavailable ({type(exc).__name__}); "
                "using config.yaml portfolio instead")
    return PaperBroker(config), None


def get_execution_broker(config):
    """Strict broker for order placement — raises instead of falling back.
    Silent fallback would be wrong here: an order must go to IBKR or nowhere."""
    from .ibkr import IBKRBroker
    return IBKRBroker(config)
