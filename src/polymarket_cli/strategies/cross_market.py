"""Cross-market correlation strategy for group events.

In group events (e.g. "Who wins the 2028 election?"), each outcome is a
separate market with a Yes/No price.  The Yes prices across all outcomes
should sum to approximately 1.0 (since exactly one candidate wins).

When the sum deviates — one candidate spikes without others dropping
proportionally — there's a temporary mispricing.  This strategy detects
those imbalances and bets on convergence.

This is a *structural* signal, not a statistical one.  The sum-to-one
constraint is mechanical, so mispricings tend to correct within hours
as arbitrageurs step in.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone, timedelta

from polymarket_cli.models import Event, Market, Outcome
from polymarket_cli.strategies.base import TradeSignal

# How far the sum can deviate from 1.0 before we consider it mispriced.
SUM_THRESHOLD = 0.05  # 5¢

# Minimum number of outcomes in a group for this strategy to apply.
MIN_GROUP_SIZE = 3


class CrossMarketStrategy:
    name = "cross_market"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_group_event(event: Event) -> bool:
        """True if this event has multiple single-outcome markets (group structure)."""
        if len(event.markets) < MIN_GROUP_SIZE:
            return False
        # Group events have markets where each market has a single collapsed outcome
        # (the groupItemTitle-based outcome from gamma.py parsing)
        for m in event.markets:
            if len(m.outcomes) != 1:
                return False
        return True

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
    # Core: score a single outcome within a group event
    # ------------------------------------------------------------------

    def score_group(
        self,
        event: Event,
        series_map: dict[str, list[dict]],
    ) -> list[TradeSignal]:
        """Score all outcomes in a group event for sum-to-one mispricing.

        Returns a list of signals (possibly empty).
        """
        if not self._is_group_event(event):
            return []
        if self._is_expiring(event):
            return []

        # Collect current prices and recent price histories
        outcomes: list[Outcome] = []
        for m in event.markets:
            if m.outcomes and m.outcomes[0].token_id:
                outcomes.append(m.outcomes[0])

        if len(outcomes) < MIN_GROUP_SIZE:
            return []

        # Current sum of probabilities
        price_sum = sum(o.price for o in outcomes)
        deviation = price_sum - 1.0

        if abs(deviation) < SUM_THRESHOLD:
            return []  # sum is close enough to 1.0

        # Check historical sum stability using series data.
        # If we have series for enough outcomes, compute the sum at each
        # hourly point and see if the current deviation is unusual.
        outcome_series = {}
        for o in outcomes:
            s = series_map.get(o.token_id, [])
            if len(s) >= 24:
                outcome_series[o.token_id] = s

        # Need series for at least half the outcomes to compute meaningful sums
        if len(outcome_series) < len(outcomes) // 2:
            return []

        # Find which outcome(s) moved the most recently — they're the ones
        # driving the imbalance.
        recent_moves: list[tuple[Outcome, float]] = []
        for o in outcomes:
            s = series_map.get(o.token_id, [])
            if len(s) >= 6:
                move = s[-1]["p"] - s[-6]["p"]  # 6hr move
                recent_moves.append((o, move))

        if not recent_moves:
            return []

        # Sort by absolute recent move (biggest mover first)
        recent_moves.sort(key=lambda x: abs(x[1]), reverse=True)

        signals: list[TradeSignal] = []

        if deviation > SUM_THRESHOLD:
            # Sum > 1.05: market is overpriced overall.
            # The outcome that spiked the most recently is likely overpriced.
            # Signal: SELL the biggest recent gainer.
            for o, move in recent_moves:
                if move > 0.01 and o.price > 0.05:
                    confidence = min(abs(deviation) / 0.15, 1.0)
                    vol_weight = math.log1p(event.volume_24hr) / math.log1p(1_000_000)
                    vol_weight = min(vol_weight, 2.0)

                    signals.append(TradeSignal(
                        event=event,
                        outcome=o,
                        direction="SELL",
                        confidence=round(confidence, 3),
                        score=round(confidence * vol_weight, 6),
                        strategy=self.name,
                        rationale=(
                            f"sum={price_sum:.2f} (>{1+SUM_THRESHOLD:.2f}), "
                            f"{o.name} +{move*100:.1f}¢ in 6hr"
                        ),
                    ))
                    break  # only signal the biggest mover

        elif deviation < -SUM_THRESHOLD:
            # Sum < 0.95: market is underpriced overall.
            # The outcome that dropped the most recently is likely underpriced.
            # Signal: BUY the biggest recent loser.
            for o, move in recent_moves:
                if move < -0.01 and o.price > 0.05 and o.price < 0.95:
                    confidence = min(abs(deviation) / 0.15, 1.0)
                    vol_weight = math.log1p(event.volume_24hr) / math.log1p(1_000_000)
                    vol_weight = min(vol_weight, 2.0)

                    signals.append(TradeSignal(
                        event=event,
                        outcome=o,
                        direction="BUY",
                        confidence=round(confidence, 3),
                        score=round(confidence * vol_weight, 6),
                        strategy=self.name,
                        rationale=(
                            f"sum={price_sum:.2f} (<{1-SUM_THRESHOLD:.2f}), "
                            f"{o.name} {move*100:.1f}¢ in 6hr"
                        ),
                    ))
                    break

        return signals

    def score(
        self,
        event: Event,
        outcome: Outcome,
        series: list[dict],
    ) -> TradeSignal | None:
        """Protocol-compatible single-outcome scorer.

        Cross-market needs all outcomes at once, so this is a no-op.
        Use score_group() directly or let CompositeStrategy handle it.
        """
        return None
