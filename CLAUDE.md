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
polymarket recommend              # composite trade signals (momentum + SMA + mean-reversion)
polymarket recommend -s momentum --top 5    # single strategy, top 5
polymarket recommend -s sma -n 3            # SMA crossover only
polymarket recommend -s mean-reversion      # mean-reversion only
polymarket recommend --limit 50             # scan more markets
polymarket backtest                         # backtest all strategies (~28 days data)
polymarket backtest -s momentum --horizon 6 # single strategy, 6hr lookahead
polymarket backtest --limit 30 --step 3     # more markets, finer granularity
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

```bash
pytest tests/                    # run unit tests (strategies use synthetic data, no network)
```

To smoke-test after changes:
```bash
polymarket markets --limit 3 --format json
polymarket market who-will-trump-nominate-as-fed-chair --no-deltas
polymarket search "bitcoin" --limit 3
polymarket recommend -s momentum --top 3 --format json
polymarket recommend -s composite --top 3 --format json
polymarket backtest --limit 5 --format json
```

## Architecture

```
src/polymarket_cli/
├── main.py              # typer app, command registration
├── models.py            # Event, Market, Outcome dataclasses
├── api/
│   ├── gamma.py         # Gamma API client (market data, no auth)
│   └── clob.py          # CLOB client (price history, batch series, 24hr deltas)
├── commands/
│   ├── dashboard.py     # polymarket dashboard
│   ├── markets.py       # polymarket markets
│   ├── market.py        # polymarket market <slug>
│   ├── search.py        # polymarket search <query>
│   ├── recommend.py     # polymarket recommend (multi-strategy signals)
│   ├── backtest.py      # polymarket backtest (replay history, measure accuracy)
│   └── whales.py        # polymarket whales
├── strategies/
│   ├── base.py          # TradeSignal dataclass + Strategy protocol
│   ├── momentum.py      # linear regression slope + R² trend quality
│   ├── sma.py           # 6hr/24hr SMA crossover with volatility normalization
│   ├── mean_reversion.py # z-score vs 72hr rolling mean, speed-penalized
│   └── composite.py     # weighted ensemble, agreement-based ranking
└── display/
    ├── tables.py        # rich table builders (dashboard, markets, event detail)
    └── format.py        # number formatters ($1.2M, 94¢, ▲0.4)

tests/
└── test_strategies.py   # unit tests with synthetic price series

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

Available intervals and what they return (fidelity=60, hourly points):
- `1d` → ~24 points (24hr) — used by dashboard deltas
- `1w` → ~166 points (7 days) — used by recommend strategies
- `max` → ~669 points (28 days) — used by backtest

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

**Strategy system**: `strategies/` implements a pluggable strategy protocol. Each
strategy takes an Event, Outcome, and price series, and returns a `TradeSignal` with
direction (BUY/SELL), confidence (0-1), score, and human-readable rationale.
- **Momentum**: linear regression slope over 24hr window. R² measures trend cleanliness.
- **SMA**: 6hr vs 24hr simple moving average crossover. Gap normalized by volatility (σ).
- **Mean reversion**: z-score vs 72hr rolling mean. Penalizes fast moves (likely news).
- **Composite**: runs all three, ranks by confidence × agreement count. Requires ≥2
  strategies to agree for high-confidence signals.

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
