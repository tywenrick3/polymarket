"""Polymarket Gamma API client — public read-only market data."""

import json
import asyncio
from typing import Any

import httpx

from polymarket_cli.models import Event, Market, Outcome

GAMMA_BASE = "https://gamma-api.polymarket.com"

SORT_FIELDS = {
    "volume_24hr": "volume24hr",
    "volume": "volume",
    "liquidity": "liquidity",
    "end_date": "endDate",
}


def _parse_market(raw: dict[str, Any]) -> Market:
    outcomes_raw: list[str] = json.loads(raw.get("outcomes", "[]"))
    prices_raw: list[str] = json.loads(raw.get("outcomePrices", "[]"))
    token_ids: list[str] = json.loads(raw.get("clobTokenIds", "[]"))

    group_title = raw.get("groupItemTitle", "").strip()
    # Multi-outcome events: each market is a candidate with Yes/No outcomes.
    # Use groupItemTitle as the outcome name and the Yes price as the price.
    is_group_market = bool(group_title) and outcomes_raw == ["Yes", "No"]

    outcomes = []
    if is_group_market:
        yes_price = float(prices_raw[0]) if prices_raw else 0.0
        yes_token = token_ids[0] if token_ids else ""
        outcomes.append(
            Outcome(
                name=group_title,
                price=yes_price,
                price_delta=0.0,
                token_id=yes_token,
            )
        )
    else:
        for i, name in enumerate(outcomes_raw):
            price = float(prices_raw[i]) if i < len(prices_raw) else 0.0
            outcomes.append(
                Outcome(
                    name=name,
                    price=price,
                    price_delta=0.0,
                    token_id=token_ids[i] if i < len(token_ids) else "",
                )
            )

    return Market(
        id=raw.get("id", ""),
        question=raw.get("question", raw.get("title", "")),
        outcomes=outcomes,
        volume=float(raw.get("volume", 0) or 0),
        volume_24hr=float(raw.get("volume24hr", 0) or 0),
        token_ids=token_ids,
    )


def _parse_event(raw: dict[str, Any]) -> Event:
    markets = [_parse_market(m) for m in raw.get("markets", [])]
    return Event(
        id=raw.get("id", ""),
        slug=raw.get("slug", ""),
        title=raw.get("title", ""),
        volume=float(raw.get("volume", 0) or 0),
        volume_24hr=float(raw.get("volume24hr", 0) or 0),
        liquidity=float(raw.get("liquidity", 0) or 0),
        markets=markets,
        end_date=raw.get("endDate", ""),
        resolution_source=raw.get("resolutionSource", ""),
    )


async def fetch_top_events(
    limit: int = 10,
    sort: str = "volume_24hr",
) -> list[Event]:
    """Fetch top events sorted by the given field."""
    order = SORT_FIELDS.get(sort, "volume24hr")
    params = {
        "active": "true",
        "closed": "false",
        "order": order,
        "ascending": "false",
        "limit": str(limit),
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{GAMMA_BASE}/events", params=params)
        resp.raise_for_status()
        data = resp.json()
    return [_parse_event(e) for e in data]


async def fetch_event_by_slug(slug: str) -> Event | None:
    """Fetch a single event by its slug."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{GAMMA_BASE}/events/slug/{slug}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
    # endpoint returns a single object or list
    if isinstance(data, list):
        return _parse_event(data[0]) if data else None
    return _parse_event(data)


async def search_events(query: str, limit: int = 10) -> list[Event]:
    """Client-side title search across active events (fetches top 500 by volume)."""
    params = {
        "active": "true",
        "closed": "false",
        "order": "volume",
        "ascending": "false",
        "limit": "500",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{GAMMA_BASE}/events", params=params)
        resp.raise_for_status()
        data = resp.json()

    terms = query.lower().split()
    matches = []
    for raw in data:
        title = raw.get("title", "").lower()
        if all(term in title for term in terms):
            matches.append(_parse_event(raw))
        if len(matches) >= limit:
            break
    return matches
