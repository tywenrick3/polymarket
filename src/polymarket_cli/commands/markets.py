import asyncio
import json
import sys
from typing import Annotated

import typer

from polymarket_cli.api.gamma import fetch_top_events
from polymarket_cli.display.tables import render_markets, console

app = typer.Typer()


@app.callback(invoke_without_command=True)
def markets(
    limit: Annotated[int, typer.Option("--limit", "-n", help="Number of markets")] = 20,
    sort: Annotated[str, typer.Option("--sort", help="Sort by: volume_24hr, volume, liquidity, end_date")] = "volume",
    fmt: Annotated[str, typer.Option("--format", help="Output format: table or json")] = "table",
) -> None:
    """List top Polymarket events sorted by volume."""

    async def run() -> None:
        with console.status("[dim]Fetching markets…[/dim]", spinner="dots"):
            events = await fetch_top_events(limit=limit, sort=sort)

        if fmt == "json" or not sys.stdout.isatty():
            out = [
                {
                    "id": e.id,
                    "slug": e.slug,
                    "title": e.title,
                    "volume": e.volume,
                    "volume_24hr": e.volume_24hr,
                    "liquidity": e.liquidity,
                    "end_date": e.end_date,
                }
                for e in events
            ]
            print(json.dumps(out, indent=2))
        else:
            render_markets(events)

    asyncio.run(run())
