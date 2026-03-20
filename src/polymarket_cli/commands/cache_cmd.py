"""Cache management commands — inspect and clear the local data cache."""

import json
import sys
from datetime import datetime, timezone

import typer

from polymarket_cli.cache import (
    get_connection,
    ensure_schema,
    cache_stats,
    clear_cache,
)

app = typer.Typer(help="Manage the local data cache")


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024  # type: ignore[assignment]
    return f"{n:.1f} TB"


def _fmt_ts(ts: int | None) -> str:
    if ts is None:
        return "—"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


@app.command()
def stats() -> None:
    """Show cache statistics."""
    conn = get_connection()
    ensure_schema(conn)
    s = cache_stats(conn)
    conn.close()

    if not sys.stdout.isatty():
        print(json.dumps(s, indent=2))
        return

    from rich.console import Console
    from rich.table import Table

    console = Console()
    console.print()
    console.print("[bold]Cache Statistics[/bold]")
    console.print()

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="dim")
    table.add_column("Value")

    table.add_row("Database", s["db_path"])
    table.add_row("Size", _fmt_size(s["db_size_bytes"]))
    table.add_row("Price points", f"{s['price_points']:,}")
    table.add_row("Tokens tracked", f"{s['tokens_tracked']:,}")
    table.add_row("Event snapshots", f"{s['event_snapshots']:,}")
    table.add_row("External points", f"{s['external_points']:,}")
    table.add_row("Oldest data", _fmt_ts(s["oldest_timestamp"]))
    table.add_row("Newest data", _fmt_ts(s["newest_timestamp"]))

    console.print(table)
    console.print()


@app.command()
def clear(
    series: bool = typer.Option(False, "--series", help="Clear price series only"),
    events: bool = typer.Option(False, "--events", help="Clear event snapshots only"),
    external: bool = typer.Option(False, "--external", help="Clear external data only"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Clear cached data."""
    clear_all = not (series or events or external)

    if not yes:
        scope = "ALL cached data" if clear_all else ", ".join(
            name for flag, name in [
                (series, "price series"),
                (events, "event snapshots"),
                (external, "external data"),
            ] if flag
        )
        confirm = typer.confirm(f"Delete {scope}?")
        if not confirm:
            raise typer.Abort()

    conn = get_connection()
    ensure_schema(conn)
    clear_cache(conn, series=series, events=events, external=external, all_data=clear_all)
    conn.close()

    typer.echo("Cache cleared.")
