"""Rich table builders for all commands."""

from datetime import datetime

from rich.console import Console
from rich.table import Table
from rich import box
from rich.text import Text
from rich.rule import Rule
from rich.panel import Panel
from rich.columns import Columns

from polymarket_cli.models import Event
from polymarket_cli.display.format import (
    fmt_volume,
    fmt_price,
    fmt_delta,
    fmt_volume_delta,
    truncate,
)

console = Console()

# Empirically measured column widths for the dashboard table (SIMPLE_HEAD + pad_edge=True).
# Measured via rendered separator line lengths:
#   0 outcomes = 59 chars,  each additional outcome = +32 chars
_W_RANK    = 2   # "#"
_W_EVENT   = 30  # event title (truncated)
_W_TOTAL   = 8   # "$413.0M"
_W_24H     = 8   # "+$23.0M"
_W_NAME    = 14  # outcome name (truncated)
_W_PRICE   = 4   # "74¢"
_W_DELTA   = 5   # "▲1.0"

_FIXED = 59   # measured: fixed columns take 59 chars
_PER   = 32   # measured: each outcome group takes 32 chars


def _max_outcomes(cap: int = 5) -> int:
    """How many outcome groups fit without overflowing the terminal."""
    available = (console.width or 120) - _FIXED
    return max(1, min(cap, available // _PER))


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

def render_dashboard(events: list[Event]) -> None:
    now = datetime.now().strftime("%b %d %H:%M")
    max_out = _max_outcomes()

    console.print()
    console.print(
        Rule(
            f"[bold cyan]POLYMARKET[/bold cyan]  [dim]{now}[/dim]",
            style="cyan dim",
        )
    )
    console.print()

    table = Table(
        box=box.SIMPLE_HEAD,
        show_header=True,
        header_style="bold dim",
        pad_edge=True,
        expand=False,
        show_edge=False,
    )

    table.add_column("#",     style="dim",    min_width=_W_RANK,  max_width=_W_RANK,  justify="right", no_wrap=True)
    table.add_column("Event",               min_width=_W_EVENT, max_width=_W_EVENT,                  no_wrap=True)
    table.add_column("Total", justify="right", min_width=_W_TOTAL, max_width=_W_TOTAL,               no_wrap=True)
    table.add_column("24h",   justify="right", min_width=_W_24H,   max_width=_W_24H,                 no_wrap=True)

    for i in range(1, max_out + 1):
        table.add_column(f"#{i}", min_width=_W_NAME,  max_width=_W_NAME,               no_wrap=True)
        table.add_column("¢",     min_width=_W_PRICE, max_width=_W_PRICE, justify="right", no_wrap=True)
        table.add_column("Δ",     min_width=_W_DELTA, max_width=_W_DELTA, justify="right", no_wrap=True)

    for rank, event in enumerate(events, 1):
        v24h_text, v24h_style = fmt_volume_delta(event.volume_24hr)

        row: list = [
            str(rank),
            truncate(event.title, 30),
            fmt_volume(event.volume),
            Text(v24h_text, style=v24h_style),
        ]

        # Gather & sort outcomes; filter resolved (0¢/100¢) unless nothing else
        all_out = [o for m in event.markets for o in m.outcomes]
        active  = [o for o in all_out if 0.005 < o.price < 0.995]
        pool    = active if active else all_out
        pool.sort(key=lambda o: o.price, reverse=True)

        # Deduplicate by name
        seen: set[str] = set()
        deduped = []
        for o in pool:
            if o.name not in seen:
                seen.add(o.name)
                deduped.append(o)

        for i in range(max_out):
            if i < len(deduped):
                o = deduped[i]
                delta_text, delta_style = fmt_delta(o.price_delta)
                row.append(truncate(o.name, _W_NAME))
                row.append(fmt_price(o.price))
                row.append(Text(delta_text, style=delta_style))
            else:
                row.extend(["", "", ""])

        table.add_row(*row)

    console.print(table)
    console.print(
        "  [dim]▲ up  ▼ down (≤0.1¢)   Prices = probability in cents[/dim]\n"
    )


# ---------------------------------------------------------------------------
# Markets list
# ---------------------------------------------------------------------------

def render_markets(events: list[Event]) -> None:
    table = Table(
        box=box.SIMPLE_HEAD,
        show_header=True,
        header_style="bold dim",
        pad_edge=True,
        expand=False,
        show_edge=False,
    )

    table.add_column("#",           style="dim", width=3,  justify="right", no_wrap=True)
    table.add_column("Event",                    width=40,                  no_wrap=True)
    table.add_column("Total Vol",   justify="right", width=9,               no_wrap=True)
    table.add_column("24h Vol",     justify="right", width=8,               no_wrap=True)
    table.add_column("Top Outcome",              width=22,                  no_wrap=True)
    table.add_column("Price",       justify="right", width=5,               no_wrap=True)

    for rank, event in enumerate(events, 1):
        v24h_text, v24h_style = fmt_volume_delta(event.volume_24hr)

        # Leading outcome — prefer non-"No" outcomes; highest price wins
        all_flat = [o for m in event.markets for o in m.outcomes]
        candidates = [o for o in all_flat if o.name != "No"] or all_flat
        top = max(candidates, key=lambda o: o.price) if candidates else None

        table.add_row(
            str(rank),
            truncate(event.title, 40),
            fmt_volume(event.volume),
            Text(v24h_text, style=v24h_style),
            truncate(top.name,  22) if top else "—",
            fmt_price(top.price) if top else "—",
        )

    console.print()
    console.print(table)


# ---------------------------------------------------------------------------
# Single event detail
# ---------------------------------------------------------------------------

def render_event(event: Event) -> None:
    console.print()
    console.print(
        Panel(
            f"[bold white]{event.title}[/bold white]",
            style="cyan",
            expand=False,
        )
    )

    # Meta row
    meta_parts = [f"[dim]Vol:[/dim] [white]{fmt_volume(event.volume)}[/white]"]
    if event.volume_24hr:
        t, s = fmt_volume_delta(event.volume_24hr)
        meta_parts.append(f"[dim]24h:[/dim] [{s}]{t}[/{s}]")
    meta_parts.append(f"[dim]Liquidity:[/dim] [white]{fmt_volume(event.liquidity)}[/white]")
    if event.end_date:
        try:
            dt = datetime.fromisoformat(event.end_date.replace("Z", "+00:00"))
            meta_parts.append(f"[dim]Closes:[/dim] [white]{dt.strftime('%b %d, %Y')}[/white]")
        except ValueError:
            pass
    console.print("  " + "   ".join(meta_parts))
    console.print()

    # Detect group event: every market parsed as one outcome (groupItemTitle)
    is_group = all(len(m.outcomes) == 1 for m in event.markets if m.outcomes)

    if is_group:
        all_out = [m.outcomes[0] for m in event.markets if m.outcomes]
        sorted_out = sorted(all_out, key=lambda o: o.price, reverse=True)
        display = [o for o in sorted_out if o.price >= 0.01] or sorted_out[:6]

        tbl = _outcome_table()
        for o in display:
            delta_text, delta_style = fmt_delta(o.price_delta)
            tbl.add_row(
                truncate(o.name, 36),
                fmt_price(o.price),
                Text(delta_text, style=delta_style),
            )
        console.print(tbl)
    else:
        for market in event.markets:
            if not market.outcomes:
                continue
            active = [o for o in market.outcomes if 0.005 < o.price < 0.995]
            display = active if active else market.outcomes

            console.print(f"  [bold]{truncate(market.question, 60)}[/bold]")
            tbl = _outcome_table()
            for o in sorted(display, key=lambda x: x.price, reverse=True):
                delta_text, delta_style = fmt_delta(o.price_delta)
                tbl.add_row(
                    truncate(o.name, 36),
                    fmt_price(o.price),
                    Text(delta_text, style=delta_style),
                )
            console.print(tbl)

    console.print()


def _outcome_table() -> Table:
    tbl = Table(
        box=box.SIMPLE_HEAD,
        show_header=True,
        header_style="bold dim",
        pad_edge=True,
        expand=False,
        show_edge=False,
    )
    tbl.add_column("Outcome", width=36,                  no_wrap=True)
    tbl.add_column("Price",   width=6,  justify="right", no_wrap=True)
    tbl.add_column("Δ24h",    width=7,  justify="right", no_wrap=True)
    return tbl
