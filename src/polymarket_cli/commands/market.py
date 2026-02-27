import asyncio
import json
import sys
from typing import Annotated

import typer

from polymarket_cli.api.gamma import fetch_event_by_slug
from polymarket_cli.api.clob import fill_price_deltas
from polymarket_cli.display.tables import render_event, console

app = typer.Typer()


@app.callback(invoke_without_command=True)
def market(
    slug: Annotated[str, typer.Argument(help="Event slug (from polymarket.com URL)")],
    no_deltas: Annotated[bool, typer.Option("--no-deltas", help="Skip 24hr price delta fetch")] = False,
    fmt: Annotated[str, typer.Option("--format", help="Output format: table or json")] = "table",
) -> None:
    """Show detail for a single Polymarket event by slug."""

    async def run() -> None:
        with console.status(f"[dim]Fetching {slug}…[/dim]", spinner="dots"):
            event = await fetch_event_by_slug(slug)

        if event is None:
            console.print(f"[red]No event found for slug:[/red] {slug}")
            raise typer.Exit(1)

        if not no_deltas:
            with console.status("[dim]Fetching price history…[/dim]", spinner="dots"):
                await fill_price_deltas([event])

        if fmt == "json" or not sys.stdout.isatty():
            out = {
                "id": event.id,
                "slug": event.slug,
                "title": event.title,
                "volume": event.volume,
                "volume_24hr": event.volume_24hr,
                "end_date": event.end_date,
                "markets": [
                    {
                        "question": m.question,
                        "outcomes": [
                            {"name": o.name, "price": o.price, "price_delta": o.price_delta}
                            for o in m.outcomes
                        ],
                    }
                    for m in event.markets
                ],
            }
            print(json.dumps(out, indent=2))
        else:
            render_event(event)

    asyncio.run(run())
