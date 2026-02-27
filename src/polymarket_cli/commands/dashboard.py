import asyncio
import json
import sys
from typing import Annotated

import typer

from polymarket_cli.api.gamma import fetch_top_events
from polymarket_cli.api.clob import fill_price_deltas
from polymarket_cli.display.tables import render_dashboard, console

app = typer.Typer()


@app.callback(invoke_without_command=True)
def dashboard(
    limit: Annotated[int, typer.Option("--limit", "-n", help="Number of markets to show")] = 10,
    sort: Annotated[str, typer.Option("--sort", help="Sort by: volume_24hr, volume, liquidity")] = "volume_24hr",
    fmt: Annotated[str, typer.Option("--format", help="Output format: table or json")] = "table",
    no_deltas: Annotated[bool, typer.Option("--no-deltas", help="Skip 24hr price delta fetch (faster)")] = False,
) -> None:
    """Show a dashboard of top Polymarket events with 24hr changes."""

    async def run() -> None:
        with console.status("[dim]Fetching markets…[/dim]", spinner="dots"):
            events = await fetch_top_events(limit=limit, sort=sort)

        if not no_deltas and events:
            with console.status("[dim]Fetching price history…[/dim]", spinner="dots"):
                await fill_price_deltas(events)

        if fmt == "json" or not sys.stdout.isatty():
            out = []
            for e in events:
                out.append({
                    "id": e.id,
                    "slug": e.slug,
                    "title": e.title,
                    "volume": e.volume,
                    "volume_24hr": e.volume_24hr,
                    "markets": [
                        {
                            "question": m.question,
                            "outcomes": [
                                {
                                    "name": o.name,
                                    "price": o.price,
                                    "price_delta": o.price_delta,
                                }
                                for o in m.outcomes[:5]
                            ],
                        }
                        for m in e.markets
                    ],
                })
            print(json.dumps(out, indent=2))
        else:
            render_dashboard(events)

    asyncio.run(run())
