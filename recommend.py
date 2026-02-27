#!/usr/bin/env python3
"""
Polymarket trade recommender — fetches live market data and surfaces
the single most interesting trade using a simple momentum signal.

Signal: price_delta * log(volume_24hr + 1) / sqrt(price)

  - price_delta: outcome moved UP in the last 24hrs (crowd shifting)
  - volume_24hr: heavy recent trading = confident signal, not noise
  - price:       normalise for mid-range bets (10-70¢ sweet spot)

This is a heuristic tool, NOT financial advice.
"""

import asyncio
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

# Add src/ to path if running directly from repo root
sys.path.insert(0, "src")

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

from polymarket_cli.api.gamma import fetch_top_events
from polymarket_cli.api.clob import fill_price_deltas
from polymarket_cli.models import Event
from polymarket_cli.display.format import fmt_price, fmt_volume

console = Console()


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    event_title: str
    event_slug: str
    outcome_name: str
    price: float
    delta: float          # 24hr price change (price units)
    event_vol_24h: float
    score: float


def score_outcome(
    event: Event,
    outcome_name: str,
    price: float,
    delta: float,
) -> float:
    """
    Score = delta * log(vol_24h + 1) / sqrt(price_mid_distance)

    price_mid_distance amplifies mid-range outcomes (most actionable).
    Outcomes near 0¢ or 100¢ score lower.
    """
    if delta <= 0:
        return -999.0   # only recommend buys (positive momentum)
    if price < 0.02 or price > 0.90:
        return -999.0   # near-resolved or near-certain — skip
    if event.volume_24hr > event.volume * 0.6:
        return -999.0   # >60% of lifetime volume in 24h = live/expiring event (noisy)
    if event.end_date:
        try:
            closes = datetime.fromisoformat(event.end_date.replace("Z", "+00:00"))
            if closes - datetime.now(timezone.utc) < timedelta(days=3):
                return -999.0  # closes too soon — likely a live game or same-day event
        except ValueError:
            pass

    vol_weight = math.log1p(event.volume_24hr)
    # Reward mid-range prices; penalise extremes
    mid_distance = 1 - abs(price - 0.5) * 2   # 1.0 at 50¢, 0.0 at 0¢/100¢
    mid_distance = max(mid_distance, 0.01)

    return delta * vol_weight * mid_distance


def find_best_trade(events: list[Event]) -> Candidate | None:
    best: Candidate | None = None

    for event in events:
        all_outcomes = [o for m in event.markets for o in m.outcomes]
        # Filter active outcomes
        active = [o for o in all_outcomes if 0.02 < o.price < 0.90]

        for o in active:
            s = score_outcome(event, o.name, o.price, o.price_delta)
            if s <= 0:
                continue
            if best is None or s > best.score:
                best = Candidate(
                    event_title=event.title,
                    event_slug=event.slug,
                    outcome_name=o.name,
                    price=o.price,
                    delta=o.price_delta,
                    event_vol_24h=event.volume_24hr,
                    score=s,
                )

    return best


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def render_recommendation(pick: Candidate) -> None:
    delta_cents = pick.delta * 100
    price_cents = pick.price * 100
    implied_pct = pick.price * 100

    console.print()
    console.print(Rule("[bold cyan]POLYMARKET TRADE SIGNAL[/bold cyan]", style="cyan dim"))
    console.print()

    # Main recommendation panel
    body = Text()
    body.append(f"  Market:   ", style="dim")
    body.append(f"{pick.event_title}\n", style="bold white")
    body.append(f"  Outcome:  ", style="dim")
    body.append(f"{pick.outcome_name}\n", style="bold green")
    body.append(f"  Action:   ", style="dim")
    body.append(f"BUY", style="bold green")
    body.append(f"  {pick.outcome_name} @ {fmt_price(pick.price)}\n")
    body.append(f"\n")
    body.append(f"  Signal\n", style="bold dim")
    body.append(f"  ├─ Price:       ", style="dim")
    body.append(f"{price_cents:.1f}¢  ({implied_pct:.1f}% implied probability)\n")
    body.append(f"  ├─ 24h move:    ", style="dim")
    body.append(f"▲{delta_cents:.1f}¢", style="green")
    body.append(f"  (upward momentum)\n")
    body.append(f"  ├─ Market vol:  ", style="dim")
    body.append(f"{fmt_volume(pick.event_vol_24h)} in last 24h\n")
    body.append(f"  └─ Score:       ", style="dim")
    body.append(f"{pick.score:.2f}  (momentum × volume × mid-range weight)\n")
    body.append(f"\n")
    body.append(f"  Reasoning\n", style="bold dim")
    body.append(f"  This outcome rose {delta_cents:.1f}¢ in 24h on {fmt_volume(pick.event_vol_24h)}\n")
    body.append(f"  of market volume — suggesting new information or shifting\n")
    body.append(f"  consensus. Mid-range price ({price_cents:.0f}¢) means the bet is\n")
    body.append(f"  still open and not near resolution.\n")
    body.append(f"\n")
    body.append(f"  Polymarket URL\n", style="bold dim")
    body.append(f"  https://polymarket.com/event/{pick.event_slug}\n", style="dim")

    console.print(Panel(body, border_style="cyan", expand=False))

    console.print(
        "  [dim bold yellow]⚠  Heuristic signal only. Not financial advice.[/dim bold yellow]\n"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    with console.status("[dim]Fetching top markets…[/dim]", spinner="dots"):
        events = await fetch_top_events(limit=30, sort="volume_24hr")

    with console.status("[dim]Fetching 24hr price history…[/dim]", spinner="dots"):
        await fill_price_deltas(events)

    pick = find_best_trade(events)

    if pick is None:
        console.print("[yellow]No clear momentum signal found right now.[/yellow]")
        return

    render_recommendation(pick)


if __name__ == "__main__":
    asyncio.run(main())
