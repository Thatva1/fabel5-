"""Config loading: config.yaml (risk rules, watchlist) + .env (API keys).

All risk rules live in config.yaml — nothing risk-related is hard-coded.
"""
import os

import yaml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.yaml")
DATA_DIR = os.path.join(PROJECT_ROOT, "data")

DISCLAIMER = (
    "Research / decision-support tool only. Nothing here is executed automatically "
    "and nothing here is financial advice. All outputs require your manual review "
    "and final decision. Data may be delayed or inaccurate."
)

REQUIRED_ACCOUNT_KEYS = (
    "portfolio_value", "risk_per_trade_pct", "max_position_pct",
    "max_total_exposure_pct", "max_open_positions",
)


def _load_env_file():
    """Load KEY=VALUE lines from .env into the environment (existing env wins)."""
    path = os.path.join(PROJECT_ROOT, ".env")
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file()


def load_config():
    with open(CONFIG_PATH) as f:
        try:
            cfg = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            raise ValueError(f"config.yaml is not valid YAML: {exc}") from exc
    if not isinstance(cfg, dict):
        raise ValueError("config.yaml must be a mapping at the top level")
    account = cfg.get("account") or {}
    missing = [k for k in REQUIRED_ACCOUNT_KEYS if k not in account]
    if missing:
        raise ValueError(f"config.yaml account section is missing: {', '.join(missing)}")
    cfg.setdefault("base_currency", "USD")
    cfg.setdefault("watchlist", [])
    cfg.setdefault("positions", [])
    for pos in cfg["positions"]:
        pos.setdefault("currency", cfg["base_currency"])
    return cfg


def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        f.write("# Trade Assistant configuration. Risk rules are only ever changed by you.\n")
        yaml.safe_dump(cfg, f, sort_keys=False, default_flow_style=False)


def api_key(name):
    return os.environ.get(name) or None
