import typer

from polymarket_cli.commands.dashboard import dashboard
from polymarket_cli.commands.markets import markets
from polymarket_cli.commands.market import market
from polymarket_cli.commands.search import search

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


if __name__ == "__main__":
    app()
