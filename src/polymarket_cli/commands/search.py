import asyncio
import json
import sys
from typing import Annotated

import typer

from polymarket_cli.api.gamma import search_events
from polymarket_cli.display.tables import render_markets, console

app = typer.Typer()


@app.callback(invoke_without_command=True)
def search(
    query: Annotated[str, typer.Argument(help="Search query")],
    limit: Annotated[int, typer.Option("--limit", "-n", help="Max results")] = 10,
    fmt: Annotated[str, typer.Option("--format", help="Output format: table or json")] = "table",
) -> None:
    """Search active Polymarket events by title."""

    async def run() -> None:
        with console.status(f"[dim]Searching \"{query}\"…[/dim]", spinner="dots"):
            events = await search_events(query=query, limit=limit)

        if not events:
            console.print(f"[yellow]No results for:[/yellow] {query}")
            return

        if fmt == "json" or not sys.stdout.isatty():
            out = [
                {
                    "id": e.id,
                    "slug": e.slug,
                    "title": e.title,
                    "volume": e.volume,
                    "volume_24hr": e.volume_24hr,
                }
                for e in events
            ]
            print(json.dumps(out, indent=2))
        else:
            console.print(f"\n[dim]Results for:[/dim] [bold]{query}[/bold]\n")
            render_markets(events)

    asyncio.run(run())
