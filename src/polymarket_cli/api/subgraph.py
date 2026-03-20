"""Polymarket subgraph client — on-chain trade data via Goldsky (public, no auth)."""

import asyncio
from collections import defaultdict
from dataclasses import dataclass

import httpx

SUBGRAPH_BASE = (
    "https://api.goldsky.com/api/public/"
    "project_cl6mb8i9h0003e201j6li0diw/subgraphs"
)

ORDERBOOK_URL = f"{SUBGRAPH_BASE}/orderbook-subgraph/0.0.1/gn"

_FILL_FIELDS = """
    maker
    taker
    makerAssetId
    takerAssetId
    makerAmountFilled
    takerAmountFilled
"""


@dataclass
class WhaleTrade:
    address: str
    outcome_name: str
    side: str            # "BUY" or "SELL"
    usd_amount: float    # total USDC volume
    num_trades: int      # number of fills


async def _query_subgraph(
    client: httpx.AsyncClient, url: str, query: str,
) -> dict:
    resp = await client.post(url, json={"query": query}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(data["errors"][0].get("message", "subgraph error"))
    return data.get("data", {})


def _process_fills(fills: list[dict]) -> dict[tuple[str, str], list]:
    """Aggregate fills into (address, side) -> [total_usd, count].

    Trade direction:
    - makerAssetId="0": maker offers USDC → maker is BUYER, taker is SELLER
    - takerAssetId="0": taker offers USDC → taker is BUYER, maker is SELLER
    """
    agg: dict[tuple[str, str], list] = defaultdict(lambda: [0.0, 0])

    for fill in fills:
        maker_asset = fill["makerAssetId"]
        if maker_asset == "0":
            # Maker pays USDC → maker BUYS, taker SELLS
            usd = int(fill["makerAmountFilled"]) / 1e6
            agg[(fill["maker"], "BUY")][0] += usd
            agg[(fill["maker"], "BUY")][1] += 1
            agg[(fill["taker"], "SELL")][0] += usd
            agg[(fill["taker"], "SELL")][1] += 1
        else:
            # takerAssetId="0": taker pays USDC → taker BUYS, maker SELLS
            usd = int(fill["takerAmountFilled"]) / 1e6
            agg[(fill["taker"], "BUY")][0] += usd
            agg[(fill["taker"], "BUY")][1] += 1
            agg[(fill["maker"], "SELL")][0] += usd
            agg[(fill["maker"], "SELL")][1] += 1

    return agg


async def _fetch_trades_for_token(
    client: httpx.AsyncClient,
    token_id: str,
    outcome_name: str,
    limit: int,
) -> list[WhaleTrade]:
    """Fetch largest trades for a token and aggregate by wallet + side."""
    # Two query directions to capture all large fills
    q1 = """
    {
      orderFilledEvents(
        where: { takerAssetId: "%s", makerAssetId: "0" }
        orderBy: makerAmountFilled
        orderDirection: desc
        first: %d
      ) { %s }
    }
    """ % (token_id, limit * 5, _FILL_FIELDS)

    q2 = """
    {
      orderFilledEvents(
        where: { makerAssetId: "%s", takerAssetId: "0" }
        orderBy: takerAmountFilled
        orderDirection: desc
        first: %d
      ) { %s }
    }
    """ % (token_id, limit * 5, _FILL_FIELDS)

    d1, d2 = await asyncio.gather(
        _query_subgraph(client, ORDERBOOK_URL, q1),
        _query_subgraph(client, ORDERBOOK_URL, q2),
    )

    all_fills = (
        d1.get("orderFilledEvents", []) + d2.get("orderFilledEvents", [])
    )
    agg = _process_fills(all_fills)

    whales = []
    for (addr, side), (total_usd, count) in agg.items():
        whales.append(WhaleTrade(
            address=addr,
            outcome_name=outcome_name,
            side=side,
            usd_amount=total_usd,
            num_trades=count,
        ))

    whales.sort(key=lambda w: w.usd_amount, reverse=True)
    return whales[:limit]


async def fetch_whales_for_event(
    token_outcome_map: dict[str, str],
    limit: int = 10,
) -> list[WhaleTrade]:
    """Fetch top traders across all outcomes of an event.

    token_outcome_map: {token_id: outcome_name}
    Returns whale trades sorted by USD volume descending.
    """
    sem = asyncio.Semaphore(10)

    async def fetch_with_sem(
        client: httpx.AsyncClient, token_id: str, name: str,
    ) -> list[WhaleTrade]:
        async with sem:
            try:
                return await _fetch_trades_for_token(client, token_id, name, limit)
            except Exception:
                return []

    async with httpx.AsyncClient() as client:
        tasks = [
            fetch_with_sem(client, tid, name)
            for tid, name in token_outcome_map.items()
        ]
        results = await asyncio.gather(*tasks)

    all_whales = [w for batch in results for w in batch]
    all_whales.sort(key=lambda w: w.usd_amount, reverse=True)
    return all_whales[:limit]
