"""Unit tests for the weather temperature strategy.

All data is synthetic — no network calls, no Open-Meteo API.
"""

import math
from datetime import date

import pytest

from polymarket_cli.models import Event, Market, Outcome
from polymarket_cli.strategies.weather import (
    WeatherStrategy,
    parse_bracket,
    BracketRange,
    ensemble_bracket_probabilities,
    _bracket_prob_for_member,
    _gaussian_cdf,
)
from polymarket_cli.strategies.base import TradeSignal
from polymarket_cli.api.weather import EnsembleForecast
from polymarket_cli.weather_cities import parse_weather_event, is_weather_event


# ---------------------------------------------------------------------------
# Bracket parsing — real Polymarket outcome names
# ---------------------------------------------------------------------------

class TestBracketParsing:
    # Fahrenheit 2-degree ranges (US cities)
    def test_fahrenheit_range(self):
        assert parse_bracket("32-33°F") == BracketRange(32.0, 33.0)

    def test_fahrenheit_range_wide(self):
        assert parse_bracket("44-45°F") == BracketRange(44.0, 45.0)

    def test_fahrenheit_or_below(self):
        assert parse_bracket("31°F or below") == BracketRange(None, 31.0)

    def test_fahrenheit_or_higher(self):
        assert parse_bracket("42°F or higher") == BracketRange(42.0, None)

    # Celsius single-degree (non-US cities)
    def test_celsius_single(self):
        assert parse_bracket("9°C") == BracketRange(9.0, 9.0)

    def test_celsius_negative(self):
        assert parse_bracket("-5°C") == BracketRange(-5.0, -5.0)

    def test_celsius_or_below(self):
        assert parse_bracket("13°C or below") == BracketRange(None, 13.0)

    def test_celsius_or_higher(self):
        assert parse_bracket("18°C or higher") == BracketRange(18.0, None)

    def test_celsius_large(self):
        assert parse_bracket("42°C") == BracketRange(42.0, 42.0)

    # Edge cases
    def test_unparseable(self):
        assert parse_bracket("sunny") is None
        assert parse_bracket("") is None
        assert parse_bracket("Yes") is None

    # Celsius range (e.g., temperature increase markets — different format)
    def test_celsius_range(self):
        # Not currently on weather markets but the parser should handle it
        assert parse_bracket("10-11°C") == BracketRange(10.0, 11.0)


# ---------------------------------------------------------------------------
# Gaussian math
# ---------------------------------------------------------------------------

class TestGaussianMath:
    def test_cdf_at_mean(self):
        assert abs(_gaussian_cdf(50.0, 50.0, 2.0) - 0.5) < 0.001

    def test_cdf_far_right(self):
        assert _gaussian_cdf(100.0, 50.0, 2.0) > 0.999

    def test_cdf_far_left(self):
        assert _gaussian_cdf(0.0, 50.0, 2.0) < 0.001

    def test_bracket_prob_centered(self):
        """Member at 50, bracket [45, 55] should capture almost everything."""
        p = _bracket_prob_for_member(50.0, 2.0, BracketRange(45.0, 55.0))
        assert p > 0.98

    def test_bracket_prob_far_away(self):
        """Member at 50, bracket [70, 80] should be near zero."""
        p = _bracket_prob_for_member(50.0, 2.0, BracketRange(70.0, 80.0))
        assert p < 0.001

    def test_single_degree_bracket(self):
        """Single degree [9, 9] treated as [8.5, 9.5]."""
        p = _bracket_prob_for_member(9.0, 1.0, BracketRange(9.0, 9.0))
        # Should be ~38% for N(9, 1) in [8.5, 9.5]
        assert 0.3 < p < 0.5

    def test_open_low_bracket(self):
        """Member at 30, bracket (-inf, 32] should be high."""
        p = _bracket_prob_for_member(30.0, 2.0, BracketRange(None, 32.0))
        assert p > 0.85

    def test_open_high_bracket(self):
        """Member at 75, bracket [71, +inf) should be high."""
        p = _bracket_prob_for_member(75.0, 2.0, BracketRange(71.0, None))
        assert p > 0.95

    def test_probabilities_sum_to_one_celsius(self):
        """Exhaustive single-degree Celsius brackets should sum to ~1.0."""
        brackets = (
            [BracketRange(None, 5.0)]
            + [BracketRange(float(i), float(i)) for i in range(6, 14)]
            + [BracketRange(14.0, None)]
        )
        total = sum(_bracket_prob_for_member(10.0, 1.5, b) for b in brackets)
        assert abs(total - 1.0) < 0.01

    def test_probabilities_sum_to_one_fahrenheit(self):
        """Exhaustive 2-degree Fahrenheit brackets should sum to ~1.0."""
        brackets = (
            [BracketRange(None, 31.0)]
            + [BracketRange(float(i), float(i + 1)) for i in range(32, 42, 2)]
            + [BracketRange(42.0, None)]
        )
        total = sum(_bracket_prob_for_member(36.0, 1.5, b) for b in brackets)
        assert abs(total - 1.0) < 0.01


# ---------------------------------------------------------------------------
# Ensemble probabilities
# ---------------------------------------------------------------------------

class TestEnsembleProbabilities:
    def _make_forecast(self, highs: list[float], unit: str = "celsius") -> EnsembleForecast:
        return EnsembleForecast(
            city_name="Test",
            target_date=date(2026, 3, 18),
            member_highs=highs,
            point_forecast=sum(highs) / len(highs),
            n_members=len(highs),
            unit=unit,
        )

    def _make_celsius_brackets(self) -> list[tuple[Outcome, BracketRange]]:
        """London-style: single-degree Celsius with open ends."""
        specs = [
            ("8°C or below", BracketRange(None, 8.0)),
            ("9°C", BracketRange(9.0, 9.0)),
            ("10°C", BracketRange(10.0, 10.0)),
            ("11°C", BracketRange(11.0, 11.0)),
            ("12°C", BracketRange(12.0, 12.0)),
            ("13°C", BracketRange(13.0, 13.0)),
            ("14°C", BracketRange(14.0, 14.0)),
            ("15°C", BracketRange(15.0, 15.0)),
            ("16°C", BracketRange(16.0, 16.0)),
            ("17°C", BracketRange(17.0, 17.0)),
            ("18°C or higher", BracketRange(18.0, None)),
        ]
        return [
            (Outcome(name=n, price=0.0, price_delta=0.0, token_id=f"tok-{i}"), br)
            for i, (n, br) in enumerate(specs)
        ]

    def test_concentrated_ensemble(self):
        """All members at 12°C should heavily weight the 12°C bracket."""
        forecast = self._make_forecast([12.0] * 31)
        brackets = self._make_celsius_brackets()
        probs = ensemble_bracket_probabilities(forecast, brackets, sigma=1.0)
        assert probs["12°C"] > 0.3  # highest single bracket

    def test_spread_ensemble(self):
        """Wide spread should give multiple brackets significant probability."""
        highs = [8.0 + i * 0.35 for i in range(31)]  # 8-18.5 range
        forecast = self._make_forecast(highs)
        brackets = self._make_celsius_brackets()
        probs = ensemble_bracket_probabilities(forecast, brackets, sigma=1.0)
        significant = sum(1 for p in probs.values() if p > 0.05)
        assert significant >= 5

    def test_normalized_to_one(self):
        forecast = self._make_forecast([10.0 + i * 0.2 for i in range(31)])
        brackets = self._make_celsius_brackets()
        probs = ensemble_bracket_probabilities(forecast, brackets, sigma=1.0)
        assert abs(sum(probs.values()) - 1.0) < 0.001


# ---------------------------------------------------------------------------
# Weather strategy scoring
# ---------------------------------------------------------------------------

class TestWeatherStrategy:
    def _make_weather_event(self, bracket_prices: dict[str, float]) -> Event:
        event = Event(
            id="evt-w",
            slug="highest-temperature-in-london-on-march-18-2026",
            title="Highest temperature in London on March 18?",
            volume=500_000,
            volume_24hr=100_000,
            liquidity=200_000,
        )
        for i, (name, price) in enumerate(bracket_prices.items()):
            o = Outcome(name=name, price=price, price_delta=0.0, token_id=f"tok-{i}")
            m = Market(id=f"m-{i}", question=f"Will it be {name}?", outcomes=[o])
            event.markets.append(m)
        return event

    def test_underpriced_bracket_buy(self):
        """Forecast says 12°C is likely, market has it cheap => BUY."""
        strat = WeatherStrategy(min_edge=0.05)
        forecast = EnsembleForecast(
            city_name="London",
            target_date=date(2026, 3, 18),
            member_highs=[12.0] * 31,
            point_forecast=12.0,
            n_members=31,
            unit="celsius",
        )
        event = self._make_weather_event({
            "8°C or below": 0.02, "9°C": 0.03, "10°C": 0.05,
            "11°C": 0.08, "12°C": 0.10,  # market says 10%, forecast says ~35%+
            "13°C": 0.15, "14°C": 0.20,
            "15°C": 0.15, "16°C": 0.10,
            "17°C": 0.07, "18°C or higher": 0.05,
        })
        signals = strat.score_event(event, forecast)
        buy_12 = [s for s in signals if s.outcome.name == "12°C" and s.direction == "BUY"]
        assert len(buy_12) == 1
        assert buy_12[0].confidence > 0.3

    def test_overpriced_bracket_sell(self):
        """Forecast says 18°C+ is unlikely, market has it expensive => SELL."""
        strat = WeatherStrategy(min_edge=0.05)
        forecast = EnsembleForecast(
            city_name="London",
            target_date=date(2026, 3, 18),
            member_highs=[12.0] * 31,
            point_forecast=12.0,
            n_members=31,
            unit="celsius",
        )
        event = self._make_weather_event({
            "8°C or below": 0.02, "9°C": 0.03, "10°C": 0.05,
            "11°C": 0.08, "12°C": 0.30, "13°C": 0.15,
            "14°C": 0.10, "15°C": 0.07, "16°C": 0.05,
            "17°C": 0.05,
            "18°C or higher": 0.10,  # market 10%, forecast ~0%
        })
        signals = strat.score_event(event, forecast)
        sell_18 = [s for s in signals if "18" in s.outcome.name and s.direction == "SELL"]
        assert len(sell_18) == 1

    def test_no_signal_when_aligned(self):
        """Well-priced market should produce few/no signals."""
        strat = WeatherStrategy(min_edge=0.08)
        # Forecast centered at 12, market prices roughly match
        forecast = EnsembleForecast(
            city_name="London",
            target_date=date(2026, 3, 18),
            member_highs=[11.0 + i * 0.1 for i in range(31)],  # 11-14
            point_forecast=12.5,
            n_members=31,
            unit="celsius",
        )
        event = self._make_weather_event({
            "8°C or below": 0.01, "9°C": 0.02, "10°C": 0.04,
            "11°C": 0.12, "12°C": 0.25, "13°C": 0.25,
            "14°C": 0.15, "15°C": 0.08, "16°C": 0.04,
            "17°C": 0.02, "18°C or higher": 0.02,
        })
        signals = strat.score_event(event, forecast)
        high_conf = [s for s in signals if s.confidence > 0.5]
        assert len(high_conf) <= 2

    def test_max_entry_price_filters_buys(self):
        """Don't BUY brackets already priced above 70¢."""
        strat = WeatherStrategy(min_edge=0.05)
        forecast = EnsembleForecast(
            city_name="London",
            target_date=date(2026, 3, 18),
            member_highs=[12.0] * 31,
            point_forecast=12.0,
            n_members=31,
            unit="celsius",
        )
        event = self._make_weather_event({
            "8°C or below": 0.01, "9°C": 0.01, "10°C": 0.02,
            "11°C": 0.03, "12°C": 0.75,  # already expensive
            "13°C": 0.05, "14°C": 0.03,
            "15°C": 0.03, "16°C": 0.03,
            "17°C": 0.02, "18°C or higher": 0.02,
        })
        signals = strat.score_event(event, forecast)
        # Should NOT have a BUY on 12°C since price > 0.70
        buy_12 = [s for s in signals if s.outcome.name == "12°C" and s.direction == "BUY"]
        assert len(buy_12) == 0

    def test_fahrenheit_brackets(self):
        """Strategy works with 2-degree Fahrenheit brackets."""
        strat = WeatherStrategy(min_edge=0.05)
        forecast = EnsembleForecast(
            city_name="NYC",
            target_date=date(2026, 3, 18),
            member_highs=[36.0] * 31,
            point_forecast=36.0,
            n_members=31,
            unit="fahrenheit",
        )
        event = self._make_weather_event({
            "31°F or below": 0.02, "32-33°F": 0.05, "34-35°F": 0.10,
            "36-37°F": 0.15,  # underpriced vs forecast
            "38-39°F": 0.20, "40-41°F": 0.20,
            "42-43°F": 0.13, "44-45°F": 0.07,
            "46-47°F": 0.04, "48-49°F": 0.02,
            "50°F or higher": 0.02,
        })
        signals = strat.score_event(event, forecast)
        # Should find signals
        assert len(signals) > 0


# ---------------------------------------------------------------------------
# Title parsing
# ---------------------------------------------------------------------------

class TestTitleParsing:
    def test_nyc(self):
        result = parse_weather_event("Highest temperature in NYC on March 18?", 2026)
        assert result is not None
        city, target = result
        assert city.name == "NYC"
        assert city.unit == "fahrenheit"
        assert target == date(2026, 3, 18)

    def test_london(self):
        result = parse_weather_event("Highest temperature in London on March 17?", 2026)
        assert result is not None
        city, target = result
        assert city.name == "London"
        assert city.unit == "celsius"
        assert target == date(2026, 3, 17)

    def test_tel_aviv(self):
        result = parse_weather_event("Highest temperature in Tel Aviv on March 16?", 2026)
        assert result is not None
        city, target = result
        assert city.name == "Tel Aviv"

    def test_buenos_aires(self):
        result = parse_weather_event("Highest temperature in Buenos Aires on March 17?", 2026)
        assert result is not None
        assert result[0].name == "Buenos Aires"

    def test_toronto(self):
        result = parse_weather_event("Highest temperature in Toronto on March 18?", 2026)
        assert result is not None
        assert result[0].unit == "celsius"

    def test_unknown_city(self):
        assert parse_weather_event("Highest temperature in Timbuktu on March 18?", 2026) is None

    def test_non_weather(self):
        assert parse_weather_event("Who wins the 2028 election?", 2026) is None

    def test_is_weather_event(self):
        assert is_weather_event("Highest temperature in NYC on March 18?")
        assert is_weather_event("Highest temperature in Buenos Aires on March 17?")
        assert not is_weather_event("Bitcoin price prediction")
        assert not is_weather_event("Fed decision in March?")
