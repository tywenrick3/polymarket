import typer

from polymarket_cli.commands.dashboard import dashboard
from polymarket_cli.commands.markets import markets
from polymarket_cli.commands.market import market
from polymarket_cli.commands.search import search
from polymarket_cli.commands.recommend import recommend
from polymarket_cli.commands.backtest import backtest
from polymarket_cli.commands.whales import whales
from polymarket_cli.commands.paper import app as paper_app
from polymarket_cli.commands.cache_cmd import app as cache_app
from polymarket_cli.commands.weather import app as weather_app

app = typer.Typer(
    name="polymarket",
    help="Terminal CLI for Polymarket prediction markets.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

app.command("dashboard", help="Top markets dashboard with 24hr changes")(dashboard)
app.command("markets", help="List markets sorted by volume")(markets)
app.command("market", help="Detail view for a single event")(market)
app.command("search", help="Search active markets by title")(search)
app.command("recommend", help="Trade signals: momentum, SMA, mean-reversion, or composite")(recommend)
app.command("backtest", help="Backtest strategies against historical price data")(backtest)
app.command("whales", help="Top whale positions for an event")(whales)
app.add_typer(paper_app, name="paper", help="Paper trading: snapshot positions & check P&L")
app.add_typer(cache_app, name="cache", help="Manage the local data cache")
app.add_typer(weather_app, name="weather", help="Forecast-based signals for temperature bracket markets")


if __name__ == "__main__":
    app()
