"""Strategy correlation and blend selection."""

import pytest

from assistant.backtest import blend


def test_periodic_returns_from_an_equity_curve():
    assert blend.periodic_returns([100, 110, 99]) == pytest.approx([0.1, -0.1])
    assert blend.periodic_returns([100]) == []


def test_streams_are_aligned_on_shared_dates_before_correlating():
    """Strategies start at different times — a three-year signal cannot trade
    until it has three years of history. Correlating one strategy's 2019
    against another's 2023 produces a number that means nothing."""
    streams = {
        "early": (["d1", "d2", "d3", "d4"], [0.1, -0.1, 0.2, 0.0]),
        "late": (["d3", "d4", "d5"], [0.2, 0.0, 0.3]),
    }
    dates, aligned = blend.align_streams(streams)
    assert (dates, aligned) == ([], {}), \
        "two shared dates cannot support a correlation — say nothing, not something"

    wider = {
        "a": (["d1", "d2", "d3", "d4"], [0.1, -0.1, 0.2, 0.0]),
        "b": (["d2", "d3", "d4"], [-0.1, 0.2, 0.0]),
    }
    dates, aligned = blend.align_streams(wider)
    assert dates == ["d2", "d3", "d4"]
    assert aligned["a"] == pytest.approx([-0.1, 0.2, 0.0])


def test_a_strategy_correlates_perfectly_with_itself():
    aligned = {"a": [0.1, -0.05, 0.2, 0.0, -0.1]}
    matrix = blend.correlation_matrix(aligned)
    assert matrix["a"]["a"] == pytest.approx(1.0)


def test_opposite_streams_correlate_negatively():
    aligned = {"up": [0.1, -0.05, 0.2, 0.0], "down": [-0.1, 0.05, -0.2, 0.0]}
    matrix = blend.correlation_matrix(aligned)
    assert matrix["up"]["down"] == pytest.approx(-1.0)


def test_covariance_matrix_is_ordered_to_match_its_names():
    aligned = {"a": [0.1, -0.1, 0.2, 0.0], "b": [0.0, 0.1, -0.1, 0.2]}
    names = ["b", "a"]
    cov = blend.covariance_matrix(aligned, names)
    assert cov[0][0] == pytest.approx(blend._covariance(aligned["b"], aligned["b"]))
    assert cov[0][1] == pytest.approx(cov[1][0]), "covariance must be symmetric"


# --- blend selection ---------------------------------------------------------

def test_the_blend_refuses_a_near_copy_of_what_it_already_holds():
    """A second copy of the best strategy adds risk without adding return."""
    aligned = {
        "trend": [0.10, -0.05, 0.20, 0.00, -0.10, 0.15],
        "trend_twin": [0.11, -0.04, 0.19, 0.01, -0.09, 0.14],   # nearly identical
        "reversion": [-0.08, 0.12, -0.15, 0.05, 0.10, -0.06],   # moves opposite
    }
    means = {"trend": 0.02, "trend_twin": 0.018, "reversion": 0.015}
    chosen, rejected = blend.choose_blend(means, aligned, max_correlation=0.6)

    assert "trend" in chosen
    assert "reversion" in chosen, "an uncorrelated positive strategy belongs in"
    assert "trend_twin" not in chosen
    assert "correlation" in rejected["trend_twin"]


def test_a_losing_strategy_is_not_rescued_by_being_uncorrelated():
    aligned = {"good": [0.1, -0.05, 0.2], "bad": [-0.2, 0.1, -0.3]}
    chosen, rejected = blend.choose_blend({"good": 0.02, "bad": -0.01}, aligned)
    assert chosen == ["good"]
    assert "no positive edge" in rejected["bad"]


def test_the_blend_is_capped_and_says_why():
    """A blend that silently drops a strategy is impossible to argue with."""
    aligned = {f"s{i}": [0.1 * (i + 1), -0.05 * (i + 1), 0.2, -0.1 * (i + 1)]
               for i in range(6)}
    means = {f"s{i}": 0.02 - i * 0.001 for i in range(6)}
    chosen, rejected = blend.choose_blend(means, aligned, max_correlation=1.1,
                                          max_strategies=3)
    assert len(chosen) == 3
    assert all("already holds 3" in r for n, r in rejected.items() if n not in chosen)


def test_the_best_edge_is_picked_first():
    aligned = {"a": [0.1, -0.1, 0.2], "b": [0.05, -0.2, 0.1]}
    chosen, _ = blend.choose_blend({"a": 0.01, "b": 0.05}, aligned,
                                   max_correlation=1.1)
    assert chosen[0] == "b"


# --- outputs -----------------------------------------------------------------

def test_the_matrix_writes_a_readable_csv(tmp_path):
    matrix = blend.correlation_matrix({"a": [0.1, -0.1, 0.2, 0.0],
                                       "b": [0.0, 0.1, -0.1, 0.2]})
    path = blend.write_matrix_csv(matrix, str(tmp_path / "m.csv"))
    rows = open(path).read().strip().splitlines()
    assert rows[0].startswith("strategy,a,b")
    assert len(rows) == 3


def test_the_heatmap_is_self_contained_svg(tmp_path):
    matrix = blend.correlation_matrix({"a": [0.1, -0.1, 0.2, 0.0],
                                       "b": [0.0, 0.1, -0.1, 0.2]})
    path = blend.write_heatmap_svg(matrix, str(tmp_path / "m.svg"))
    svg = open(path).read()
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    assert "http" not in svg.replace("http://www.w3.org/2000/svg", ""), \
        "no external references — the file must stand alone"
