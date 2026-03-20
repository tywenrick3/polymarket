import asyncio
import json
import sys
from typing import Annotated

import typer

from polymarket_cli.api.gamma import fetch_event_by_slug
from polymarket_cli.api.subgraph import fetch_whales_for_event
from polymarket_cli.display.tables import render_whales, console

app = typer.Typer()


def _build_token_map(event) -> dict[str, str]:
    """Build {token_id: outcome_name} from an event's markets."""
    token_map = {}
    for market in event.markets:
        for outcome in market.outcomes:
            if outcome.token_id:
                token_map[outcome.token_id] = outcome.name
    return token_map


@app.callback(invoke_without_command=True)
def whales(
    slug: Annotated[str, typer.Argument(help="Event slug (from polymarket.com URL)")],
    limit: Annotated[int, typer.Option("--limit", "-n", help="Max whale entries to show")] = 20,
    fmt: Annotated[str, typer.Option("--format", help="Output format: table or json")] = "table",
) -> None:
    """Show top whale traders for a Polymarket event."""

    async def run() -> None:
        with console.status(f"[dim]Fetching {slug}…[/dim]", spinner="dots"):
            event = await fetch_event_by_slug(slug)

        if event is None:
            console.print(f"[red]No event found for slug:[/red] {slug}")
            raise typer.Exit(1)

        token_map = _build_token_map(event)
        if not token_map:
            console.print("[red]No outcome tokens found for this event.[/red]")
            raise typer.Exit(1)

        with console.status("[dim]Querying on-chain trades…[/dim]", spinner="dots"):
            trades = await fetch_whales_for_event(token_map, limit=limit)

        if not trades:
            console.print("[yellow]No whale trades found.[/yellow]")
            raise typer.Exit(0)

        if fmt == "json" or not sys.stdout.isatty():
            out = {
                "event": event.title,
                "slug": event.slug,
                "whales": [
                    {
                        "address": t.address,
                        "outcome": t.outcome_name,
                        "side": t.side,
                        "usd_volume": round(t.usd_amount, 2),
                        "num_trades": t.num_trades,
                    }
                    for t in trades
                ],
            }
            print(json.dumps(out, indent=2))
        else:
            render_whales(event.title, trades)

    asyncio.run(run())
