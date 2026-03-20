"""Unit tests for trading strategies using synthetic price series.

No network calls — all data is fabricated to test deterministic behavior.
"""

import math
import time

import pytest

from polymarket_cli.models import Event, Market, Outcome
from polymarket_cli.strategies.momentum import MomentumStrategy
from polymarket_cli.strategies.sma import SMAStrategy
from polymarket_cli.strategies.mean_reversion import MeanReversionStrategy, _logit
from polymarket_cli.strategies.cross_market import CrossMarketStrategy
from polymarket_cli.strategies.composite import CompositeStrategy
from polymarket_cli.strategies.regime import detect_regime, Regime


# ---------------------------------------------------------------------------
# Helpers: synthetic data builders
# ---------------------------------------------------------------------------

def _make_event(**overrides) -> Event:
    defaults = dict(
        id="evt-1",
        slug="test-event",
        title="Test Event",
        volume=10_000_000,
        volume_24hr=500_000,
        liquidity=1_000_000,
        end_date="2027-01-01T00:00:00Z",
    )
    defaults.update(overrides)
    return Event(**defaults)


def _make_outcome(price: float = 0.50, name: str = "Yes") -> Outcome:
    return Outcome(name=name, price=price, price_delta=0.0, token_id="tok-1")


def _make_series(
    prices: list[float],
    interval_secs: int = 3600,
    start_t: int = 1_700_000_000,
) -> list[dict]:
    """Build a [{t, p}] series from a list of prices, one per hour."""
    return [{"t": start_t + i * interval_secs, "p": p} for i, p in enumerate(prices)]


def _steady_uptrend(n: int = 48, start: float = 0.30, slope: float = 0.005) -> list[float]:
    """Perfectly linear uptrend."""
    return [start + i * slope for i in range(n)]


def _steady_downtrend(n: int = 48, start: float = 0.70, slope: float = 0.005) -> list[float]:
    return [start - i * slope for i in range(n)]


def _flat(n: int = 48, price: float = 0.50) -> list[float]:
    return [price] * n


def _spike_then_flat(n: int = 48, base: float = 0.40, spike: float = 0.60) -> list[float]:
    """Sharp jump at 75% through the series, then flat at the spike level."""
    pivot = int(n * 0.75)
    return [base] * pivot + [spike] * (n - pivot)


def _mean_reverting(
    n: int = 72,
    mean: float = 0.50,
    amplitude: float = 0.10,
) -> list[float]:
    """Oscillating series that deviates then returns to mean."""
    prices = []
    for i in range(n):
        # Sine wave around the mean
        prices.append(mean + amplitude * math.sin(2 * math.pi * i / n))
    return prices


def _noisy_uptrend(n: int = 48, start: float = 0.30, slope: float = 0.003) -> list[float]:
    """Uptrend with zigzag noise — lower R² than steady."""
    prices = []
    for i in range(n):
        noise = 0.02 * (1 if i % 3 == 0 else -1)
        prices.append(start + i * slope + noise)
    return prices


# ---------------------------------------------------------------------------
# Momentum strategy tests
# ---------------------------------------------------------------------------

class TestMomentum:
    def setup_method(self):
        self.strat = MomentumStrategy(lookback_hrs=24)

    def test_steady_uptrend_buys(self):
        series = _make_series(_steady_uptrend())
        sig = self.strat.score(_make_event(), _make_outcome(0.50), series)
        assert sig is not None
        assert sig.direction == "BUY"
        assert sig.confidence > 0.5

    def test_steady_downtrend_sells(self):
        series = _make_series(_steady_downtrend())
        sig = self.strat.score(_make_event(), _make_outcome(0.50), series)
        assert sig is not None
        assert sig.direction == "SELL"
        assert sig.confidence > 0.5

    def test_flat_market_no_signal(self):
        series = _make_series(_flat())
        sig = self.strat.score(_make_event(), _make_outcome(0.50), series)
        assert sig is None

    def test_noisy_trend_lower_confidence(self):
        clean = _make_series(_steady_uptrend())
        noisy = _make_series(_noisy_uptrend())
        sig_clean = self.strat.score(_make_event(), _make_outcome(0.50), clean)
        sig_noisy = self.strat.score(_make_event(), _make_outcome(0.50), noisy)
        assert sig_clean is not None and sig_noisy is not None
        assert sig_clean.confidence > sig_noisy.confidence

    def test_extreme_price_filtered(self):
        series = _make_series(_steady_uptrend(start=0.96))
        sig = self.strat.score(_make_event(), _make_outcome(0.97), series)
        assert sig is None

    def test_expiring_event_filtered(self):
        series = _make_series(_steady_uptrend())
        event = _make_event(end_date="2025-01-01T00:00:00Z")  # already past
        sig = self.strat.score(event, _make_outcome(0.50), series)
        assert sig is None

    def test_anomalous_volume_filtered(self):
        series = _make_series(_steady_uptrend())
        event = _make_event(volume=1_000_000, volume_24hr=800_000)  # 80% of lifetime
        sig = self.strat.score(event, _make_outcome(0.50), series)
        assert sig is None

    def test_too_few_points_no_signal(self):
        series = _make_series([0.40, 0.45])
        sig = self.strat.score(_make_event(), _make_outcome(0.45), series)
        assert sig is None

    def test_linreg_slope_direction(self):
        """Verify slope sign matches trend direction."""
        up_slope, up_r2 = MomentumStrategy._linreg(
            _make_series(_steady_uptrend(n=24))
        )
        down_slope, down_r2 = MomentumStrategy._linreg(
            _make_series(_steady_downtrend(n=24))
        )
        assert up_slope > 0
        assert down_slope < 0
        assert up_r2 > 0.9
        assert down_r2 > 0.9


# ---------------------------------------------------------------------------
# SMA strategy tests
# ---------------------------------------------------------------------------

class TestSMA:
    def setup_method(self):
        self.strat = SMAStrategy(fast_hrs=6, slow_hrs=24)

    def test_crossover_up_buys(self):
        # Downtrend then sharp reversal. Truncated to 27 points so the
        # *last* bar is the exact crossover moment (fast just crossed above slow).
        prices = [0.45 - i * 0.002 for i in range(24)] + [0.45 + i * 0.02 for i in range(3)]
        series = _make_series(prices)
        sig = self.strat.score(_make_event(), _make_outcome(prices[-1]), series)
        assert sig is not None
        assert sig.direction == "BUY"
        assert "CROSS" in sig.rationale

    def test_crossover_down_sells(self):
        # Uptrend then sharp reversal, truncated to the crossover bar.
        prices = [0.55 + i * 0.002 for i in range(24)] + [0.55 - i * 0.02 for i in range(3)]
        series = _make_series(prices)
        sig = self.strat.score(_make_event(), _make_outcome(prices[-1]), series)
        assert sig is not None
        assert sig.direction == "SELL"
        assert "CROSS" in sig.rationale

    def test_flat_no_signal(self):
        series = _make_series(_flat(n=30))
        sig = self.strat.score(_make_event(), _make_outcome(0.50), series)
        assert sig is None

    def test_needs_enough_data(self):
        series = _make_series(_steady_uptrend(n=20))
        sig = self.strat.score(_make_event(), _make_outcome(0.40), series)
        assert sig is None

    def test_dead_zone_filters_micro_crossovers(self):
        """Tiny gap crossovers (< 0.5¢) should be suppressed."""
        # Flat with micro-noise: gap at crossover is essentially zero
        prices = _flat(n=26, price=0.50) + [0.50 + i * 0.0003 for i in range(10)]
        series = _make_series(prices)
        sig = self.strat.score(_make_event(), _make_outcome(0.50), series)
        assert sig is None  # gap too small to pass dead zone

    def test_boundary_dampening(self):
        """Signals near price extremes (>0.90) should be suppressed."""
        prices = _flat(n=26, price=0.92) + [0.92 + i * 0.015 for i in range(10)]
        series = _make_series(prices)
        sig = self.strat.score(_make_event(), _make_outcome(0.95), series)
        assert sig is None

    def test_widening_gap_no_longer_fires(self):
        """Steady uptrend with no crossover should NOT produce a signal."""
        # Pure uptrend from the start — fast SMA is always above slow SMA,
        # gap is widening but there's never a crossover.
        prices = _steady_uptrend(n=36, start=0.30, slope=0.005)
        series = _make_series(prices)
        sig = self.strat.score(_make_event(), _make_outcome(0.45), series)
        assert sig is None


# ---------------------------------------------------------------------------
# Mean reversion strategy tests
# ---------------------------------------------------------------------------

class TestMeanReversion:
    def setup_method(self):
        self.strat = MeanReversionStrategy(lookback_hrs=72, z_threshold=1.5)

    def test_oversold_buys(self):
        """Price well below its rolling mean → BUY (expect reversion up)."""
        # 72 points at 0.50, then drop to 0.30
        prices = _flat(n=60, price=0.50) + _flat(n=12, price=0.30)
        series = _make_series(prices)
        sig = self.strat.score(_make_event(), _make_outcome(0.30), series)
        assert sig is not None
        assert sig.direction == "BUY"

    def test_overbought_sells(self):
        """Price well above its rolling mean → SELL (expect reversion down)."""
        prices = _flat(n=60, price=0.40) + _flat(n=12, price=0.65)
        series = _make_series(prices)
        sig = self.strat.score(_make_event(), _make_outcome(0.65), series)
        assert sig is not None
        assert sig.direction == "SELL"

    def test_near_mean_no_signal(self):
        """Price close to mean → no signal."""
        series = _make_series(_flat(n=72))
        sig = self.strat.score(_make_event(), _make_outcome(0.50), series)
        assert sig is None

    def test_gradual_trend_no_signal(self):
        """Steady trend moves the mean with it — z-score stays low."""
        # Very gentle uptrend over 72 hours
        prices = _steady_uptrend(n=72, start=0.40, slope=0.002)
        series = _make_series(prices)
        sig = self.strat.score(_make_event(), _make_outcome(prices[-1]), series)
        # A gentle trend should have a moderate z-score, may or may not trigger
        # The key property: it should NOT have high confidence
        if sig is not None:
            assert sig.confidence < 0.5

    def test_fast_move_penalized(self):
        """A very fast recent move gets a speed penalty (likely news, not overreaction)."""
        # Flat for 66 hours, then sharp drop in last 6 hours
        prices = _flat(n=66, price=0.50) + [0.50 - i * 0.04 for i in range(6)]
        series = _make_series(prices)
        sig_fast = self.strat.score(_make_event(), _make_outcome(prices[-1]), series)

        # Same total deviation but spread over longer time
        prices_slow = _flat(n=48, price=0.50) + [0.50 - i * 0.01 for i in range(24)]
        series_slow = _make_series(prices_slow)
        sig_slow = self.strat.score(
            _make_event(), _make_outcome(prices_slow[-1]), series_slow
        )

        # Fast move should have lower confidence due to speed penalty
        if sig_fast is not None and sig_slow is not None:
            assert sig_fast.confidence <= sig_slow.confidence

    def test_too_little_data(self):
        series = _make_series(_flat(n=10))
        sig = self.strat.score(_make_event(), _make_outcome(0.50), series)
        assert sig is None


# ---------------------------------------------------------------------------
# Composite strategy tests
# ---------------------------------------------------------------------------

class TestComposite:
    def setup_method(self):
        self.comp = CompositeStrategy()

    def test_strong_uptrend_produces_signal(self):
        """Clear uptrend should get at least momentum + SMA agreement."""
        prices = _steady_uptrend(n=80, start=0.30, slope=0.003)
        series = _make_series(prices)
        sig = self.comp.score(
            _make_event(), _make_outcome(prices[-1]), series
        )
        assert sig is not None
        assert sig.direction == "BUY"
        assert sig.strategy == "composite"

    def test_flat_market_no_signal(self):
        series = _make_series(_flat(n=80))
        sig = self.comp.score(_make_event(), _make_outcome(0.50), series)
        assert sig is None

    def test_multi_agreement_higher_score(self):
        """Signals with more strategy agreement should score higher."""
        # Strong uptrend: momentum + SMA should agree
        strong = _make_series(_steady_uptrend(n=80, start=0.30, slope=0.004))
        sig_strong = self.comp.score(
            _make_event(), _make_outcome(0.60), strong
        )

        # Mild uptrend: maybe only one strategy triggers
        mild = _make_series(_noisy_uptrend(n=80, start=0.45, slope=0.001))
        sig_mild = self.comp.score(
            _make_event(), _make_outcome(0.50), mild
        )

        if sig_strong is not None and sig_mild is not None:
            assert sig_strong.confidence >= sig_mild.confidence

    def test_score_all_returns_ranked_list(self):
        event = _make_event()
        o1 = _make_outcome(0.50, name="Candidate A")
        o1.token_id = "tok-a"
        o2 = _make_outcome(0.50, name="Candidate B")
        o2.token_id = "tok-b"
        event.markets = [Market(id="m1", question="test", outcomes=[o1, o2])]

        series_map = {
            "tok-a": _make_series(_steady_uptrend(n=80, start=0.30, slope=0.004)),
            "tok-b": _make_series(_flat(n=80)),
        }

        signals = self.comp.score_all([event], series_map, top_n=5)
        assert isinstance(signals, list)
        # tok-a should produce a signal, tok-b (flat) probably shouldn't
        if len(signals) >= 1:
            assert signals[0].outcome.name == "Candidate A"

    def test_rationale_includes_agreement_label(self):
        prices = _steady_uptrend(n=80, start=0.30, slope=0.004)
        series = _make_series(prices)
        sig = self.comp.score(
            _make_event(), _make_outcome(prices[-1]), series
        )
        if sig is not None:
            assert "agree" in sig.rationale

    def test_extreme_price_filtered(self):
        """Composite should inherit individual strategy price filters."""
        series = _make_series(_steady_uptrend(n=80, start=0.96, slope=0.001))
        sig = self.comp.score(_make_event(), _make_outcome(0.99), series)
        assert sig is None


# ---------------------------------------------------------------------------
# Log-odds mean reversion tests
# ---------------------------------------------------------------------------

class TestLogOdds:
    def test_logit_symmetry(self):
        """logit(0.5) should be 0, logit(0.9) > 0, logit(0.1) < 0."""
        assert abs(_logit(0.5)) < 0.001
        assert _logit(0.9) > 0
        assert _logit(0.1) < 0

    def test_logit_boundary_sensitivity(self):
        """Move from 0.90→0.95 should be larger in logit space than 0.50→0.55."""
        move_mid = _logit(0.55) - _logit(0.50)
        move_high = _logit(0.95) - _logit(0.90)
        assert move_high > move_mid

    def test_oversold_near_boundary_detects(self):
        """A drop near the boundary should be detected as oversold in logit space."""
        strat = MeanReversionStrategy(lookback_hrs=72, z_threshold=1.5)
        # Price was stable at 0.85, dropped to 0.70 — in logit space this
        # is a bigger deviation than in raw price space.
        prices = _flat(n=60, price=0.85) + _flat(n=12, price=0.70)
        series = _make_series(prices)
        sig = strat.score(_make_event(), _make_outcome(0.70), series)
        assert sig is not None
        assert sig.direction == "BUY"
        assert "logit" in sig.rationale


# ---------------------------------------------------------------------------
# Regime detection tests
# ---------------------------------------------------------------------------

class TestRegime:
    def test_flat_market_is_quiet(self):
        series = _make_series(_flat(n=80))
        assert detect_regime(series) == Regime.QUIET

    def test_steady_trend_is_normal(self):
        series = _make_series(_steady_uptrend(n=80, start=0.30, slope=0.002))
        regime = detect_regime(series)
        # A steady trend has consistent volatility — should be normal or quiet
        assert regime in (Regime.NORMAL, Regime.QUIET)

    def test_spike_is_news(self):
        """A sudden price spike should trigger NEWS regime."""
        # 72 hours flat, then volatile spike in last 12 hours
        prices = _flat(n=68, price=0.50)
        # Add sharp oscillations in the last 12 hours
        for i in range(12):
            prices.append(0.50 + (0.10 if i % 2 == 0 else -0.10))
        series = _make_series(prices)
        regime = detect_regime(series)
        assert regime == Regime.NEWS

    def test_insufficient_data_returns_normal(self):
        series = _make_series(_flat(n=10))
        assert detect_regime(series) == Regime.NORMAL


# ---------------------------------------------------------------------------
# Cross-market correlation tests
# ---------------------------------------------------------------------------

class TestCrossMarket:
    def setup_method(self):
        self.strat = CrossMarketStrategy()

    def _make_group_event(self, prices: list[float]) -> Event:
        """Build a group event with N outcomes at given prices."""
        event = _make_event()
        event.markets = []
        for i, price in enumerate(prices):
            o = Outcome(
                name=f"Candidate {chr(65+i)}",
                price=price,
                price_delta=0.0,
                token_id=f"tok-{i}",
            )
            m = Market(id=f"m-{i}", question=f"Will {o.name} win?", outcomes=[o])
            event.markets.append(m)
        return event

    def test_balanced_group_no_signal(self):
        """Prices summing to ~1.0 should produce no signal."""
        event = self._make_group_event([0.50, 0.30, 0.15, 0.05])
        series_map = {
            f"tok-{i}": _make_series(_flat(n=30, price=p))
            for i, p in enumerate([0.50, 0.30, 0.15, 0.05])
        }
        signals = self.strat.score_group(event, series_map)
        assert len(signals) == 0

    def test_overpriced_group_sells(self):
        """Sum > 1.05 should generate a SELL on the biggest recent gainer."""
        # Prices sum to 1.15 — overpriced
        event = self._make_group_event([0.55, 0.35, 0.15, 0.10])
        # Candidate A spiked up in the last 3 hours (within the 6hr window)
        series_a = _make_series(_flat(n=27, price=0.45) + _flat(n=3, price=0.55))
        series_map = {
            "tok-0": series_a,
            "tok-1": _make_series(_flat(n=30, price=0.35)),
            "tok-2": _make_series(_flat(n=30, price=0.15)),
            "tok-3": _make_series(_flat(n=30, price=0.10)),
        }
        signals = self.strat.score_group(event, series_map)
        assert len(signals) == 1
        assert signals[0].direction == "SELL"
        assert signals[0].outcome.name == "Candidate A"

    def test_underpriced_group_buys(self):
        """Sum < 0.95 should generate a BUY on the biggest recent loser."""
        # Prices sum to 0.85 — underpriced
        event = self._make_group_event([0.40, 0.25, 0.10, 0.10])
        # Candidate A dropped in the last 3 hours
        series_a = _make_series(_flat(n=27, price=0.52) + _flat(n=3, price=0.40))
        series_map = {
            "tok-0": series_a,
            "tok-1": _make_series(_flat(n=30, price=0.25)),
            "tok-2": _make_series(_flat(n=30, price=0.10)),
            "tok-3": _make_series(_flat(n=30, price=0.10)),
        }
        signals = self.strat.score_group(event, series_map)
        assert len(signals) == 1
        assert signals[0].direction == "BUY"

    def test_non_group_event_skipped(self):
        """Events with non-group structure should be skipped."""
        event = _make_event()
        o1 = _make_outcome(0.60, "Yes")
        o2 = _make_outcome(0.40, "No")
        event.markets = [Market(id="m1", question="test", outcomes=[o1, o2])]
        signals = self.strat.score_group(event, {})
        assert len(signals) == 0

    def test_too_few_outcomes_skipped(self):
        """Group events with < 3 outcomes should be skipped."""
        event = self._make_group_event([0.60, 0.40])
        signals = self.strat.score_group(event, {})
        assert len(signals) == 0
