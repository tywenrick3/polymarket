"""Weather temperature strategy — forecast vs market probability.

Compares Open-Meteo GFS ensemble forecast probabilities against
Polymarket bracket prices. Signals BUY when a bracket is underpriced
relative to the forecast, SELL when overpriced.

Bracket formats on Polymarket (all are groupItemTitle values):
  US cities (°F):     "32-33°F"  "31°F or below"  "42°F or higher"
  Non-US cities (°C): "9°C"      "13°C or below"  "18°C or higher"
"""

from __future__ import annotations

import math
import re

from polymarket_cli.models import Event, Outcome
from polymarket_cli.strategies.base import TradeSignal
from polymarket_cli.api.weather import EnsembleForecast


# ---------------------------------------------------------------------------
# Bracket parsing
# ---------------------------------------------------------------------------

class BracketRange:
    """A parsed temperature bracket."""
    __slots__ = ("low", "high")

    def __init__(self, low: float | None, high: float | None):
        self.low = low
        self.high = high

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, BracketRange):
            return NotImplemented
        return self.low == other.low and self.high == other.high

    def __repr__(self) -> str:
        return f"BracketRange({self.low}, {self.high})"


# Patterns ordered most-specific first.
_BRACKET_PATTERNS: list[tuple[re.Pattern, object]] = [
    # "32-33°F" / "32–33°F" / "32-33°C"  (2-degree Fahrenheit or Celsius range)
    (re.compile(r"^(-?\d+(?:\.\d+)?)\s*[-–]\s*(-?\d+(?:\.\d+)?)\s*°[FC]$", re.I),
     lambda m: BracketRange(float(m.group(1)), float(m.group(2)))),

    # "31°F or below" / "13°C or below"
    (re.compile(r"^(-?\d+(?:\.\d+)?)\s*°[FC]\s+or\s+(?:below|less)$", re.I),
     lambda m: BracketRange(None, float(m.group(1)))),

    # "42°F or higher" / "18°C or higher"
    (re.compile(r"^(-?\d+(?:\.\d+)?)\s*°[FC]\s+or\s+(?:higher|more|above)$", re.I),
     lambda m: BracketRange(float(m.group(1)), None)),

    # "≤32°F" / "<=13°C"
    (re.compile(r"^[≤<]=?\s*(-?\d+(?:\.\d+)?)\s*°[FC]$", re.I),
     lambda m: BracketRange(None, float(m.group(1)))),

    # "≥71°F" / ">=18°C"
    (re.compile(r"^[≥>]=?\s*(-?\d+(?:\.\d+)?)\s*°[FC]$", re.I),
     lambda m: BracketRange(float(m.group(1)), None)),

    # Single degree: "9°C" / "-5°C" / "22°C"  (exact integer bucket)
    # Treated as [N, N] — the Gaussian smoothing handles the ±0.5 rounding.
    (re.compile(r"^(-?\d+(?:\.\d+)?)\s*°[FC]$", re.I),
     lambda m: BracketRange(float(m.group(1)), float(m.group(1)))),
]


def parse_bracket(name: str) -> BracketRange | None:
    """Parse a Polymarket outcome name into a BracketRange."""
    name = name.strip()
    for pattern, builder in _BRACKET_PATTERNS:
        m = pattern.match(name)
        if m:
            return builder(m)
    return None


# ---------------------------------------------------------------------------
# Probability math
# ---------------------------------------------------------------------------

def _gaussian_cdf(x: float, mu: float, sigma: float) -> float:
    """CDF of N(mu, sigma) evaluated at x."""
    if sigma <= 0:
        return 1.0 if x >= mu else 0.0
    return 0.5 * (1.0 + math.erf((x - mu) / (sigma * math.sqrt(2.0))))


def _bracket_prob_for_member(high: float, sigma: float, br: BracketRange) -> float:
    """Probability mass of N(high, sigma) within the bracket.

    For single-degree brackets [N, N], we integrate over [N-0.5, N+0.5]
    (the rounding bin). For ranges [A, B], we integrate over [A-0.5, B+0.5]
    to account for integer rounding at boundaries.
    """
    if br.low is not None and br.high is not None:
        if br.low == br.high:
            # Single degree bucket: [N-0.5, N+0.5]
            lo = br.low - 0.5
            hi = br.high + 0.5
        else:
            # Range bucket: [A-0.5, B+0.5]
            lo = br.low - 0.5
            hi = br.high + 0.5
    elif br.low is None:
        # Open low: (-inf, B+0.5]
        lo = None
        hi = br.high + 0.5  # type: ignore[operator]
    else:
        # Open high: [A-0.5, +inf)
        lo = br.low - 0.5
        hi = None

    lo_cdf = _gaussian_cdf(lo, high, sigma) if lo is not None else 0.0
    hi_cdf = _gaussian_cdf(hi, high, sigma) if hi is not None else 1.0

    return max(0.0, hi_cdf - lo_cdf)


def ensemble_bracket_probabilities(
    forecast: EnsembleForecast,
    brackets: list[tuple[Outcome, BracketRange]],
    sigma: float,
) -> dict[str, float]:
    """Compute forecast-implied probability for each bracket.

    Each ensemble member's daily max is treated as a Gaussian with the
    given sigma to smooth the discrete member distribution. Probabilities
    are normalized to sum to 1.0.
    """
    n = forecast.n_members
    if n == 0:
        return {}

    probs: dict[str, float] = {}

    for outcome, bracket in brackets:
        total = 0.0
        for high in forecast.member_highs:
            total += _bracket_prob_for_member(high, sigma, bracket)
        probs[outcome.name] = total / n

    # Normalize
    prob_sum = sum(probs.values())
    if prob_sum > 0:
        for name in probs:
            probs[name] /= prob_sum

    return probs


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------

# Clamp ensemble-derived probabilities to avoid extreme confidence
_PROB_FLOOR = 0.02
_PROB_CEIL = 0.98

# Don't signal on outcomes already priced above this (too expensive)
MAX_ENTRY_PRICE = 0.70


class WeatherStrategy:
    """Compare ensemble forecast probabilities against Polymarket prices."""

    name = "weather"

    def __init__(
        self,
        min_edge: float = 0.08,      # 8 percentage points
        sigma_f: float = 1.5,        # Gaussian smoothing for °F markets
        sigma_c: float = 1.0,        # Gaussian smoothing for °C markets
    ):
        self._min_edge = min_edge
        self._sigma_f = sigma_f
        self._sigma_c = sigma_c

    def score_event(
        self,
        event: Event,
        forecast: EnsembleForecast,
    ) -> list[TradeSignal]:
        """Score all brackets in a weather event against the forecast."""
        # Parse brackets from outcome names
        brackets: list[tuple[Outcome, BracketRange]] = []
        for market in event.markets:
            for outcome in market.outcomes:
                br = parse_bracket(outcome.name)
                if br is not None:
                    brackets.append((outcome, br))

        if len(brackets) < 3:
            return []

        sigma = self._sigma_f if forecast.unit == "fahrenheit" else self._sigma_c

        # Compute forecast-implied probabilities
        forecast_probs = ensemble_bracket_probabilities(forecast, brackets, sigma)
        if not forecast_probs:
            return []

        signals: list[TradeSignal] = []

        for outcome, bracket in brackets:
            forecast_p = forecast_probs.get(outcome.name, 0.0)
            # Clamp
            forecast_p = max(_PROB_FLOOR, min(_PROB_CEIL, forecast_p))

            market_p = outcome.price
            edge = forecast_p - market_p

            if abs(edge) < self._min_edge:
                continue

            # Direction
            direction = "BUY" if edge > 0 else "SELL"

            # Don't buy expensive outcomes
            if direction == "BUY" and market_p > MAX_ENTRY_PRICE:
                continue

            # Confidence: scales with edge magnitude
            # 8% = low, 25%+ = high
            confidence = min(abs(edge) / 0.25, 1.0)

            score = confidence * abs(edge)

            unit_sym = "°F" if forecast.unit == "fahrenheit" else "°C"
            bracket_label = _bracket_label(bracket, unit_sym)

            signals.append(TradeSignal(
                event=event,
                outcome=outcome,
                direction=direction,
                confidence=round(confidence, 3),
                score=round(score, 6),
                strategy=self.name,
                rationale=(
                    f"forecast={forecast_p*100:.0f}% market={market_p*100:.0f}% "
                    f"edge={edge*100:+.0f}pp | "
                    f"point={forecast.point_forecast:.0f}{unit_sym} "
                    f"range={min(forecast.member_highs):.0f}-{max(forecast.member_highs):.0f}{unit_sym} "
                    f"({forecast.n_members}mbr)"
                ),
            ))

        signals.sort(key=lambda s: s.score, reverse=True)
        return signals


def _bracket_label(b: BracketRange, unit: str) -> str:
    if b.low is None:
        return f"<={b.high:.0f}{unit}"
    if b.high is None:
        return f">={b.low:.0f}{unit}"
    if b.low == b.high:
        return f"{b.low:.0f}{unit}"
    return f"{b.low:.0f}-{b.high:.0f}{unit}"
