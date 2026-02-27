# Polymarket CLI

Terminal CLI for Polymarket prediction markets. Read-only, no API key required.

Inspired by [@karpathy's tweet](https://x.com/karpathy/status/2026360908398862478) — CLIs are agent-native interfaces.

## Commands

```bash
polymarket dashboard              # top 10 markets by 24hr volume + price deltas
polymarket dashboard --limit 20   # show more markets
polymarket dashboard --no-deltas  # skip CLOB price history fetch (faster)
polymarket markets                # list markets sorted by total volume
polymarket markets --sort volume_24hr --limit 5
polymarket market <slug>          # detail view for a single event
polymarket market who-will-trump-nominate-as-fed-chair
polymarket search "NBA 2026"      # search active markets by title
python3 recommend.py              # momentum-based single trade recommendation
```

All commands output **JSON automatically when piped** (agent/script friendly):
```bash
polymarket markets --limit 5 | jq '.[].title'
polymarket market fed-rate-cut | jq '.markets[].outcomes'
```

## Development

```bash
pip3 install -e .          # install (already done — binary is at /Library/Frameworks/Python.framework/Versions/3.13/bin/polymarket)
pip3 install -e ".[dev]"   # install with dev deps if added later
```

No tests yet. To smoke-test after changes:
```bash
polymarket markets --limit 3 --format json
polymarket market who-will-trump-nominate-as-fed-chair --no-deltas
polymarket search "bitcoin" --limit 3
```

## Architecture

```
src/polymarket_cli/
├── main.py              # typer app, command registration
├── models.py            # Event, Market, Outcome dataclasses
├── api/
│   ├── gamma.py         # Gamma API client (market data, no auth)
│   └── clob.py          # CLOB client (price history for 24hr deltas)
├── commands/
│   ├── dashboard.py     # polymarket dashboard
│   ├── markets.py       # polymarket markets
│   ├── market.py        # polymarket market <slug>
│   └── search.py        # polymarket search <query>
└── display/
    ├── tables.py        # rich table builders (dashboard, markets, event detail)
    └── format.py        # number formatters ($1.2M, 94¢, ▲0.4)

recommend.py             # standalone trade recommender script (momentum signal)
DESIGN.md                # full design doc with API notes and trade-offs
```

## APIs (both public, no auth)

| API | Base URL | Used for |
|-----|----------|----------|
| Gamma | `https://gamma-api.polymarket.com` | events, markets, volume, search |
| CLOB  | `https://clob.polymarket.com`       | price history (24hr deltas) |

Key Gamma endpoints:
```
GET /events?active=true&closed=false&order=volume24hr&ascending=false&limit=N
GET /events/slug/{slug}
GET /events?active=true&closed=false&limit=500   # used for search (client-side filter)
```

Key CLOB endpoint:
```
GET /prices-history?market={token_id}&interval=1d&fidelity=60
```
Returns `{history: [{t: unix_ts, p: price}, ...]}`. Delta = last.p - first.p.

## Key implementation notes

**Group events** (e.g. "Who wins the 2028 election?"): each candidate is a separate
`Market` with `groupItemTitle = "Gavin Newsom"` and `outcomes = ["Yes", "No"]`.
`gamma.py` detects this and collapses each market into a single `Outcome` using the
`groupItemTitle` as the name and the Yes price as the probability.

**Dashboard responsive layout**: `_max_outcomes()` in `tables.py` calculates how many
outcome columns fit in the current terminal width. Empirically measured:
- Fixed columns = 59 chars
- Per outcome group = 32 chars
- Formula: `max(1, min(5, (terminal_width - 59) // 32))`

**TTY detection**: all commands check `sys.stdout.isatty()`. When piped, output is
always JSON. When in a terminal, output is rich tables.

**Price delta fetch**: `clob.py` batches all CLOB price-history requests concurrently
using `asyncio.gather` with a semaphore of 20. Only fetches for the first 5 outcomes
per event to keep the dashboard fast (~3-5s for 10 markets).

## Data model

```python
Event
  id, slug, title
  volume          # lifetime USD volume
  volume_24hr     # 24hr USD volume
  liquidity
  end_date
  markets: list[Market]

Market
  id, question    # e.g. "Will Gavin Newsom win the 2028 Dem nomination?"
  outcomes: list[Outcome]
  volume, volume_24hr
  token_ids       # CLOB token IDs for price history lookup

Outcome
  name            # "Gavin Newsom" (from groupItemTitle) or "Yes"/"No"
  price           # 0.0–1.0 (probability)
  price_delta     # 24hr change, filled by clob.py
  token_id        # used for CLOB price history
```

## Extending

**Add trading**: requires EIP-712 signing + CLOB auth (L1 = wallet sig, L2 = HMAC).
Use `@polymarket/clob-client` (npm) or the Python SDK. Store private key in
`POLYMARKET_PRIVATE_KEY` env var. See `DESIGN.md § Future Work`.

**Add watch mode**: wrap `dashboard` command in a loop with `time.sleep(30)` and
`console.clear()`. Or use `rich.live.Live` for a proper live-updating display.

**Add portfolio**: Polymarket subgraph at `https://subgraph.satsuma-prod.com/...`
exposes positions by wallet address — no auth required for reads.
