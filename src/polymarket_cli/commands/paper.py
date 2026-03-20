"""Paper trading tracker — snapshot positions and check P&L later."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import typer

from polymarket_cli.display.format import fmt_price

app = typer.Typer(help="Paper trading tracker")

POSITIONS_FILE = Path(__file__).resolve().parents[3] / "paper_positions.json"
GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"


def _load_positions() -> dict:
    if not POSITIONS_FILE.exists():
        return {}
    return json.loads(POSITIONS_FILE.read_text())


def _save_positions(data: dict) -> None:
    POSITIONS_FILE.write_text(json.dumps(data, indent=2))


def _fetch_current_price(token_id: str) -> float | None:
    """Fetch the latest price for a token from CLOB price history."""
    try:
        resp = httpx.get(
            f"{CLOB_BASE}/prices-history",
            params={"market": token_id, "interval": "1d", "fidelity": 60},
            timeout=15,
        )
        resp.raise_for_status()
        history = resp.json().get("history", [])
        if history:
            return history[-1]["p"]
    except Exception:
        pass
    return None


def _fetch_gamma_price(slug: str, outcome_name: str, direction: str) -> float | None:
    """Fetch current price from Gamma API as fallback / cross-check."""
    try:
        resp = httpx.get(f"{GAMMA_BASE}/events/slug/{slug}", timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            data = data[0] if data else {}
        for m in data.get("markets", []):
            gti = (m.get("groupItemTitle") or "").strip()
            outs = json.loads(m.get("outcomes", "[]"))
            prices = json.loads(m.get("outcomePrices", "[]"))
            is_group = bool(gti) and outs == ["Yes", "No"]
            if is_group and gti == outcome_name:
                yes_price = float(prices[0])
                if direction == "SELL":
                    return round(1.0 - yes_price, 4)
                return round(yes_price, 4)
    except Exception:
        pass
    return None


@app.command()
def check(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show extra detail"),
):
    """Check current prices and calculate P&L for all paper positions."""
    data = _load_positions()
    if not data or "trades" not in data:
        typer.echo("No positions found. Run `polymarket paper snapshot` first.")
        raise typer.Exit(1)

    entry_time = data.get("snapshot_time", "unknown")
    bankroll = data.get("bankroll", 100.0)
    trades = data["trades"]

    # Determine if stdout is a TTY for formatting
    is_tty = sys.stdout.isatty()

    if is_tty:
        from rich.console import Console
        from rich.table import Table

        console = Console()
        console.print()
        console.print(
            f"[bold]Paper Portfolio Check[/bold]  "
            f"(entered: {entry_time}  |  checked: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')})"
        )
        console.print()

        table = Table(show_header=True, header_style="bold")
        table.add_column("#", width=3)
        table.add_column("Market", min_width=20)
        table.add_column("Direction", width=6)
        table.add_column("Entry", justify="right", width=8)
        table.add_column("Current", justify="right", width=8)
        table.add_column("Δ Price", justify="right", width=8)
        table.add_column("Size", justify="right", width=8)
        table.add_column("P&L", justify="right", width=10)
        table.add_column("Return", justify="right", width=8)
        table.add_column("Status", width=10)
    else:
        results = []

    total_pnl = 0.0
    total_invested = 0.0
    wins = 0
    losses = 0

    for i, trade in enumerate(trades, 1):
        entry = trade["entry_price"]
        size = trade["size"]
        token_id = trade["token_id"]
        slug = trade["slug"]
        outcome = trade["outcome"]
        direction = trade["direction"]

        # Try CLOB first, fall back to Gamma
        current = _fetch_current_price(token_id)
        if current is None:
            current = _fetch_gamma_price(slug, outcome, direction)

        if current is None:
            # Market may have resolved — try Gamma as fallback
            current = _fetch_gamma_price(slug, outcome, direction)

        if current is None:
            delta = 0.0
            pnl = 0.0
            pnl_pct = 0.0
            status = "???"
        else:
            current = round(current, 4)
            delta = round(current - entry, 4)
            # Shares bought = size / entry_price
            shares = size / entry
            pnl = round(shares * delta, 2)
            pnl_pct = round((delta / entry) * 100, 1)

            # Check if resolved (price near 0 or 1)
            if current >= 0.99:
                status = "WON"
                pnl = round(shares * (1.0 - entry), 2)
                pnl_pct = round(((1.0 - entry) / entry) * 100, 1)
            elif current <= 0.01:
                status = "LOST"
                pnl = -size
                pnl_pct = -100.0
            else:
                status = "OPEN"

        total_pnl += pnl
        total_invested += size
        if pnl > 0:
            wins += 1
        elif pnl < 0:
            losses += 1

        if is_tty:
            # Color the P&L
            if pnl > 0:
                pnl_style = "bold green"
            elif pnl < 0:
                pnl_style = "bold red"
            else:
                pnl_style = "dim"

            if isinstance(current, float):
                current_display = f"{current:.4f}"
                delta_display = f"{delta:+.4f}"
            else:
                current_display = "N/A"
                delta_display = "—"

            pnl_display = f"{'+'if pnl>=0 else ''}${pnl:.2f}"
            ret_display = f"{'+'if pnl_pct>=0 else ''}{pnl_pct:.1f}%"

            status_style = {"WON": "bold green", "LOST": "bold red", "OPEN": "yellow"}.get(status, "dim")

            table.add_row(
                str(i),
                f"{outcome}\n[dim]{slug[:40]}[/dim]",
                direction,
                f"{entry:.4f}",
                current_display,
                delta_display,
                f"${size:.0f}",
                f"[{pnl_style}]{pnl_display}[/{pnl_style}]",
                f"[{pnl_style}]{ret_display}[/{pnl_style}]",
                f"[{status_style}]{status}[/{status_style}]",
            )
        else:
            results.append({
                "outcome": outcome,
                "slug": slug,
                "direction": direction,
                "entry_price": entry,
                "current_price": current,
                "delta": delta,
                "size": size,
                "pnl": pnl,
                "return_pct": pnl_pct,
                "status": status,
            })

    if is_tty:
        console.print(table)
        console.print()

        # Summary
        pnl_color = "green" if total_pnl >= 0 else "red"
        console.print(f"  Bankroll:  ${bankroll:.0f} → [bold {pnl_color}]${bankroll + total_pnl:.2f}[/bold {pnl_color}]")
        console.print(f"  Total P&L: [bold {pnl_color}]{'+'if total_pnl>=0 else ''}${total_pnl:.2f} ({total_pnl/total_invested*100:+.1f}%)[/bold {pnl_color}]")
        console.print(f"  Record:    {wins}W / {losses}L / {len(trades)-wins-losses} open")
        console.print()
    else:
        summary = {
            "snapshot_time": entry_time,
            "check_time": datetime.now(timezone.utc).isoformat(),
            "bankroll": bankroll,
            "current_value": round(bankroll + total_pnl, 2),
            "total_pnl": round(total_pnl, 2),
            "return_pct": round(total_pnl / total_invested * 100, 1),
            "wins": wins,
            "losses": losses,
            "open": len(trades) - wins - losses,
            "trades": results,
        }
        typer.echo(json.dumps(summary, indent=2))


@app.command()
def snapshot():
    """Record current positions (re-fetches live prices as entry points)."""
    typer.echo("Fetching live prices for all positions...")

    trades_config = [
        {
            "slug": "elon-musk-of-tweets-march-10-march-17",
            "outcome": "260-279",
            "direction": "BUY",
            "size": 30.0,
            "rationale": "Near-resolution convergence — resolves Mar 17",
        },
        {
            "slug": "will-crude-oil-cl-hit-by-end-of-march",
            "outcome": "\u2191 $110",
            "direction": "SELL",
            "size": 25.0,
            "rationale": "Oil crashing, R²=0.80 downtrend, buy NO",
        },
        {
            "slug": "what-price-will-bitcoin-hit-in-march-2026",
            "outcome": "\u2191 80,000",
            "direction": "BUY",
            "size": 20.0,
            "rationale": "BTC momentum +16c/24hr, already hit $75k this month",
        },
        {
            "slug": "us-x-iran-ceasefire-by",
            "outcome": "April 30",
            "direction": "SELL",
            "size": 15.0,
            "rationale": "Contra-news: Iran FM rejected ceasefire today, buy NO",
        },
        {
            "slug": "elon-musk-of-tweets-march-13-march-20",
            "outcome": "240-259",
            "direction": "BUY",
            "size": 10.0,
            "rationale": "Tweet rate declining, 5.8x payout, resolves Mar 20",
        },
    ]

    trades = []
    for tc in trades_config:
        # Fetch token ID and live price from Gamma
        try:
            resp = httpx.get(
                f"{GAMMA_BASE}/events/slug/{tc['slug']}", timeout=15
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                data = data[0]

            token_id = ""
            entry_price = 0.0
            for m in data.get("markets", []):
                gti = (m.get("groupItemTitle") or "").strip()
                outs = json.loads(m.get("outcomes", "[]"))
                prices = json.loads(m.get("outcomePrices", "[]"))
                tids = json.loads(m.get("clobTokenIds", "[]"))
                is_group = bool(gti) and outs == ["Yes", "No"]
                if is_group and gti == tc["outcome"]:
                    if tc["direction"] == "SELL":
                        token_id = tids[1] if len(tids) > 1 else ""
                        entry_price = round(1.0 - float(prices[0]), 4)
                    else:
                        token_id = tids[0] if tids else ""
                        entry_price = round(float(prices[0]), 4)
                    break

            trades.append({
                **tc,
                "token_id": token_id,
                "entry_price": entry_price,
            })
            typer.echo(f"  ✓ {tc['outcome']:25s}  entry={entry_price:.4f}  ${tc['size']:.0f}")
        except Exception as e:
            typer.echo(f"  ✗ {tc['outcome']:25s}  ERROR: {e}")
            trades.append({**tc, "token_id": "", "entry_price": 0.0})

    positions = {
        "snapshot_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "bankroll": 100.0,
        "strategy": "24hr-momentum-contrarian",
        "trades": trades,
    }

    _save_positions(positions)
    typer.echo(f"\nSaved {len(trades)} positions to {POSITIONS_FILE.name}")
    typer.echo("Run `polymarket paper check` anytime to see P&L.")
