"""Polymarket CLOB API client — price history and series data.

All public functions check the local SQLite cache before hitting the network.
Fresh results are stored back into the cache, accumulating historical data
beyond the CLOB's 28-day window.
"""

import asyncio
from typing import Any

import httpx

from polymarket_cli.cache import (
    get_connection,
    ensure_schema,
    get_cached_batch_series,
    get_cached_series,
    store_series,
)

CLOB_BASE = "https://clob.polymarket.com"


# ---------------------------------------------------------------------------
# Low-level fetchers (always hit the network)
# ---------------------------------------------------------------------------

async def _fetch_history(
    client: httpx.AsyncClient,
    token_id: str,
    interval: str = "1d",
    fidelity: int = 60,
) -> list[dict]:
    """Return raw [{t: unix, p: float}, ...] from the CLOB prices-history endpoint."""
    try:
        resp = await client.get(
            f"{CLOB_BASE}/prices-history",
            params={"market": token_id, "interval": interval, "fidelity": str(fidelity)},
        )
        resp.raise_for_status()
        return resp.json().get("history", [])
    except Exception:
        return []


async def _fetch_price_delta(client: httpx.AsyncClient, token_id: str) -> float:
    """Return the 24hr price delta for a single outcome token."""
    history = await _fetch_history(client, token_id, interval="1d", fidelity=60)
    if len(history) < 2:
        return 0.0
    return round(history[-1]["p"] - history[0]["p"], 4)


# ---------------------------------------------------------------------------
# Price series (for strategies) — cached
# ---------------------------------------------------------------------------

async def fetch_price_series(
    token_id: str,
    interval: str = "1w",
    fidelity: int = 60,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[dict]:
    """Fetch price history for a single token.

    Returns list of {t: unix_timestamp, p: price} dicts, sorted by time.
    Default: 7 days of hourly data (~166 points) — enough for SMA and
    mean-reversion calculations.
    """
    conn = get_connection()
    ensure_schema(conn)

    cached = get_cached_series(conn, token_id, interval)
    if cached is not None:
        conn.close()
        return cached

    if client is not None:
        points = await _fetch_history(client, token_id, interval, fidelity)
    else:
        async with httpx.AsyncClient(timeout=15) as c:
            points = await _fetch_history(c, token_id, interval, fidelity)

    store_series(conn, token_id, interval, points)
    conn.close()
    return points


async def fetch_batch_series(
    token_ids: list[str],
    interval: str = "1w",
    fidelity: int = 60,
    max_concurrent: int = 20,
) -> dict[str, list[dict]]:
    """Fetch price series for multiple tokens concurrently.

    Returns {token_id: [{t, p}, ...]} mapping. Uses cache for fresh data,
    only fetches missing tokens from the network.
    """
    conn = get_connection()
    ensure_schema(conn)

    cached, missing = get_cached_batch_series(conn, token_ids, interval)

    if not missing:
        conn.close()
        return cached

    # Fetch only the missing tokens from the API
    sem = asyncio.Semaphore(max_concurrent)
    fresh: dict[str, list[dict]] = {}

    async def _fetch(client: httpx.AsyncClient, tid: str) -> None:
        async with sem:
            fresh[tid] = await _fetch_history(client, tid, interval, fidelity)

    async with httpx.AsyncClient(timeout=15) as client:
        tasks = [asyncio.create_task(_fetch(client, tid)) for tid in missing]
        await asyncio.gather(*tasks)

    # Store fresh results (accumulates with existing points)
    for tid, points in fresh.items():
        store_series(conn, tid, interval, points)

    conn.close()

    # Merge cached + fresh
    cached.update(fresh)
    return cached


# ---------------------------------------------------------------------------
# Dashboard helper — delta fetching (short TTL, cached per-token)
# ---------------------------------------------------------------------------

async def fill_price_deltas(events: list[Any]) -> None:
    """Mutate events in-place: fill outcome.price_delta from CLOB price history.

    Batches all token requests concurrently with a semaphore to avoid flooding.
    Only processes the first 5 outcomes per event to keep the dashboard fast.
    """
    # Collect tokens that need delta computation
    token_ids = []
    for event in events:
        for market in event.markets:
            for outcome in market.outcomes[:5]:
                if outcome.token_id:
                    token_ids.append(outcome.token_id)

    # Batch fetch 1d series (cached where fresh)
    series_map = await fetch_batch_series(token_ids, interval="1d", fidelity=60)

    # Compute deltas from the series
    for event in events:
        for market in event.markets:
            for i, outcome in enumerate(market.outcomes[:5]):
                if not outcome.token_id:
                    continue
                history = series_map.get(outcome.token_id, [])
                if len(history) < 2:
                    market.outcomes[i].price_delta = 0.0
                else:
                    market.outcomes[i].price_delta = round(
                        history[-1]["p"] - history[0]["p"], 4
                    )
