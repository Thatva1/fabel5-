"""Audit findings C-1 and C-2: config.yaml must survive routine dashboard use.

C-1: save_config() round-tripped the whole file through yaml.safe_dump, which
cannot preserve comments — one click on "add to watchlist" destroyed 90 of the
91 comment lines that document why each risk threshold is what it is.

C-2: the write was neither atomic nor locked.

The fix splits mutable state (watchlist, execution.enabled) into state.yaml and
makes config.yaml read-only to the program.
"""
import os
import shutil
import threading

import pytest
import yaml

from assistant.core import config as cfg


@pytest.fixture
def sandbox(monkeypatch, tmp_path):
    """A throwaway copy of the real config.yaml, with state.yaml alongside."""
    tmp = tmp_path / "config.yaml"
    shutil.copy(cfg.CONFIG_PATH, tmp)
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(tmp))
    return tmp


def _comment_lines(path):
    with open(path) as f:
        return sum(1 for line in f if line.lstrip().startswith("#"))


def test_the_real_config_still_has_its_comments():
    """Guards the premise: if this drops to ~1 the damage already happened."""
    assert _comment_lines(cfg.CONFIG_PATH) > 50


def test_watchlist_add_does_not_touch_config_yaml(sandbox):
    before_bytes = sandbox.read_bytes()
    before_comments = _comment_lines(sandbox)

    cfg.add_to_watchlist("TSCO.L")

    assert sandbox.read_bytes() == before_bytes, "config.yaml was rewritten"
    assert _comment_lines(sandbox) == before_comments


def test_execution_toggle_does_not_touch_config_yaml(sandbox):
    before = sandbox.read_bytes()
    cfg.set_execution_enabled(True)
    cfg.set_execution_enabled(False)
    assert sandbox.read_bytes() == before


def test_watchlist_changes_persist_through_load_config(sandbox):
    original = cfg.load_config()["watchlist"]
    cfg.add_to_watchlist("TSCO.L")
    assert "TSCO.L" in cfg.load_config()["watchlist"]

    removed, _ = cfg.remove_from_watchlist("TSCO.L")
    assert removed is True
    assert cfg.load_config()["watchlist"] == original


def test_removing_an_absent_ticker_reports_false(sandbox):
    removed, _ = cfg.remove_from_watchlist("NOTHERE")
    assert removed is False


def test_execution_flag_persists_through_load_config(sandbox):
    cfg.set_execution_enabled(True)
    assert cfg.load_config()["execution"]["enabled"] is True
    cfg.set_execution_enabled(False)
    assert cfg.load_config()["execution"]["enabled"] is False


def test_state_seeds_from_config_so_watchlist_is_not_reset(sandbox):
    """First mutation must carry the existing config.yaml watchlist across."""
    original = cfg.load_config()["watchlist"]
    assert original, "fixture config should have a watchlist"

    cfg.add_to_watchlist("ZZZZ")
    state = yaml.safe_load(open(os.path.join(sandbox.parent, "state.yaml")))
    for symbol in original:
        assert symbol in state["watchlist"]


def test_toggle_preserves_other_execution_settings(sandbox):
    """Only `enabled` is state; host/port/account stay config.yaml's."""
    before = cfg.load_config()["execution"]
    cfg.set_execution_enabled(True)
    after = cfg.load_config()["execution"]
    assert after["port"] == before["port"]
    assert after["host"] == before["host"]
    assert after["account"] == before["account"]


def test_state_file_is_valid_yaml_with_a_header_comment(sandbox):
    cfg.set_execution_enabled(True)
    text = open(os.path.join(sandbox.parent, "state.yaml")).read()
    assert text.startswith("#")
    assert yaml.safe_load(text)["execution"]["enabled"] is True


def test_concurrent_watchlist_adds_do_not_lose_writes(sandbox):
    """C-2: without a lock, the read-modify-write races and drops symbols."""
    symbols = [f"SYM{i}" for i in range(40)]
    errors = []

    def add(symbol):
        try:
            cfg.add_to_watchlist(symbol)
        except Exception as exc:          # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=add, args=(s,)) for s in symbols]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    final = cfg.load_config()["watchlist"]
    assert set(symbols) <= set(final), "a concurrent add was lost"


def test_concurrent_toggle_and_add_keep_both_changes(sandbox):
    """The worst interleaving in the audit: an add racing an execution toggle
    silently reverting the toggle."""
    def toggle():
        cfg.set_execution_enabled(True)

    threads = [threading.Thread(target=toggle)]
    threads += [threading.Thread(target=cfg.add_to_watchlist, args=(f"R{i}",))
                for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    loaded = cfg.load_config()
    assert loaded["execution"]["enabled"] is True
    assert "R19" in loaded["watchlist"]


def test_state_write_is_atomic_leaving_no_partial_file(sandbox, monkeypatch):
    """A crash mid-write must leave the previous state intact, never a truncated
    file — this is what makes the risk config unbreakable by a killed process."""
    cfg.add_to_watchlist("KEEP")
    good = open(os.path.join(sandbox.parent, "state.yaml")).read()

    real_replace = os.replace

    def boom(src, dst):
        raise OSError("simulated crash after the temp file was written")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        cfg.add_to_watchlist("LOST")
    monkeypatch.setattr(os, "replace", real_replace)

    assert open(os.path.join(sandbox.parent, "state.yaml")).read() == good
    assert "KEEP" in cfg.load_config()["watchlist"]
    assert "LOST" not in cfg.load_config()["watchlist"]
    # And no temp files were left behind.
    assert not [n for n in os.listdir(sandbox.parent) if n.startswith(".state-")]


def test_corrupt_state_yaml_reports_clearly(sandbox):
    with open(os.path.join(sandbox.parent, "state.yaml"), "w") as f:
        f.write("watchlist: [unclosed\n")
    with pytest.raises(ValueError, match="state.yaml is not valid YAML"):
        cfg.load_config()


def test_ibkr_account_can_come_from_the_environment(sandbox, monkeypatch):
    """H-1: lets the account id live in .env instead of config.yaml."""
    monkeypatch.setenv("IBKR_ACCOUNT", "DU9999999")
    assert cfg.load_config()["execution"]["account"] == "DU9999999"


def test_config_account_is_used_when_env_is_unset(sandbox, monkeypatch):
    monkeypatch.delenv("IBKR_ACCOUNT", raising=False)
    assert cfg.load_config()["execution"]["account"] == "DUR673876"
