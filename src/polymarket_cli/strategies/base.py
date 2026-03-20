"""Base types for trading strategies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from polymarket_cli.models import Event, Outcome


@dataclass(frozen=True)
class TradeSignal:
    """A single actionable trade signal produced by a strategy."""

    event: Event
    outcome: Outcome
    direction: str          # "BUY" or "SELL"
    confidence: float       # 0.0–1.0 (how strong the signal is)
    score: float            # raw numeric score for ranking
    strategy: str           # name of the strategy that produced this
    rationale: str          # one-line human-readable explanation


class Strategy(Protocol):
    """Protocol that all strategies implement."""

    name: str

    def score(
        self,
        event: Event,
        outcome: Outcome,
        series: list[dict],
    ) -> TradeSignal | None:
        """Evaluate a single outcome and return a signal, or None if no trade.

        Args:
            event: The parent event (for volume/date context).
            outcome: The specific outcome to evaluate.
            series: Price history as [{t: unix_ts, p: float}, ...] sorted by time.
                    Typically ~166 points (7 days hourly).
        """
        ...
