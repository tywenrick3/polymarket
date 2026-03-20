"""Composite strategy — regime-aware weighted ensemble.

Runs every strategy on each outcome, then combines their signals.
Unlike a fixed-weight ensemble, the composite detects the current
volatility regime (quiet/normal/news) and shifts weights accordingly:

  QUIET:  mean-reversion 60%, momentum 10%, cross-market 25%, sma 5%
  NORMAL: mean-reversion 40%, momentum 30%, cross-market 25%, sma 5%
  NEWS:   momentum 55%, cross-market 25%, mean-reversion 15%, sma 5%

Scoring:
  1. Detect regime from the price series.
  2. Run all strategies, collect signals per (event, outcome).
  3. For each outcome, group signals by direction.
  4. Composite confidence = regime-weighted average × agreement bonus.
  5. Only surface signals where confidence > minimum threshold.
"""

from __future__ import annotations

from polymarket_cli.models import Event, Outcome
from polymarket_cli.strategies.base import TradeSignal
from polymarket_cli.strategies.momentum import MomentumStrategy
from polymarket_cli.strategies.sma import SMAStrategy
from polymarket_cli.strategies.mean_reversion import MeanReversionStrategy
from polymarket_cli.strategies.cross_market import CrossMarketStrategy
from polymarket_cli.strategies.regime import detect_regime, REGIME_WEIGHTS, Regime


# Fallback if regime detection doesn't have enough data
DEFAULT_WEIGHTS = REGIME_WEIGHTS[Regime.NORMAL]


class CompositeStrategy:
    name = "composite"

    def __init__(
        self,
        min_confidence: float = 0.15,
    ):
        self._min_confidence = min_confidence
        self._strategies = [
            MomentumStrategy(),
            SMAStrategy(),
            MeanReversionStrategy(),
        ]
        self._cross_market = CrossMarketStrategy()

    def _get_weights(self, series: list[dict]) -> dict[str, float]:
        """Return strategy weights based on current volatility regime."""
        regime = detect_regime(series)
        return REGIME_WEIGHTS.get(regime, DEFAULT_WEIGHTS)

    def score(
        self,
        event: Event,
        outcome: Outcome,
        series: list[dict],
    ) -> TradeSignal | None:
        """Run all per-outcome strategies and combine into a single signal."""
        signals: list[TradeSignal] = []
        for strat in self._strategies:
            sig = strat.score(event, outcome, series)
            if sig is not None:
                signals.append(sig)

        if not signals:
            return None

        weights = self._get_weights(series)
        return self._combine(event, outcome, signals, weights)

    def _combine(
        self,
        event: Event,
        outcome: Outcome,
        signals: list[TradeSignal],
        weights: dict[str, float],
    ) -> TradeSignal | None:
        """Combine multiple strategy signals into one composite signal."""
        # Group by direction
        buy_signals = [s for s in signals if s.direction == "BUY"]
        sell_signals = [s for s in signals if s.direction == "SELL"]

        # Choose the majority direction; on tie, pick higher total confidence
        if len(buy_signals) > len(sell_signals):
            chosen, direction = buy_signals, "BUY"
        elif len(sell_signals) > len(buy_signals):
            chosen, direction = sell_signals, "SELL"
        else:
            buy_conf = sum(s.confidence for s in buy_signals)
            sell_conf = sum(s.confidence for s in sell_signals)
            if buy_conf >= sell_conf:
                chosen, direction = buy_signals, "BUY"
            else:
                chosen, direction = sell_signals, "SELL"

        # Weighted average confidence using regime-aware weights
        total_weight = 0.0
        weighted_conf = 0.0
        for sig in chosen:
            w = weights.get(sig.strategy, 0.1)
            weighted_conf += sig.confidence * w
            total_weight += w

        if total_weight == 0:
            return None

        composite_conf = weighted_conf / total_weight

        # Agreement bonus
        n_total = len(signals)
        n_agree = len(chosen)
        if n_agree >= 2:
            composite_conf *= 1.0 + 0.2 * n_agree
        elif n_total >= 2:
            composite_conf *= 0.6

        composite_conf = min(composite_conf, 1.0)

        if composite_conf < self._min_confidence:
            return None

        composite_score = composite_conf * n_agree

        # Build rationale
        regime = detect_regime([])  # lightweight — just for label
        parts = []
        for sig in chosen:
            parts.append(f"{sig.strategy}: {sig.rationale}")

        disagreeing = [s for s in signals if s.direction != direction]
        for sig in disagreeing:
            parts.append(f"{sig.strategy}: DISAGREES -- {sig.rationale}")

        agreement_label = f"{n_agree}/{n_total} agree"

        return TradeSignal(
            event=event,
            outcome=outcome,
            direction=direction,
            confidence=round(composite_conf, 3),
            score=round(composite_score, 6),
            strategy=self.name,
            rationale=f"[{agreement_label}] " + " | ".join(parts),
        )

    def score_all(
        self,
        events: list[Event],
        series_map: dict[str, list[dict]],
        top_n: int = 5,
    ) -> list[TradeSignal]:
        """Score all outcomes across all events and return the top N signals.

        Includes both per-outcome strategy signals and cross-market signals
        for group events.
        """
        signals: list[TradeSignal] = []

        # Per-outcome scoring (momentum, SMA, mean-reversion via composite)
        for event in events:
            for market in event.markets:
                for outcome in market.outcomes:
                    if not outcome.token_id:
                        continue
                    series = series_map.get(outcome.token_id, [])
                    if len(series) < 10:
                        continue
                    sig = self.score(event, outcome, series)
                    if sig is not None:
                        signals.append(sig)

        # Cross-market scoring (group events — sum-to-one mispricing)
        for event in events:
            cross_sigs = self._cross_market.score_group(event, series_map)
            signals.extend(cross_sigs)

        signals.sort(key=lambda s: s.score, reverse=True)
        return signals[:top_n]
