"""One place that knows how to build a CrossSection from frames.

The sweep, the paper session and the backtest all need one, and each was
reaching for it slightly differently. A rank computed against a different
universe is a different signal, so it is worth having exactly one way to make it.
"""


def build_cross_section(frames):
    from ..strategies.cross_section import CrossSection

    try:
        return CrossSection.from_frames(frames)
    except Exception:
        return None
