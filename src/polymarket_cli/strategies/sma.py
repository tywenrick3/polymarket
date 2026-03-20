"""SMA crossover strategy.

Uses two simple moving averages — a fast (short) and slow (long) window.
Signals ONLY on actual crossovers (fast crossing above/below slow), NOT
on widening gaps.  A dead-zone filter suppresses micro-crossovers from
flat-market noise, and boundary dampening avoids signalling near 0/1.

Previous version fired on widening gaps too, which generated masses of
false signals in prediction markets (26% win rate).  Crossover-only with
dead zone should be far more selective.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone, timedelta

from polymarket_cli.models import Event, Outcome
from polymarket_cli.strategies.base import TradeSignal

# Minimum gap (in price units) at the moment of crossover.
# Filters out noise-driven micro-crossovers during flat periods.
# Kept at 0.5¢ because crossovers inherently start with small gaps —
# the gap only widens after.  The real filtering comes from
# crossover-only + boundary dampening + volatility normalization.
DEAD_ZONE = 0.005  # 0.5¢

# Boundary dampening: suppress signals near extremes where there's
# almost no upside for trend-following.
PRICE_FLOOR = 0.10
PRICE_CEIL = 0.90


class SMAStrategy:
    name = "sma"

    def __init__(self, fast_hrs: int = 6, slow_hrs: int = 24):
        self._fast_hrs = fast_hrs
        self._slow_hrs = slow_hrs

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _sma(prices: list[float], window: int) -> float | None:
        """Simple moving average of the last `window` prices."""
        if len(prices) < window:
            return None
        return sum(prices[-window:]) / window

    @staticmethod
    def _std(prices: list[float], window: int) -> float:
        """Standard deviation of the last `window` prices."""
        if len(prices) < window:
            return 0.0
        seg = prices[-window:]
        mean = sum(seg) / len(seg)
        var = sum((p - mean) ** 2 for p in seg) / len(seg)
        return var ** 0.5

    @staticmethod
    def _is_expiring(event: Event) -> bool:
        if event.volume_24hr > event.volume * 0.6:
            return True
        if event.end_date:
            try:
                closes = datetime.fromisoformat(
                    event.end_date.replace("Z", "+00:00")
                )
                if closes - datetime.now(timezone.utc) < timedelta(days=3):
                    return True
            except ValueError:
                pass
        return False

    # ------------------------------------------------------------------
    # Core scoring
    # ------------------------------------------------------------------

    def score(
        self,
        event: Event,
        outcome: Outcome,
        series: list[dict],
    ) -> TradeSignal | None:
        if self._is_expiring(event):
            return None

        # Boundary dampening — no upside for trend-following near extremes
        if outcome.price < PRICE_FLOOR or outcome.price > PRICE_CEIL:
            return None

        # We need at least slow_window + a few extra points for crossover detection
        prices = [p["p"] for p in series]
        if len(prices) < self._slow_hrs + 2:
            return None

        fast = self._sma(prices, self._fast_hrs)
        slow = self._sma(prices, self._slow_hrs)
        if fast is None or slow is None:
            return None

        # Previous-bar SMAs for crossover detection
        prev_prices = prices[:-1]
        prev_fast = self._sma(prev_prices, self._fast_hrs)
        prev_slow = self._sma(prev_prices, self._slow_hrs)
        if prev_fast is None or prev_slow is None:
            return None

        gap = fast - slow
        prev_gap = prev_fast - prev_slow

        # CROSSOVER ONLY — no more widening-gap signals.
        # A crossover is when the fast and slow SMAs swap sides.
        crossed_up = prev_gap <= 0 and gap > 0
        crossed_down = prev_gap >= 0 and gap < 0

        if not (crossed_up or crossed_down):
            return None

        # Dead zone: ignore crossovers where the gap is tiny (noise).
        if abs(gap) < DEAD_ZONE:
            return None

        direction = "BUY" if crossed_up else "SELL"

        # Normalise gap by volatility to get signal strength
        vol = self._std(prices, self._slow_hrs)
        if vol < 0.001:
            vol = 0.001
        normalised_gap = abs(gap) / vol

        # Volume weight
        vol_weight = math.log1p(event.volume_24hr) / math.log1p(1_000_000)
        vol_weight = min(vol_weight, 2.0)

        raw = normalised_gap * vol_weight

        # Confidence: crossovers that are >2σ are high confidence
        confidence = min(normalised_gap / 3.0, 1.0)

        gap_cents = gap * 100
        cross_label = "CROSS ▲ " if crossed_up else "CROSS ▼ "

        return TradeSignal(
            event=event,
            outcome=outcome,
            direction=direction,
            confidence=round(confidence, 3),
            score=round(raw, 6),
            strategy=self.name,
            rationale=(
                f"{cross_label}{self._fast_hrs}hr SMA {'>' if gap > 0 else '<'} "
                f"{self._slow_hrs}hr SMA by {abs(gap_cents):.1f}¢ "
                f"({normalised_gap:.1f}σ)"
            ),
        )
