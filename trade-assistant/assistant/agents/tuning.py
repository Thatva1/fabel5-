"""What has been tuned for one country, why, and how to undo it.

Each sub-version keeps one small file of differences from the main version,
data/agents/<name>/tuning.json, and an append-only log of every change that was
proposed, tested, applied, rejected or undone. config.yaml is never written: it
is the main version, and the program treats it as read-only.

Only settings on a whitelist can be tuned, each within bounds around the main
version's value, so neither a person in a hurry nor the learning agent can
reach the risk limits, the order path or another country.
"""
import json
import os
import uuid
from datetime import datetime, timezone

from ..core.config import DATA_DIR

AGENTS_DIR = os.path.join(DATA_DIR, "agents")

# A tuned number may move to between half and double the main version's value.
# Wide enough to adapt a stop or a lookback to a market; too narrow to turn a
# rule into a different rule.
LOWER, UPPER = 0.5, 2.0
# Settings that are not numbers to tune: a lookback is a count of bars, and a
# regime list or direction list changes what the rule is.
NOT_TUNABLE = {"regimes", "directions"}


def version_dir(name, agents_dir=None):
    return os.path.join(agents_dir or AGENTS_DIR, name)


def _tuning_path(name, agents_dir=None):
    return os.path.join(version_dir(name, agents_dir), "tuning.json")


def _log_path(name, agents_dir=None):
    return os.path.join(version_dir(name, agents_dir), "changes.jsonl")


def load(name, agents_dir=None):
    """{"strategies": {strategy: {param: value}}} — empty for an untuned version."""
    try:
        with open(_tuning_path(name, agents_dir)) as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(name, tuning, agents_dir=None):
    path = _tuning_path(name, agents_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as handle:
        json.dump(tuning, handle, indent=2, sort_keys=True)
    os.replace(tmp, path)


def history(name, agents_dir=None, limit=None):
    rows = []
    try:
        with open(_log_path(name, agents_dir)) as handle:
            for line in handle:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        continue
    except OSError:
        return []
    return rows[-limit:] if limit else rows


def _append(name, row, agents_dir=None):
    path = _log_path(name, agents_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as handle:
        handle.write(json.dumps(row, default=str) + "\n")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def main_value(config, strategy, param):
    """The main version's value: config.yaml's block over the strategy's defaults."""
    from ..strategies import registry
    for item in registry.all_strategies():
        if item.name == strategy:
            if param == "enabled":
                return item.enabled(config)
            return item.params_for(config).get(param)
    raise KeyError(f"unknown strategy {strategy!r}")


def tunables(config):
    """[(strategy, param, main value, low, high)] — everything that may be tuned."""
    from ..strategies import registry
    out = []
    for item in registry.all_strategies():
        out.append((item.name, "enabled", item.enabled(config), False, True))
        for param, value in sorted(item.params_for(config).items()):
            if param in NOT_TUNABLE or isinstance(value, bool):
                continue
            if isinstance(value, (int, float)) and value != 0:
                low, high = sorted((value * LOWER, value * UPPER))
                out.append((item.name, param, value, low, high))
    return out


def check(config, strategy, param, value):
    """The value to apply, coerced to the main value's type, or ValueError."""
    for name, key, main, low, high in tunables(config):
        if name == strategy and key == param:
            if key == "enabled":
                if not isinstance(value, bool):
                    raise ValueError("enabled must be true or false")
                return value
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"{strategy}.{param} must be a number")
            if not (low <= value <= high):
                raise ValueError(f"{strategy}.{param}={value} is outside {low:g}–{high:g} "
                                 f"(half to double the main version's {main:g})")
            return int(round(value)) if isinstance(main, int) else float(value)
    raise ValueError(f"{strategy}.{param} is not a tunable setting")


def current(name, config, strategy, param, agents_dir=None):
    tuned = (load(name, agents_dir).get("strategies") or {}).get(strategy) or {}
    return tuned[param] if param in tuned else main_value(config, strategy, param)


def apply(name, config, strategy, param, value, *, by, reason, evidence=None,
          agents_dir=None):
    """Set one tuned value for one version and log it. Returns the log row."""
    value = check(config, strategy, param, value)
    old = current(name, config, strategy, param, agents_dir)
    tuning = load(name, agents_dir)
    block = tuning.setdefault("strategies", {}).setdefault(strategy, {})
    if value == main_value(config, strategy, param):
        block.pop(param, None)          # back to the main version: no difference left
        if not block:
            tuning["strategies"].pop(strategy, None)
    else:
        block[param] = value
    save(name, tuning, agents_dir)
    row = {"id": uuid.uuid4().hex[:10], "at": _now(), "version": name,
           "action": "applied", "strategy": strategy, "param": param,
           "old": old, "new": value, "by": by, "reason": reason,
           "evidence": evidence or {}}
    _append(name, row, agents_dir)
    return row


def record(name, row, agents_dir=None):
    """Log a proposal that was NOT applied (rejected by its test, or skipped)."""
    row = {"id": uuid.uuid4().hex[:10], "at": _now(), "version": name, **row}
    _append(name, row, agents_dir)
    return row


def undo(name, config, change_id, *, by="you", agents_dir=None):
    """Put one applied change back to the value it replaced."""
    for row in reversed(history(name, agents_dir)):
        if row.get("id") == change_id:
            if row.get("action") != "applied":
                raise ValueError("only an applied change can be undone")
            if any(r.get("undoes") == change_id for r in history(name, agents_dir)):
                raise ValueError("that change has already been undone")
            result = apply(name, config, row["strategy"], row["param"], row["old"],
                           by=by, reason=f"undo of {change_id}", agents_dir=agents_dir)
            # Re-log as an undo, so the history reads as what happened.
            rows = history(name, agents_dir)
            rows[-1].update({"action": "undone", "undoes": change_id})
            with open(_log_path(name, agents_dir), "w") as handle:
                for item in rows:
                    handle.write(json.dumps(item, default=str) + "\n")
            return {**result, "action": "undone", "undoes": change_id}
    raise ValueError(f"no change {change_id!r} for {name}")


def last_changed(name, strategy, param, agents_dir=None):
    """When this setting last changed in this version, or None."""
    for row in reversed(history(name, agents_dir)):
        if (row.get("strategy"), row.get("param")) == (strategy, param) and \
                row.get("action") in ("applied", "undone"):
            return row.get("at")
    return None


def differences(name, config, agents_dir=None):
    """[(strategy, param, main value, this version's value)] — what makes it different."""
    out = []
    for strategy, params in sorted((load(name, agents_dir).get("strategies") or {}).items()):
        for param, value in sorted(params.items()):
            out.append({"strategy": strategy, "param": param,
                        "main": main_value(config, strategy, param), "value": value})
    return out


def overlay(name, config, agents_dir=None):
    """config with this version's tuned values laid over its strategy blocks."""
    import copy
    tuned = load(name, agents_dir).get("strategies") or {}
    if not tuned:
        return config
    out = copy.deepcopy(config)
    blocks = out.setdefault("strategies", {})
    for strategy, params in tuned.items():
        blocks[strategy] = {**(blocks.get(strategy) or {}), **params}
    return out
