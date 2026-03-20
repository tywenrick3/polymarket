"""Volatility regime detection.

Classifies the current market state into one of three regimes:
  - QUIET:  low volatility, prices drifting.  Mean reversion dominates.
  - NORMAL: typical activity.  Default strategy weights.
  - NEWS:   high volatility spike.  Momentum dominates, mean reversion
            is unreliable (the "mean" is shifting).

Used by the composite strategy to dynamically adjust weights instead of
using fixed allocation.

Implementation: compare rolling 12hr volatility against a 72hr baseline.
A spike above 2x the baseline = news regime.  Below 0.5x = quiet.
"""

from __future__ import annotations

from enum import Enum


class Regime(Enum):
    QUIET = "quiet"
    NORMAL = "normal"
    NEWS = "news"


# Thresholds: ratio of short-term vol to long-term vol
NEWS_THRESHOLD = 2.0    # vol_12hr > 2x avg → news
QUIET_THRESHOLD = 0.5   # vol_12hr < 0.5x avg → quiet


def detect_regime(
    series: list[dict],
    short_hrs: int = 12,
    long_hrs: int = 72,
) -> Regime:
    """Classify the current volatility regime from a price series.

    Args:
        series: [{t, p}, ...] sorted by time.  Needs at least long_hrs points.
        short_hrs: Window for recent volatility.
        long_hrs: Window for baseline volatility.

    Returns:
        Regime enum value.
    """
    if len(series) < long_hrs:
        return Regime.NORMAL  # not enough data, assume normal

    # Compute hourly returns
    prices = [p["p"] for p in series]

    def _vol(segment: list[float]) -> float:
        """Standard deviation of hourly returns."""
        if len(segment) < 3:
            return 0.0
        returns = [segment[i] - segment[i - 1] for i in range(1, len(segment))]
        mean = sum(returns) / len(returns)
        var = sum((r - mean) ** 2 for r in returns) / len(returns)
        return var ** 0.5

    vol_short = _vol(prices[-short_hrs:])
    vol_long = _vol(prices[-long_hrs:])

    if vol_long < 1e-6:
        return Regime.QUIET  # essentially zero volatility

    ratio = vol_short / vol_long

    if ratio > NEWS_THRESHOLD:
        return Regime.NEWS
    elif ratio < QUIET_THRESHOLD:
        return Regime.QUIET
    else:
        return Regime.NORMAL


# Strategy weight presets per regime.
# Keys match strategy.name values.
REGIME_WEIGHTS: dict[Regime, dict[str, float]] = {
    Regime.QUIET: {
        "mean_reversion": 0.60,
        "momentum": 0.10,
        "sma": 0.05,
        "cross_market": 0.25,
    },
    Regime.NORMAL: {
        "mean_reversion": 0.40,
        "momentum": 0.30,
        "sma": 0.05,
        "cross_market": 0.25,
    },
    Regime.NEWS: {
        "mean_reversion": 0.15,
        "momentum": 0.55,
        "sma": 0.05,
        "cross_market": 0.25,
    },
}
