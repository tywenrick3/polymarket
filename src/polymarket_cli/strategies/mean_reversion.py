"""Mean-reversion strategy for prediction markets (log-odds enhanced).

Prediction markets overreact to news — a sudden drop from 60¢ to 40¢ on
rumour often corrects back toward a fair value.  This strategy detects
outcomes that have deviated significantly from their rolling mean and
bets on reversion.

Key improvement: computes z-scores in **log-odds space** rather than raw
price space.  In log-odds, a move from 0.90→0.95 is as significant as
0.50→0.62 (both are ~1 logit unit).  This properly handles the bounded
0-1 nature of prediction market prices — raw z-scores underweight moves
near the boundaries.

Important caveat: not all deviations revert.  A price move backed by
genuine new information (high volume, sustained move) is a regime change,
not an overreaction.  We penalise signals where volume is abnormally high
to filter these out.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone, timedelta

from polymarket_cli.models import Event, Outcome
from polymarket_cli.strategies.base import TradeSignal

# Clamp bounds to avoid log(0) and log(inf)
_CLAMP_LO = 0.01
_CLAMP_HI = 0.99


def _logit(p: float) -> float:
    """Transform probability to log-odds: ln(p / (1-p))."""
    p = max(_CLAMP_LO, min(_CLAMP_HI, p))
    return math.log(p / (1 - p))


def _inv_logit(x: float) -> float:
    """Transform log-odds back to probability."""
    return 1 / (1 + math.exp(-x))


class MeanReversionStrategy:
    name = "mean_reversion"

    def __init__(self, lookback_hrs: int = 72, z_threshold: float = 1.5):
        self._lookback_hrs = lookback_hrs
        self._z_threshold = z_threshold

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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

    @staticmethod
    def _recent_move_speed(series: list[dict], hrs: int = 6) -> float:
        """How fast has the price moved in the last N hours?

        Returns absolute price change per hour.  High values indicate a
        sharp, fast move — more likely to be news-driven.
        """
        if len(series) < 2:
            return 0.0
        cutoff = series[-1]["t"] - hrs * 3600
        recent = [p for p in series if p["t"] >= cutoff]
        if len(recent) < 2:
            return 0.0
        delta = abs(recent[-1]["p"] - recent[0]["p"])
        span_hrs = (recent[-1]["t"] - recent[0]["t"]) / 3600
        return delta / max(span_hrs, 0.1)

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

        # Trim to lookback window
        if not series:
            return None
        cutoff = series[-1]["t"] - self._lookback_hrs * 3600
        window = [p for p in series if p["t"] >= cutoff]

        # Need enough history for meaningful stats
        if len(window) < 24:
            return None

        # Transform to log-odds space for z-score computation.
        # This makes deviations near 0/1 properly weighted.
        logit_prices = [_logit(p["p"]) for p in window]
        current_logit = logit_prices[-1]

        mean_logit = sum(logit_prices) / len(logit_prices)
        var_logit = sum((x - mean_logit) ** 2 for x in logit_prices) / len(logit_prices)
        std_logit = var_logit ** 0.5

        if std_logit < 0.05:
            # Market is essentially flat in log-odds space
            return None

        z_score = (current_logit - mean_logit) / std_logit

        if abs(z_score) < self._z_threshold:
            return None

        # Oversold (z < -1.5) → BUY (expect reversion up)
        # Overbought (z > +1.5) → SELL (expect reversion down)
        direction = "BUY" if z_score < 0 else "SELL"

        # Penalise if recent move was extremely fast — likely news, not overreaction
        move_speed = self._recent_move_speed(series, hrs=6)
        speed_penalty = 1.0
        if move_speed > 0.02:  # >2¢/hr is fast for prediction markets
            speed_penalty = 0.5
        if move_speed > 0.05:  # >5¢/hr is very fast — probably real news
            speed_penalty = 0.2

        # Volume weight — moderate volume is good (liquid), extreme is suspicious
        vol_weight = math.log1p(event.volume_24hr) / math.log1p(1_000_000)
        vol_weight = min(vol_weight, 2.0)

        # Raw score: driven by z-score magnitude
        raw = (abs(z_score) - self._z_threshold) * vol_weight * speed_penalty

        # Confidence: higher z = higher confidence, penalised by speed
        confidence = min((abs(z_score) - self._z_threshold) / 2.0, 1.0) * speed_penalty

        # Report in both spaces for readability
        current_price = window[-1]["p"]
        mean_price = _inv_logit(mean_logit)
        deviation_cents = (current_price - mean_price) * 100

        return TradeSignal(
            event=event,
            outcome=outcome,
            direction=direction,
            confidence=round(confidence, 3),
            score=round(raw, 6),
            strategy=self.name,
            rationale=(
                f"z={z_score:+.2f} logit "
                f"({abs(deviation_cents):.1f}¢ from {self._lookback_hrs}hr "
                f"mean of {mean_price*100:.1f}¢)"
            ),
        )
