import asyncio
import json
import sys
from typing import Annotated

import typer

from polymarket_cli.api.gamma import fetch_top_events
from polymarket_cli.api.clob import fetch_batch_series
from polymarket_cli.display.format import fmt_price, fmt_volume
from polymarket_cli.display.tables import console
from polymarket_cli.strategies import (
    CompositeStrategy,
    MomentumStrategy,
    SMAStrategy,
    MeanReversionStrategy,
    CrossMarketStrategy,
    TradeSignal,
)

from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

app = typer.Typer()

STRATEGY_MAP = {
    "composite": CompositeStrategy,
    "momentum": MomentumStrategy,
    "sma": SMAStrategy,
    "mean-reversion": MeanReversionStrategy,
    "cross-market": CrossMarketStrategy,
}

STRATEGY_NAMES = ", ".join(STRATEGY_MAP.keys())


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def _confidence_bar(conf: float) -> Text:
    """Render a 5-block confidence bar: [***..]"""
    filled = int(conf * 5)
    bar = Text("[")
    bar.append("■" * filled, style="bold green" if filled >= 3 else "bold yellow" if filled >= 2 else "bold red")
    bar.append("·" * (5 - filled), style="dim")
    bar.append("]")
    return bar


def _render_single(signal: TradeSignal) -> None:
    """Render a single trade signal as a rich panel."""
    price_cents = signal.outcome.price * 100
    dir_style = "bold green" if signal.direction == "BUY" else "bold red"

    console.print()
    console.print(Rule("[bold cyan]POLYMARKET TRADE SIGNAL[/bold cyan]", style="cyan dim"))
    console.print()

    body = Text()
    body.append("  Market:     ", style="dim")
    body.append(f"{signal.event.title}\n", style="bold white")
    body.append("  Outcome:    ", style="dim")
    body.append(f"{signal.outcome.name}\n", style=dir_style)
    body.append("  Action:     ", style="dim")
    body.append(signal.direction, style=dir_style)
    body.append(f"  {signal.outcome.name} @ {fmt_price(signal.outcome.price)}\n")
    body.append("\n")

    body.append("  Signal\n", style="bold dim")
    body.append("  ├─ Price:       ", style="dim")
    body.append(f"{price_cents:.1f}¢  ({price_cents:.1f}% implied probability)\n")
    body.append("  ├─ Market vol:  ", style="dim")
    body.append(f"{fmt_volume(signal.event.volume_24hr)} in last 24h\n")
    body.append("  ├─ Confidence:  ", style="dim")

    filled = int(signal.confidence * 5)
    conf_str = "■" * filled + "·" * (5 - filled)
    body.append(f"[{conf_str}] {signal.confidence:.0%}\n")

    body.append("  └─ Strategy:    ", style="dim")
    body.append(f"{signal.strategy}\n")

    # Parse and display sub-strategy rationales
    if signal.strategy == "composite":
        body.append("\n")
        body.append("  Breakdown\n", style="bold dim")
        parts = signal.rationale.split(" | ")
        # First part has the agreement label
        if parts:
            agreement = parts[0].split("] ")[0] + "]" if "]" in parts[0] else ""
            if agreement:
                body.append(f"  ├─ Agreement:   ", style="dim")
                body.append(f"{agreement}\n")
            # Extract strategy lines
            strat_lines = []
            for part in parts:
                # Strip leading [x/y agree] from first part
                clean = part.split("] ")[-1] if "]" in part else part
                strat_lines.append(clean)
            for i, line in enumerate(strat_lines):
                connector = "└─" if i == len(strat_lines) - 1 else "├─"
                is_disagree = "DISAGREES" in line
                style = "red" if is_disagree else "green" if signal.direction == "BUY" else "red"
                body.append(f"  {connector} ", style="dim")
                body.append(f"{line}\n", style=style if not is_disagree else "yellow")
    else:
        body.append("\n")
        body.append("  Rationale\n", style="bold dim")
        body.append(f"  └─ {signal.rationale}\n", style="dim")

    body.append("\n")
    body.append("  Polymarket URL\n", style="bold dim")
    body.append(f"  https://polymarket.com/event/{signal.event.slug}\n", style="dim")

    console.print(Panel(body, border_style="cyan", expand=False))


def _render_multi(signals: list[TradeSignal]) -> None:
    """Render multiple signals as a ranked table."""
    console.print()
    console.print(Rule("[bold cyan]POLYMARKET TRADE SIGNALS[/bold cyan]", style="cyan dim"))
    console.print()

    table = Table(show_header=True, header_style="bold dim", box=None, padding=(0, 1))
    table.add_column("#", style="dim", width=3)
    table.add_column("Action", width=6)
    table.add_column("Outcome", min_width=20)
    table.add_column("Price", justify="right", width=6)
    table.add_column("Conf", width=9)
    table.add_column("Strategy", width=12)
    table.add_column("Rationale", min_width=30)

    for i, sig in enumerate(signals, 1):
        dir_style = "bold green" if sig.direction == "BUY" else "bold red"
        conf_bar = _confidence_bar(sig.confidence)

        # Shorten rationale for table — just the key part
        rationale = sig.rationale
        if sig.strategy == "composite":
            # Show agreement label and first strategy
            rationale = rationale[:80] + "…" if len(rationale) > 80 else rationale

        table.add_row(
            str(i),
            Text(sig.direction, style=dir_style),
            Text(sig.outcome.name, style="bold white"),
            fmt_price(sig.outcome.price),
            conf_bar,
            sig.strategy,
            Text(rationale, style="dim"),
        )

    console.print(table)
    console.print()

    # Detail panels for top 3
    for sig in signals[:3]:
        _render_single(sig)

    console.print(
        "  [dim bold yellow]⚠  Heuristic signals only. Not financial advice.[/dim bold yellow]\n"
    )


def _signal_to_dict(sig: TradeSignal) -> dict:
    """Serialise a TradeSignal to a JSON-friendly dict."""
    return {
        "event_title": sig.event.title,
        "event_slug": sig.event.slug,
        "outcome": sig.outcome.name,
        "price": sig.outcome.price,
        "direction": sig.direction,
        "confidence": sig.confidence,
        "score": sig.score,
        "strategy": sig.strategy,
        "rationale": sig.rationale,
        "volume_24hr": sig.event.volume_24hr,
    }


# ---------------------------------------------------------------------------
# Scanning logic
# ---------------------------------------------------------------------------

def _scan_individual(
    strategy,
    events: list,
    series_map: dict[str, list[dict]],
    top_n: int,
) -> list[TradeSignal]:
    """Run a single (non-composite) strategy across all outcomes."""
    signals: list[TradeSignal] = []

    # Cross-market strategy scores entire group events, not individual outcomes
    if hasattr(strategy, "score_group"):
        for event in events:
            sigs = strategy.score_group(event, series_map)
            signals.extend(sigs)
    else:
        for event in events:
            for market in event.markets:
                for outcome in market.outcomes:
                    if not outcome.token_id:
                        continue
                    series = series_map.get(outcome.token_id, [])
                    if len(series) < 10:
                        continue
                    sig = strategy.score(event, outcome, series)
                    if sig is not None:
                        signals.append(sig)

    signals.sort(key=lambda s: s.confidence, reverse=True)
    return signals[:top_n]


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------

@app.callback(invoke_without_command=True)
def recommend(
    strategy: Annotated[
        str,
        typer.Option("--strategy", "-s", help=f"Strategy: {STRATEGY_NAMES}"),
    ] = "composite",
    top: Annotated[int, typer.Option("--top", "-n", help="Number of signals to surface")] = 3,
    limit: Annotated[int, typer.Option("--limit", help="Markets to scan")] = 30,
    fmt: Annotated[str, typer.Option("--format", help="Output format: table or json")] = "table",
) -> None:
    """Surface the best trade signals across top markets."""

    if strategy not in STRATEGY_MAP:
        console.print(f"[red]Unknown strategy '{strategy}'. Choose from: {STRATEGY_NAMES}[/red]")
        raise typer.Exit(1)

    async def run() -> list[TradeSignal]:
        with console.status("[dim]Fetching top markets…[/dim]", spinner="dots"):
            events = await fetch_top_events(limit=limit, sort="volume_24hr")

        # Collect token IDs for price series fetch
        token_ids = []
        for e in events:
            for m in e.markets:
                for o in m.outcomes[:5]:
                    if o.token_id:
                        token_ids.append(o.token_id)

        interval = "1w"  # 7 days of hourly data
        with console.status(
            f"[dim]Fetching price history ({len(token_ids)} tokens, {interval})…[/dim]",
            spinner="dots",
        ):
            series_map = await fetch_batch_series(token_ids, interval=interval, fidelity=60)

        strat_cls = STRATEGY_MAP[strategy]
        strat = strat_cls()

        if strategy == "composite":
            return strat.score_all(events, series_map, top_n=top)
        else:
            return _scan_individual(strat, events, series_map, top)

    signals = asyncio.run(run())

    is_json = fmt == "json" or not sys.stdout.isatty()

    if not signals:
        if is_json:
            print(json.dumps({"signals": [], "reason": "No clear signal found."}))
        else:
            console.print(f"[yellow]No clear {strategy} signal found right now.[/yellow]")
        return

    if is_json:
        print(json.dumps([_signal_to_dict(s) for s in signals], indent=2))
    elif len(signals) == 1:
        _render_single(signals[0])
        console.print(
            "  [dim bold yellow]⚠  Heuristic signal only. Not financial advice.[/dim bold yellow]\n"
        )
    else:
        _render_multi(signals)
