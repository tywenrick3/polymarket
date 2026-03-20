from polymarket_cli.strategies.base import TradeSignal, Strategy
from polymarket_cli.strategies.momentum import MomentumStrategy
from polymarket_cli.strategies.sma import SMAStrategy
from polymarket_cli.strategies.mean_reversion import MeanReversionStrategy
from polymarket_cli.strategies.cross_market import CrossMarketStrategy
from polymarket_cli.strategies.composite import CompositeStrategy
from polymarket_cli.strategies.regime import Regime, detect_regime
from polymarket_cli.strategies.weather import WeatherStrategy

__all__ = [
    "TradeSignal",
    "Strategy",
    "MomentumStrategy",
    "SMAStrategy",
    "MeanReversionStrategy",
    "CrossMarketStrategy",
    "CompositeStrategy",
    "Regime",
    "detect_regime",
    "WeatherStrategy",
]
