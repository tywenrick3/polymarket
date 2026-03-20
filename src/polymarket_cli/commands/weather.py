"""Weather forecast signals for temperature bracket markets."""

import asyncio
import json
import sys
from datetime import datetime
from typing import Annotated

import typer

from polymarket_cli.api.gamma import fetch_top_events, search_events
from polymarket_cli.api.weather import fetch_ensemble_forecast
from polymarket_cli.display.format import fmt_price
from polymarket_cli.display.tables import console
from polymarket_cli.strategies.weather import WeatherStrategy, parse_bracket
from polymarket_cli.strategies.base import TradeSignal
from polymarket_cli.weather_cities import parse_weather_event, is_weather_event

from rich.rule import Rule
from rich.table import Table
from rich.text import Text

app = typer.Typer()


def _signal_to_dict(sig: TradeSignal) -> dict:
    return {
        "event_title": sig.event.title,
        "event_slug": sig.event.slug,
        "outcome": sig.outcome.name,
        "market_price": sig.outcome.price,
        "direction": sig.direction,
        "confidence": sig.confidence,
        "score": sig.score,
        "strategy": sig.strategy,
        "rationale": sig.rationale,
    }


def _render_signals(signals: list[TradeSignal], forecasts: dict[str, dict]) -> None:
    """Rich terminal rendering for weather signals."""
    console.print()
    console.print(Rule("[bold cyan]WEATHER FORECAST SIGNALS[/bold cyan]", style="cyan dim"))
    console.print()

    # Show forecast summaries
    for key, fi in forecasts.items():
        unit = fi["unit_sym"]
        console.print(
            f"  [bold]{fi['city']}[/bold] {fi['date']}  "
            f"[dim]point=[/dim][bold]{fi['point']:.0f}{unit}[/bold]  "
            f"[dim]range=[/dim]{fi['low']:.0f}-{fi['high']:.0f}{unit}  "
            f"[dim]({fi['members']} members)[/dim]"
        )
    console.print()

    # Signal table
    table = Table(show_header=True, header_style="bold dim", box=None, padding=(0, 1))
    table.add_column("#", style="dim", width=3)
    table.add_column("Market", min_width=18)
    table.add_column("Bracket", width=12)
    table.add_column("Action", width=6)
    table.add_column("Market", justify="right", width=7, style="dim")
    table.add_column("Fcast", justify="right", width=7)
    table.add_column("Edge", justify="right", width=7)
    table.add_column("Conf", width=9)

    for i, sig in enumerate(signals, 1):
        dir_style = "bold green" if sig.direction == "BUY" else "bold red"

        # Parse forecast% and edge from rationale
        parts = sig.rationale.split("|")[0].strip().split()
        fcast_pct = ""
        mkt_pct = ""
        edge_pct = ""
        for p in parts:
            if p.startswith("forecast="):
                fcast_pct = p.split("=")[1]
            elif p.startswith("market="):
                mkt_pct = p.split("=")[1]
            elif p.startswith("edge="):
                edge_pct = p.split("=")[1]

        filled = int(sig.confidence * 5)
        conf_bar = "■" * filled + "·" * (5 - filled)

        # Shorten event title for table
        city_label = sig.event.title.replace("Highest temperature in ", "").split(" on ")[0]

        table.add_row(
            str(i),
            city_label,
            sig.outcome.name,
            Text(sig.direction, style=dir_style),
            mkt_pct,
            fcast_pct,
            Text(edge_pct, style=dir_style),
            Text(f"[{conf_bar}]"),
        )

    console.print(table)
    console.print()
    console.print(
        "  [dim bold yellow]Forecast-based signals. Models update every 6 hours. "
        "Not financial advice.[/dim bold yellow]"
    )
    console.print()


@app.callback(invoke_without_command=True)
def weather(
    city: Annotated[str, typer.Argument(help="City filter (e.g. 'NYC', 'London'). Omit for all.")] = "",
    top: Annotated[int, typer.Option("--top", "-n", help="Number of signals")] = 10,
    min_edge: Annotated[float, typer.Option("--min-edge", help="Minimum edge (0-1)")] = 0.08,
    fmt: Annotated[str, typer.Option("--format", help="Output: table or json")] = "table",
) -> None:
    """Forecast-based signals for temperature bracket markets."""
    current_year = datetime.now().year
    is_json = fmt == "json" or not sys.stdout.isatty()

    async def run() -> tuple[list[TradeSignal], dict[str, dict]]:
        # Find weather markets
        if not is_json:
            status = console.status("[dim]Searching for weather markets...[/dim]", spinner="dots")
            status.start()

        # Search both by query and by fetching top events
        query = f"Highest temperature in {city}" if city else "Highest temperature"
        events = await search_events(query, limit=100)

        if not is_json:
            status.stop()

        weather_events = [e for e in events if is_weather_event(e.title)]

        if not weather_events:
            return [], {}

        strat = WeatherStrategy(min_edge=min_edge)
        all_signals: list[TradeSignal] = []
        forecasts: dict[str, dict] = {}

        for event in weather_events:
            parsed = parse_weather_event(event.title, current_year)
            if parsed is None:
                continue

            city_coord, target_date = parsed

            if not is_json:
                status = console.status(
                    f"[dim]Fetching forecast: {city_coord.name} {target_date}...[/dim]",
                    spinner="dots",
                )
                status.start()

            forecast = await fetch_ensemble_forecast(city_coord, target_date)

            if not is_json:
                status.stop()

            if forecast is None:
                continue

            signals = strat.score_event(event, forecast)
            all_signals.extend(signals)

            unit_sym = "°F" if forecast.unit == "fahrenheit" else "°C"
            key = f"{forecast.city_name}:{target_date}"
            forecasts[key] = {
                "city": forecast.city_name,
                "date": target_date.isoformat(),
                "point": forecast.point_forecast,
                "low": min(forecast.member_highs),
                "high": max(forecast.member_highs),
                "members": forecast.n_members,
                "unit_sym": unit_sym,
            }

        all_signals.sort(key=lambda s: s.score, reverse=True)
        return all_signals[:top], forecasts

    signals, forecasts = asyncio.run(run())

    if not signals:
        if is_json:
            print(json.dumps({"signals": [], "reason": "No weather markets found or no edge detected."}))
        else:
            console.print("[yellow]No weather signals found.[/yellow]")
        return

    if is_json:
        output = {
            "forecasts": forecasts,
            "signals": [_signal_to_dict(s) for s in signals],
        }
        print(json.dumps(output, indent=2))
    else:
        _render_signals(signals, forecasts)
