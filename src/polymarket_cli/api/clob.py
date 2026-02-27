"""Polymarket CLOB API client — price history for 24hr deltas."""

import asyncio
from typing import Any

import httpx

CLOB_BASE = "https://clob.polymarket.com"


async def _fetch_price_delta(client: httpx.AsyncClient, token_id: str) -> float:
    """Return the 24hr price delta for a single outcome token."""
    try:
        resp = await client.get(
            f"{CLOB_BASE}/prices-history",
            params={"market": token_id, "interval": "1d", "fidelity": "60"},
        )
        resp.raise_for_status()
        history = resp.json().get("history", [])
        if len(history) < 2:
            return 0.0
        return round(history[-1]["p"] - history[0]["p"], 4)
    except Exception:
        return 0.0


async def fill_price_deltas(events: list[Any]) -> None:
    """Mutate events in-place: fill outcome.price_delta from CLOB price history.

    Batches all token requests concurrently with a semaphore to avoid flooding.
    Only processes the first 5 outcomes per event to keep the dashboard fast.
    """
    sem = asyncio.Semaphore(20)

    async def fetch_with_sem(client: httpx.AsyncClient, token_id: str) -> float:
        async with sem:
            return await _fetch_price_delta(client, token_id)

    async with httpx.AsyncClient(timeout=15) as client:
        tasks: list[tuple[Any, int, asyncio.Task]] = []

        for event in events:
            for market in event.markets:
                for i, outcome in enumerate(market.outcomes[:5]):
                    if outcome.token_id:
                        task = asyncio.create_task(
                            fetch_with_sem(client, outcome.token_id)
                        )
                        tasks.append((market, i, task))

        # Await all tasks
        for market, i, task in tasks:
            market.outcomes[i].price_delta = await task
