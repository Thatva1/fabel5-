"""Config loading: config.yaml (risk rules) + state.yaml (UI state) + .env (keys).

Two files, deliberately:

  * config.yaml is YOURS. Hand-edited, heavily commented with the measured
    reasoning behind every threshold, and NEVER written by this program. A YAML
    round-trip cannot preserve comments, so any code that rewrote this file
    would silently delete the notes that justify your risk rules.
  * state.yaml is the program's. It holds only what the dashboard mutates —
    the watchlist and the execution on/off flag — and it is machine-written,
    so losing its formatting costs nothing.

load_config() returns the two merged, with state.yaml winning for the keys it
owns, so the rest of the app sees one config dict exactly as before.

All writes are atomic (temp file + os.replace) and serialised by a lock, since
Flask serves requests on multiple worker threads.
"""
import os
import tempfile
import threading

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

STATE_HEADER = """\
# Mutable application state, written by the dashboard. NOT your configuration.
#
# Your risk rules live in config.yaml, which this program never writes — that
# is what keeps its comments intact. This file holds only the two things the UI
# changes: the watchlist, and whether order entry is switched on.
#
# Safe to delete: it is re-seeded from config.yaml on the next change.
"""

# Serialises the read-modify-write cycle across Flask worker threads. Without
# it, two near-simultaneous watchlist adds silently lose one of the changes —
# and in the worst interleaving, an execution toggle too.
_state_lock = threading.RLock()


def _state_path():
    """Alongside config.yaml, derived at call time so tests that point
    CONFIG_PATH at a temp copy isolate the state file automatically."""
    return os.path.join(os.path.dirname(CONFIG_PATH), "state.yaml")


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


def _read_yaml(path, what):
    with open(path) as f:
        try:
            data = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            raise ValueError(f"{what} is not valid YAML: {exc}") from exc
    return data


def load_state():
    """The dashboard-owned state, or {} when the file doesn't exist yet."""
    path = _state_path()
    if not os.path.exists(path):
        return {}
    data = _read_yaml(path, "state.yaml")
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("state.yaml must be a mapping at the top level")
    return data


def _write_state(state):
    """Atomic replace: a crash or full disk can never leave a half-written file.

    The temp file is created in the same directory so os.replace() stays on one
    filesystem, which is what makes it atomic.
    """
    path = _state_path()
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".state-", suffix=".yaml")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(STATE_HEADER)
            yaml.safe_dump(state, f, sort_keys=False, default_flow_style=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _raw_config():
    cfg = _read_yaml(CONFIG_PATH, "config.yaml")
    if not isinstance(cfg, dict):
        raise ValueError("config.yaml must be a mapping at the top level")
    return cfg


def load_config():
    """config.yaml overlaid with state.yaml. state.yaml wins for the keys it owns."""
    cfg = _raw_config()
    account = cfg.get("account") or {}
    missing = [k for k in REQUIRED_ACCOUNT_KEYS if k not in account]
    if missing:
        raise ValueError(f"config.yaml account section is missing: {', '.join(missing)}")

    state = load_state()
    if "watchlist" in state:
        cfg["watchlist"] = list(state["watchlist"] or [])
    if isinstance(state.get("execution"), dict) and "enabled" in state["execution"]:
        cfg.setdefault("execution", {})["enabled"] = bool(state["execution"]["enabled"])

    # The IBKR account id is account-identifying data. Keeping it in .env lets
    # config.yaml be shared or committed; config.yaml stays the fallback so
    # existing setups keep working unchanged.
    env_account = os.environ.get("IBKR_ACCOUNT")
    if env_account:
        cfg.setdefault("execution", {})["account"] = env_account.strip()

    cfg.setdefault("base_currency", "USD")
    cfg.setdefault("watchlist", [])
    cfg.setdefault("positions", [])
    for pos in cfg["positions"]:
        pos.setdefault("currency", cfg["base_currency"])
    return cfg


def _mutate_state(mutator):
    """Read-modify-write the state file under the lock, atomically.

    mutator receives the current state dict and returns whatever the caller
    should get back. It mutates the dict in place.
    """
    with _state_lock:
        state = load_state()
        if not state:
            # First write: seed from whatever config.yaml currently holds, so
            # switching to state.yaml doesn't reset the user's watchlist.
            cfg = _raw_config()
            state = {
                "watchlist": list(cfg.get("watchlist") or []),
                "execution": {"enabled": bool((cfg.get("execution") or {}).get("enabled"))},
            }
        state.setdefault("watchlist", [])
        state.setdefault("execution", {})
        result = mutator(state)
        _write_state(state)
        return result


def add_to_watchlist(ticker):
    """Returns the new watchlist. Idempotent."""
    def apply(state):
        if ticker not in state["watchlist"]:
            state["watchlist"].append(ticker)
        return list(state["watchlist"])
    return _mutate_state(apply)


def remove_from_watchlist(ticker):
    """Returns (removed, watchlist)."""
    def apply(state):
        present = ticker in state["watchlist"]
        if present:
            state["watchlist"].remove(ticker)
        return present, list(state["watchlist"])
    return _mutate_state(apply)


def set_execution_enabled(enabled):
    def apply(state):
        state["execution"]["enabled"] = bool(enabled)
        return bool(enabled)
    return _mutate_state(apply)


def api_key(name):
    return os.environ.get(name) or None
