"""Improved momentum strategy using linear regression slope.

Instead of a single (last - first) delta, we fit a line through the recent
price series and use its slope as the momentum signal.  The R² of the fit
tells us how clean the trend is — a choppy series with the same net delta
scores lower than a steady climb.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone, timedelta

from polymarket_cli.models import Event, Outcome
from polymarket_cli.strategies.base import TradeSignal


class MomentumStrategy:
    name = "momentum"

    def __init__(self, lookback_hrs: int = 24):
        self._lookback_hrs = lookback_hrs

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _linreg(points: list[dict]) -> tuple[float, float]:
        """Return (slope_per_hour, r_squared) for a [{t, p}] series.

        Slope is expressed in price-units per hour so it's comparable
        across series of different lengths.
        """
        n = len(points)
        if n < 3:
            return 0.0, 0.0

        # Normalise timestamps to hours from first point
        t0 = points[0]["t"]
        xs = [(p["t"] - t0) / 3600 for p in points]
        ys = [p["p"] for p in points]

        x_mean = sum(xs) / n
        y_mean = sum(ys) / n

        ss_xy = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
        ss_xx = sum((x - x_mean) ** 2 for x in xs)
        ss_yy = sum((y - y_mean) ** 2 for y in ys)

        if ss_xx == 0:
            return 0.0, 0.0

        slope = ss_xy / ss_xx
        r_squared = (ss_xy ** 2) / (ss_xx * ss_yy) if ss_yy > 0 else 0.0

        return slope, r_squared

    @staticmethod
    def _is_expiring(event: Event) -> bool:
        """True if the event closes within 3 days or has anomalous volume."""
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
        if outcome.price < 0.05 or outcome.price > 0.95:
            return None

        # Trim series to lookback window
        if not series:
            return None
        cutoff = series[-1]["t"] - self._lookback_hrs * 3600
        window = [p for p in series if p["t"] >= cutoff]
        if len(window) < 4:
            return None

        slope, r2 = self._linreg(window)

        # Need a meaningful slope — at least 0.1¢/hr
        if abs(slope) < 0.001:
            return None

        # Direction: positive slope = BUY, negative = SELL (buy No side)
        direction = "BUY" if slope > 0 else "SELL"

        # Volume weight: log-scaled 24hr volume, capped
        vol_weight = math.log1p(event.volume_24hr) / math.log1p(1_000_000)
        vol_weight = min(vol_weight, 2.0)

        # Mid-range preference: peaks at 50¢ but gentler than original —
        # still useful at 20-80¢ range
        mid = 1 - abs(outcome.price - 0.5) * 1.5
        mid = max(mid, 0.05)

        # Raw score: |slope| * R² * volume * mid-range
        # R² weights clean trends higher than noisy ones
        raw = abs(slope) * max(r2, 0.1) * vol_weight * mid

        # Confidence: R² is a natural 0-1 confidence measure,
        # modulated by how much data we have
        data_confidence = min(len(window) / 12, 1.0)  # full confidence at 12+ pts
        confidence = r2 * data_confidence

        delta_24h = window[-1]["p"] - window[0]["p"]
        slope_cents = slope * 100

        return TradeSignal(
            event=event,
            outcome=outcome,
            direction=direction,
            confidence=round(confidence, 3),
            score=round(raw, 6),
            strategy=self.name,
            rationale=(
                f"{'▲' if slope > 0 else '▼'}{abs(slope_cents):.2f}¢/hr "
                f"(R²={r2:.2f}, 24h Δ={delta_24h*100:+.1f}¢)"
            ),
        )
