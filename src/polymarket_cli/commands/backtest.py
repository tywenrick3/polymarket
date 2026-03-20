"""Backtest command — replay historical data to measure strategy accuracy.

Walks through each token's price series hour-by-hour, simulating what each
strategy would have recommended at each point.  Then checks if the price
moved in the predicted direction over the next N hours.

This is a *directional accuracy* test, not a P&L simulation.  It answers:
"When strategy X said BUY, did the price go up?"
"""

import asyncio
import json
import math
import os
import sys
from datetime import datetime, timezone
from typing import Annotated

import typer

from polymarket_cli.api.gamma import fetch_top_events
from polymarket_cli.api.clob import fetch_batch_series
from polymarket_cli.display.tables import console
from polymarket_cli.strategies.momentum import MomentumStrategy
from polymarket_cli.strategies.sma import SMAStrategy
from polymarket_cli.strategies.mean_reversion import MeanReversionStrategy
from polymarket_cli.strategies.composite import CompositeStrategy
from polymarket_cli.strategies.base import TradeSignal

from rich.table import Table
from rich.rule import Rule
from rich.text import Text

app = typer.Typer()

STRATEGY_MAP = {
    "composite": CompositeStrategy,
    "momentum": MomentumStrategy,
    "sma": SMAStrategy,
    "mean-reversion": MeanReversionStrategy,
}

STRATEGY_NAMES = ", ".join(STRATEGY_MAP.keys())

# Minimum lookback (hours of history needed before the strategy can score).
# We start simulating after this many points so each strategy has enough data.
MIN_LOOKBACK = 80  # covers 72hr mean-reversion + margin

LOG_DIR = os.path.expanduser("~/.polymarket/backtest_logs")


# ---------------------------------------------------------------------------
# Core backtesting logic
# ---------------------------------------------------------------------------

def _backtest_single_strategy(
    strategy,
    event,
    outcome,
    full_series: list[dict],
    horizon_hrs: int,
    step_hrs: int,
) -> list[dict]:
    """Walk through a series and record predictions vs actual outcomes.

    Returns a list of result dicts:
      {time, direction, confidence, actual_delta, correct}
    """
    results = []
    n = len(full_series)

    for i in range(MIN_LOOKBACK, n - horizon_hrs, step_hrs):
        # Give the strategy all data up to point i
        history_window = full_series[:i + 1]

        # Temporarily set the outcome price to the current point
        current_price = full_series[i]["p"]
        outcome.price = current_price

        sig = strategy.score(event, outcome, history_window)
        if sig is None:
            continue

        # Check what actually happened over the horizon
        future_price = full_series[i + horizon_hrs]["p"]
        actual_delta = future_price - current_price

        # Did the prediction match?
        if sig.direction == "BUY":
            correct = actual_delta > 0.001  # moved up by at least 0.1¢
        else:
            correct = actual_delta < -0.001  # moved down

        results.append({
            "time": full_series[i]["t"],
            "direction": sig.direction,
            "confidence": sig.confidence,
            "price_at_signal": current_price,
            "price_after": future_price,
            "actual_delta": round(actual_delta, 4),
            "correct": correct,
        })

    return results


def _aggregate_results(results: list[dict]) -> dict:
    """Compute summary stats from backtest results."""
    if not results:
        return {
            "total_signals": 0,
            "correct": 0,
            "wrong": 0,
            "win_rate": 0.0,
            "avg_confidence": 0.0,
            "avg_delta_when_correct": 0.0,
            "avg_delta_when_wrong": 0.0,
        }

    correct = [r for r in results if r["correct"]]
    wrong = [r for r in results if not r["correct"]]

    avg_conf = sum(r["confidence"] for r in results) / len(results)
    avg_delta_correct = (
        sum(abs(r["actual_delta"]) for r in correct) / len(correct)
        if correct else 0.0
    )
    avg_delta_wrong = (
        sum(abs(r["actual_delta"]) for r in wrong) / len(wrong)
        if wrong else 0.0
    )

    return {
        "total_signals": len(results),
        "correct": len(correct),
        "wrong": len(wrong),
        "win_rate": len(correct) / len(results),
        "avg_confidence": round(avg_conf, 3),
        "avg_delta_when_correct": round(avg_delta_correct * 100, 2),  # in cents
        "avg_delta_when_wrong": round(avg_delta_wrong * 100, 2),
    }


# ---------------------------------------------------------------------------
# Multi-run averaging
# ---------------------------------------------------------------------------

def _average_multi_run(
    all_runs: list[dict[str, dict]],
) -> dict[str, dict]:
    """Average results across N runs, include std dev for key metrics."""
    strategy_names = all_runs[0].keys()
    averaged: dict[str, dict] = {}

    for name in strategy_names:
        per_run = [run[name] for run in all_runs]

        # Collect per-run values for averaging
        win_rates = [r["win_rate"] for r in per_run if r["total_signals"] > 0]
        signals = [r["total_signals"] for r in per_run]
        confs = [r["avg_confidence"] for r in per_run if r["total_signals"] > 0]
        delta_wins = [r["avg_delta_when_correct"] for r in per_run if r["total_signals"] > 0]
        delta_losses = [r["avg_delta_when_wrong"] for r in per_run if r["total_signals"] > 0]

        def _mean(xs: list[float]) -> float:
            return sum(xs) / len(xs) if xs else 0.0

        def _std(xs: list[float]) -> float:
            if len(xs) < 2:
                return 0.0
            m = _mean(xs)
            return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))

        averaged[name] = {
            "total_signals": round(_mean(signals)),
            "win_rate": round(_mean(win_rates), 4),
            "win_rate_std": round(_std(win_rates), 4),
            "avg_confidence": round(_mean(confs), 3),
            "avg_delta_when_correct": round(_mean(delta_wins), 2),
            "avg_delta_when_wrong": round(_mean(delta_losses), 2),
            "n_runs": len(all_runs),
        }

    return averaged


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _save_log(
    run_results: dict[str, dict],
    run_number: int,
    total_runs: int,
    horizon: int,
    limit: int,
) -> str:
    """Save a single run's results to a JSON log file. Returns the file path."""
    os.makedirs(LOG_DIR, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"backtest_{ts}_run{run_number}.json"
    path = os.path.join(LOG_DIR, filename)

    log_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "run": run_number,
        "total_runs": total_runs,
        "horizon_hrs": horizon,
        "market_limit": limit,
        "results": run_results,
    }

    with open(path, "w") as f:
        json.dump(log_entry, f, indent=2)

    return path


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def _render_backtest(
    strategy_results: dict[str, dict],
    horizon_hrs: int,
    n_markets: int,
    n_tokens: int,
    is_averaged: bool = False,
) -> None:
    console.print()
    title = "BACKTEST RESULTS (AVERAGED)" if is_averaged else "BACKTEST RESULTS"
    console.print(Rule(f"[bold cyan]{title}[/bold cyan]", style="cyan dim"))
    console.print()

    n_runs = next(iter(strategy_results.values())).get("n_runs", 1) if is_averaged else 1
    meta_parts = [
        f"Scanned {n_markets} markets ({n_tokens} outcomes)",
        f"{horizon_hrs}hr lookahead",
        "interval=max (~28 days)",
    ]
    if is_averaged:
        meta_parts.append(f"averaged over {n_runs} runs")
    console.print(f"  [dim]{' · '.join(meta_parts)}[/dim]")
    console.print()

    table = Table(show_header=True, header_style="bold dim", box=None, padding=(0, 2))
    table.add_column("Strategy", min_width=16)
    table.add_column("Signals", justify="right", width=8)
    table.add_column("Win Rate", justify="right", width=14)
    table.add_column("Avg Conf", justify="right", width=9)
    table.add_column("Avg Δ Win", justify="right", width=10)
    table.add_column("Avg Δ Loss", justify="right", width=10)

    for name, stats in strategy_results.items():
        if stats["total_signals"] == 0:
            table.add_row(name, "0", "—", "—", "—", "—")
            continue

        wr = stats["win_rate"]
        if wr >= 0.6:
            wr_style = "bold green"
        elif wr >= 0.5:
            wr_style = "yellow"
        else:
            wr_style = "bold red"

        # Show ±std if available
        wr_text = f"{wr:.0%}"
        if "win_rate_std" in stats and stats["win_rate_std"] > 0:
            wr_text += f" ±{stats['win_rate_std']:.0%}"

        table.add_row(
            name,
            str(stats["total_signals"]),
            Text(wr_text, style=wr_style),
            f"{stats['avg_confidence']:.0%}",
            f"{stats['avg_delta_when_correct']:.1f}¢",
            f"{stats['avg_delta_when_wrong']:.1f}¢",
        )

    console.print(table)
    console.print()

    # Interpretation guide
    console.print("  [dim]Win Rate    — % of signals where price moved in predicted direction[/dim]")
    console.print("  [dim]Avg Δ Win   — average price move (¢) when the signal was correct[/dim]")
    console.print("  [dim]Avg Δ Loss  — average price move (¢) when the signal was wrong[/dim]")
    console.print("  [dim]Good sign   — win rate > 55% AND avg Δ win > avg Δ loss[/dim]")
    console.print()
    console.print(
        "  [dim bold yellow]⚠  Past performance on ~28 days of data. "
        "Not indicative of future results.[/dim bold yellow]\n"
    )


def _render_run_progress(
    run_number: int,
    total_runs: int,
    run_results: dict[str, dict],
    log_path: str,
) -> None:
    """Print a compact progress line after each run completes."""
    parts = []
    for name, stats in run_results.items():
        if stats["total_signals"] > 0:
            wr = stats["win_rate"]
            parts.append(f"{name}={wr:.0%}")
        else:
            parts.append(f"{name}=—")

    summary = "  ".join(parts)
    console.print(
        f"  [dim]Run {run_number}/{total_runs}[/dim]  {summary}  "
        f"[dim]→ {log_path}[/dim]"
    )


# ---------------------------------------------------------------------------
# Single run (extracted for reuse)
# ---------------------------------------------------------------------------

def _execute_single_run(
    strategies_to_test: list[tuple],
    horizon: int,
    step: int,
    limit: int,
    show_status: bool = True,
) -> tuple[dict[str, dict], int, int]:
    """Fetch data and run all strategies once.

    Returns (strategy_results, n_markets, n_usable_tokens).
    """

    async def run():
        if show_status:
            with console.status("[dim]Fetching top markets…[/dim]", spinner="dots"):
                events = await fetch_top_events(limit=limit, sort="volume_24hr")
        else:
            events = await fetch_top_events(limit=limit, sort="volume_24hr")

        # Collect token IDs
        token_ids = []
        token_meta: list[tuple] = []
        for e in events:
            for m in e.markets:
                for o in m.outcomes[:5]:
                    if o.token_id:
                        token_ids.append(o.token_id)
                        token_meta.append((e, o, o.token_id))

        if show_status:
            with console.status(
                f"[dim]Fetching extended history ({len(token_ids)} tokens, interval=max)…[/dim]",
                spinner="dots",
            ):
                series_map = await fetch_batch_series(
                    token_ids, interval="max", fidelity=60, max_concurrent=20
                )
        else:
            series_map = await fetch_batch_series(
                token_ids, interval="max", fidelity=60, max_concurrent=20
            )

        return events, token_meta, series_map

    events, token_meta, series_map = asyncio.run(run())

    usable = [
        (e, o, tid) for e, o, tid in token_meta
        if len(series_map.get(tid, [])) >= MIN_LOOKBACK + horizon
    ]

    strategy_results: dict[str, dict] = {}

    for strat_name, strat_cls in strategies_to_test:
        strat = strat_cls()
        all_results: list[dict] = []

        for event, outcome, tid in usable:
            series = series_map[tid]
            results = _backtest_single_strategy(
                strat, event, outcome, series, horizon, step
            )
            all_results.extend(results)

        strategy_results[strat_name] = _aggregate_results(all_results)

    return strategy_results, limit, len(usable)


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------

@app.callback(invoke_without_command=True)
def backtest(
    strategy: Annotated[
        str,
        typer.Option("--strategy", "-s", help=f"Strategy to test: {STRATEGY_NAMES}, or 'all'"),
    ] = "all",
    horizon: Annotated[int, typer.Option("--horizon", "-h", help="Hours ahead to check")] = 12,
    step: Annotated[int, typer.Option("--step", help="Hours between simulated signals")] = 6,
    limit: Annotated[int, typer.Option("--limit", help="Markets to scan")] = 20,
    runs: Annotated[int, typer.Option("--runs", "-r", help="Number of runs to average")] = 1,
    log: Annotated[bool, typer.Option("--log/--no-log", help="Save each run to ~/.polymarket/backtest_logs/")] = True,
    fmt: Annotated[str, typer.Option("--format", help="Output format: table or json")] = "table",
) -> None:
    """Backtest strategies against historical price data."""

    if strategy != "all" and strategy not in STRATEGY_MAP:
        console.print(f"[red]Unknown strategy '{strategy}'. Choose from: all, {STRATEGY_NAMES}[/red]")
        raise typer.Exit(1)

    strategies_to_test = (
        list(STRATEGY_MAP.items())
        if strategy == "all"
        else [(strategy, STRATEGY_MAP[strategy])]
    )

    is_json = fmt == "json" or not sys.stdout.isatty()

    if runs == 1:
        # Single run — original behavior
        strategy_results, n_markets, n_tokens = _execute_single_run(
            strategies_to_test, horizon, step, limit, show_status=not is_json
        )

        if log:
            log_path = _save_log(strategy_results, 1, 1, horizon, limit)
            if not is_json:
                console.print(f"  [dim]Log saved → {log_path}[/dim]\n")

        if is_json:
            print(json.dumps(strategy_results, indent=2))
        else:
            _render_backtest(strategy_results, horizon, n_markets, n_tokens)
        return

    # Multi-run mode
    all_runs: list[dict[str, dict]] = []
    n_markets = 0
    n_tokens = 0

    if not is_json:
        console.print()
        console.print(Rule(
            f"[bold cyan]BACKTEST — {runs} RUNS[/bold cyan]",
            style="cyan dim",
        ))
        console.print()

    for i in range(1, runs + 1):
        if not is_json:
            console.print(f"  [bold dim]Starting run {i}/{runs}…[/bold dim]")

        run_results, n_markets, n_tokens = _execute_single_run(
            strategies_to_test, horizon, step, limit, show_status=False
        )
        all_runs.append(run_results)

        log_path = ""
        if log:
            log_path = _save_log(run_results, i, runs, horizon, limit)

        if not is_json:
            _render_run_progress(i, runs, run_results, log_path)

    # Average across runs
    averaged = _average_multi_run(all_runs)

    if is_json:
        output = {
            "averaged": averaged,
            "per_run": all_runs,
        }
        print(json.dumps(output, indent=2))
    else:
        console.print()
        _render_backtest(averaged, horizon, n_markets, n_tokens, is_averaged=True)

        if log:
            console.print(f"  [dim]Logs saved → {LOG_DIR}/[/dim]\n")
