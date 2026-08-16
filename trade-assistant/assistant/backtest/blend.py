"""Strategy correlation, and blending from it rather than by assumption.

Equal-weighting everything is not diversification, it is an average — and the
study showed what that costs: running all twelve strategies together returned
less than four of them individually and produced the deepest drawdown in the
set. Adding a weak, correlated strategy to a strong one makes the result worse
in both directions at once.

Two things are needed to do better, and the first is missing from most of this
kind of work: the correlation of the STRATEGIES' return streams, not of the
instruments they hold. Two momentum strategies can hold entirely different
tickers and still be the same bet, because what they have in common is when
they lose. The matrix here is built from each strategy's own equity curve.

The blend then selects on two axes rather than one — positive out-of-sample
edge AND low correlation to what is already in — because a second copy of the
best strategy adds risk without adding return.
"""
import csv
import math


def periodic_returns(curve):
    """Period-over-period returns from an equity curve."""
    out = []
    for previous, current in zip(curve, curve[1:]):
        if previous:
            out.append(current / previous - 1.0)
    return out


def align_streams(streams):
    """Trim a {name: (dates, returns)} map onto the dates they all share.

    Strategies start at different times — a three-year signal cannot trade
    until it has three years of history — so an unaligned correlation compares
    one strategy's 2019 with another's 2023 and reports a number that means
    nothing. Anything with no overlap is dropped rather than padded with zeros,
    which would read as "moved together, both flat".
    """
    if not streams:
        return [], {}
    common = None
    for dates, _ in streams.values():
        as_set = set(dates)
        common = as_set if common is None else (common & as_set)
    common = sorted(common or [])
    if len(common) < 3:
        return [], {}

    aligned = {}
    for name, (dates, values) in streams.items():
        lookup = dict(zip(dates, values))
        series = [lookup[d] for d in common if d in lookup]
        if len(series) == len(common):
            aligned[name] = series
    return common, aligned


def correlation_matrix(aligned):
    """{name: {name: rho}} over the aligned return streams."""
    names = sorted(aligned)
    matrix = {a: {} for a in names}
    for a in names:
        for b in names:
            matrix[a][b] = round(_correlation(aligned[a], aligned[b]), 4)
    return matrix


def covariance_matrix(aligned, names=None):
    """Covariance as a list of lists, ordered by `names` — Kelly's input."""
    names = names or sorted(aligned)
    size = len(names)
    out = [[0.0] * size for _ in range(size)]
    for i, a in enumerate(names):
        for j, b in enumerate(names):
            out[i][j] = _covariance(aligned[a], aligned[b])
    return out


def choose_blend(candidates, aligned, max_correlation=0.6, max_strategies=4):
    """Pick positive, low-correlation strategies, best edge first.

    `candidates` is {name: mean_return}, and only positive ones are considered:
    a strategy that loses money does not become worth holding by being
    uncorrelated with one that does not. Each subsequent pick must be below
    `max_correlation` against EVERY strategy already chosen, so the blend
    cannot fill up with near-copies of its own first choice.

    Returns (chosen, rejected) where rejected explains itself — a blend that
    silently drops a strategy is impossible to argue with.
    """
    ranked = sorted((n for n, m in candidates.items() if m > 0),
                    key=lambda n: -candidates[n])
    chosen, rejected = [], {}

    for name in ranked:
        if len(chosen) >= max_strategies:
            rejected[name] = f"blend already holds {max_strategies} strategies"
            continue
        clash = None
        for held in chosen:
            rho = _correlation(aligned.get(name, []), aligned.get(held, []))
            if rho > max_correlation:
                clash = (held, rho)
                break
        if clash:
            rejected[name] = (f"correlation {clash[1]:.2f} with {clash[0]}, "
                              f"above the {max_correlation} ceiling")
            continue
        chosen.append(name)

    for name, mean in candidates.items():
        if mean <= 0 and name not in rejected:
            rejected[name] = f"no positive edge to blend (mean {mean:.5f})"
    return chosen, rejected


def write_matrix_csv(matrix, path):
    names = sorted(matrix)
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["strategy"] + names)
        for a in names:
            writer.writerow([a] + [matrix[a][b] for b in names])
    return path


def write_heatmap_svg(matrix, path, title="Strategy correlation"):
    """A standalone SVG heatmap — no plotting library, no external fonts.

    Blue for strategies that move apart, red for those that move together, so
    the thing you are looking for (a pale, uncorrelated block) is visible
    without reading a single number.
    """
    names = sorted(matrix)
    n = len(names)
    if n == 0:
        return None
    cell, left, top = 44, 190, 130
    width, height = left + n * cell + 30, top + n * cell + 30

    def colour(rho):
        # -1 blue, 0 near-white, +1 red.
        if rho >= 0:
            r, g, b = 200, int(200 - 150 * rho), int(200 - 150 * rho)
        else:
            r, g, b = int(200 + 150 * rho), int(200 + 150 * rho), 200
        return f"rgb({max(0,r)},{max(0,g)},{max(0,b)})"

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="ui-monospace,Menlo,monospace">',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="20" y="34" font-size="17" font-family="Georgia,serif" '
        f'fill="#14171c">{title}</text>',
        '<text x="20" y="56" font-size="11" fill="#6b7280">Correlation of the '
        'strategies\' own return streams — not of the instruments they hold.</text>',
    ]
    for j, b in enumerate(names):
        x = left + j * cell + cell / 2
        parts.append(f'<text x="{x}" y="{top - 10}" font-size="9.5" fill="#4a5058" '
                     f'text-anchor="end" transform="rotate(-55 {x} {top - 10})">{b}</text>')
    for i, a in enumerate(names):
        y = top + i * cell
        parts.append(f'<text x="{left - 10}" y="{y + cell/2 + 4}" font-size="10" '
                     f'fill="#4a5058" text-anchor="end">{a}</text>')
        for j, b in enumerate(names):
            rho = matrix[a][b]
            x = left + j * cell
            parts.append(f'<rect x="{x}" y="{y}" width="{cell-2}" height="{cell-2}" '
                         f'fill="{colour(rho)}" stroke="#e2dfd8"/>')
            parts.append(f'<text x="{x + cell/2 - 1}" y="{y + cell/2 + 4}" '
                         f'font-size="9.5" text-anchor="middle" '
                         f'fill="{"#ffffff" if abs(rho) > 0.6 else "#14171c"}">'
                         f'{rho:.2f}</text>')
    parts.append("</svg>")
    with open(path, "w") as handle:
        handle.write("\n".join(parts))
    return path


def _mean(values):
    return sum(values) / len(values) if values else 0.0


def _covariance(a, b):
    n = min(len(a), len(b))
    if n < 2:
        return 0.0
    a, b = a[:n], b[:n]
    ma, mb = _mean(a), _mean(b)
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (n - 1)


def _correlation(a, b):
    n = min(len(a), len(b))
    if n < 3:
        return 0.0
    cov = _covariance(a, b)
    va, vb = _covariance(a, a), _covariance(b, b)
    if va <= 0 or vb <= 0:
        return 0.0
    return cov / math.sqrt(va * vb)
