import asyncio
import json
import math
import sys
from datetime import datetime, timezone, timedelta
from typing import Annotated

import typer

from polymarket_cli.api.gamma import fetch_top_events
from polymarket_cli.api.clob import fill_price_deltas
from polymarket_cli.display.format import fmt_price, fmt_volume
from polymarket_cli.display.tables import console
from polymarket_cli.models import Event

from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

app = typer.Typer()


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _score_outcome(event: Event, price: float, delta: float) -> float:
    """
    Score = delta * log(vol_24h + 1) * mid_distance

    Positive-momentum outcomes in the mid-range price band score highest.
    """
    if delta <= 0:
        return -999.0
    if price < 0.02 or price > 0.90:
        return -999.0
    if event.volume_24hr > event.volume * 0.6:
        return -999.0  # >60% of lifetime vol in 24h = live/expiring event
    if event.end_date:
        try:
            closes = datetime.fromisoformat(event.end_date.replace("Z", "+00:00"))
            if closes - datetime.now(timezone.utc) < timedelta(days=3):
                return -999.0
        except ValueError:
            pass

    vol_weight = math.log1p(event.volume_24hr)
    mid_distance = 1 - abs(price - 0.5) * 2  # 1.0 at 50c, 0.0 at 0c/100c
    mid_distance = max(mid_distance, 0.01)
    return delta * vol_weight * mid_distance


def _find_best_trade(events: list[Event]) -> dict | None:
    best: dict | None = None
    best_score = 0.0

    for event in events:
        for market in event.markets:
            for o in market.outcomes:
                if not (0.02 < o.price < 0.90):
                    continue
                s = _score_outcome(event, o.price, o.price_delta)
                if s <= 0:
                    continue
                if s > best_score:
                    best_score = s
                    best = {
                        "event_title": event.title,
                        "event_slug": event.slug,
                        "outcome": o.name,
                        "price": o.price,
                        "price_delta": o.price_delta,
                        "volume_24hr": event.volume_24hr,
                        "score": round(s, 4),
                    }
    return best


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def _render_recommendation(pick: dict) -> None:
    delta_cents = pick["price_delta"] * 100
    price_cents = pick["price"] * 100

    console.print()
    console.print(Rule("[bold cyan]POLYMARKET TRADE SIGNAL[/bold cyan]", style="cyan dim"))
    console.print()

    body = Text()
    body.append("  Market:   ", style="dim")
    body.append(f"{pick['event_title']}\n", style="bold white")
    body.append("  Outcome:  ", style="dim")
    body.append(f"{pick['outcome']}\n", style="bold green")
    body.append("  Action:   ", style="dim")
    body.append("BUY", style="bold green")
    body.append(f"  {pick['outcome']} @ {fmt_price(pick['price'])}\n")
    body.append("\n")
    body.append("  Signal\n", style="bold dim")
    body.append("  \u251c\u2500 Price:       ", style="dim")
    body.append(f"{price_cents:.1f}\u00a2  ({price_cents:.1f}% implied probability)\n")
    body.append("  \u251c\u2500 24h move:    ", style="dim")
    body.append(f"\u25b2{delta_cents:.1f}\u00a2", style="green")
    body.append("  (upward momentum)\n")
    body.append("  \u251c\u2500 Market vol:  ", style="dim")
    body.append(f"{fmt_volume(pick['volume_24hr'])} in last 24h\n")
    body.append("  \u2514\u2500 Score:       ", style="dim")
    body.append(f"{pick['score']:.2f}  (momentum \u00d7 volume \u00d7 mid-range weight)\n")
    body.append("\n")
    body.append("  Polymarket URL\n", style="bold dim")
    body.append(f"  https://polymarket.com/event/{pick['event_slug']}\n", style="dim")

    console.print(Panel(body, border_style="cyan", expand=False))
    console.print(
        "  [dim bold yellow]\u26a0  Heuristic signal only. Not financial advice.[/dim bold yellow]\n"
    )


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------

@app.callback(invoke_without_command=True)
def recommend(
    fmt: Annotated[str, typer.Option("--format", help="Output format: table or json")] = "table",
) -> None:
    """Surface the single best momentum trade across top markets."""

    async def run() -> None:
        with console.status("[dim]Fetching top markets\u2026[/dim]", spinner="dots"):
            events = await fetch_top_events(limit=30, sort="volume_24hr")

        with console.status("[dim]Fetching 24hr price history\u2026[/dim]", spinner="dots"):
            await fill_price_deltas(events)

        pick = _find_best_trade(events)

        if pick is None:
            if fmt == "json" or not sys.stdout.isatty():
                print(json.dumps({"recommendation": None, "reason": "No clear momentum signal found."}))
            else:
                console.print("[yellow]No clear momentum signal found right now.[/yellow]")
            return

        if fmt == "json" or not sys.stdout.isatty():
            print(json.dumps({"recommendation": pick}, indent=2))
        else:
            _render_recommendation(pick)

    asyncio.run(run())
