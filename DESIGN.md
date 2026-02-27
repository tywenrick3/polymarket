# Polymarket CLI — Design Document

Inspired by [@karpathy's tweet](https://x.com/karpathy/status/2026360908398862478) (Feb 24, 2026): a terminal-native, agent-friendly CLI for exploring Polymarket prediction markets.

---

## 1. Goals

- **Replicate and extend the screenshot**: a `dashboard` command that renders the highest-volume markets with 24hr price deltas, exactly like the Karpathy/Claude screenshot.
- **Agent-first design**: every command produces clean, parseable output (table, JSON, or plain text). No interactive prompts by default. Flags are explicit and composable.
- **Zero auth for read-only**: all market-data commands hit the public Gamma REST API. No wallet or API key required.
- **Extensible base**: structure the codebase so trading, portfolio, and watch-mode can be added later without rearchitecting.

---

## 2. Scope (v1)

| Command | Description |
|---|---|
| `polymarket dashboard` | Top-N markets by volume, with 24hr change — mimics the screenshot |
| `polymarket markets` | List/search/filter markets with flexible sorting |
| `polymarket market <slug>` | Detailed view of a single event: all outcomes, prices, orderbook summary |
| `polymarket search <query>` | Full-text search across active market titles |

Out of scope for v1: trading, portfolio/positions, live watch mode, WebSocket streaming.

---

## 3. Technology Choices

| Concern | Choice | Rationale |
|---|---|---|
| Language | **Python 3.11+** | Fast iteration, best-in-class TUI libs, easy for agents to extend |
| HTTP client | **httpx** | Async-capable, clean API, supports connection pooling |
| Terminal rendering | **rich** | Tables, colors, panels, progress — battle-tested, no curses |
| CLI framework | **typer** | Built on Click, auto-generates `--help`, type-safe args |
| Config | **~/.config/polymarket/config.toml** | Optional, for defaults like `--limit`, output format |
| Packaging | **uv / pyproject.toml** | Single `uv tool install` for users; `pip install -e .` for dev |
| Entrypoint | `polymarket` binary | Installed to PATH, callable directly from shell and by agents |

---

## 4. API Layer

### 4.1 Polymarket Gamma API (market data)

Base URL: `https://gamma-api.polymarket.com`

| Endpoint | Used for |
|---|---|
| `GET /events?active=true&closed=false&order=volume_24hr&ascending=false&limit=N` | Dashboard: top markets by 24hr volume |
| `GET /events?active=true&closed=false&order=volume&ascending=false&limit=N` | Markets: top markets by total volume |
| `GET /events/slug/{slug}` | Market detail view |
| `GET /events?q={query}&active=true` | Search |
| `GET /tags` | Available tag categories |

No API key required for read-only access.

### 4.2 Key response fields

```
Event
  id, slug, title
  volume          — total lifetime volume (USD)
  volume_24hr     — 24hr volume change (USD)
  liquidity
  markets[]
    id, question  — individual outcome market (e.g. "Gavin Newsom")
    outcomePrices — [yesPrice, noPrice] as strings ("0.94", "0.06")
    lastTradePrice
    volume, volume24hr

```

### 4.3 Price delta calculation

The API does not directly return a "24hr price delta." We derive it:

```
delta = current_price - price_24hr_ago
```

To get `price_24hr_ago`, use the CLOB price history endpoint:
`GET https://clob.polymarket.com/prices-history?market={token_id}&interval=1d&fidelity=1`

This returns a series of `{t, p}` (timestamp, price) points. Take the earliest point in the 1-day window as the baseline.

For the dashboard we can also approximate by storing the snapshot on first run and computing delta on subsequent runs — or simply display the raw `volume_24hr` as the "24h" column (matching the screenshot's intent, which shows 24hr *volume* change, not price change).

> **Screenshot analysis**: The `24h` column in the screenshot shows dollar amounts ($8M, $2.2M, etc.) which are 24hr *volume* changes, not price deltas. The per-outcome `Δ24h` column (e.g. `-0.4`, `+0.3`) shows price changes in cents. We'll fetch price history for the outcome deltas.

---

## 5. Command Design

### 5.1 `polymarket dashboard`

```
$ polymarket dashboard [--limit N] [--format table|json]
```

Output matches the screenshot: ranked table of top events by 24hr volume, with up to 5 outcomes per event showing current price and 24hr delta.

```
POLYMARKET   Feb 24 10:15

  #   Event                           Total      24h     #1                    Prc¢  Δ24h  ...
  1   Democratic Presidential Nom...  $707.6M   +$8M    Gavin Newsom           94¢  ▼0.4
  2   Who will Trump nominate...       $512.1M   +$2.2M  Kevin Warsh            94¢  ▲0.1
  ...

Deltas: ▲ up  ▼ down (≤0.1¢)   Prices = probability in cents
```

Flags:
- `--limit N` (default: 10)
- `--format table|json` (default: table)
- `--sort volume_24hr|volume|liquidity` (default: volume_24hr)

### 5.2 `polymarket markets`

```
$ polymarket markets [--sort volume_24hr|volume|liquidity|end_date]
                     [--limit N]
                     [--tag <tag-name>]
                     [--format table|json|csv]
```

Compact list of events. One line per event: rank, title, total volume, 24hr volume, top outcome + price.

### 5.3 `polymarket market <slug>`

```
$ polymarket market fed-rate-cut-march-2025
```

Full detail view for a single event:
- Event title, end date, resolution source
- All outcomes in a ranked table: outcome name, price, 24hr delta, volume
- Orderbook summary: best bid/ask spread

### 5.4 `polymarket search <query>`

```
$ polymarket search "bitcoin ETF"
$ polymarket search "2026 NBA" --limit 5
```

Searches active markets by title. Returns a compact table identical to `markets`.

---

## 6. Output Formats

All commands support `--format table|json`. JSON output is newline-delimited (one object per line) for easy `jq` piping:

```bash
polymarket markets --format json | jq '.[] | {title, volume_24hr}'
```

When stdout is not a TTY (i.e. piped to another process), the CLI automatically switches to JSON and strips ANSI color codes — making it agent-friendly without requiring explicit flags.

---

## 7. Project Structure

```
polymarket/
├── pyproject.toml
├── README.md
├── DESIGN.md
└── src/
    └── polymarket_cli/
        ├── __init__.py
        ├── main.py          # typer app, command registration
        ├── api/
        │   ├── gamma.py     # Gamma API client (markets, events)
        │   └── clob.py      # CLOB client (prices-history, orderbook)
        ├── commands/
        │   ├── dashboard.py
        │   ├── markets.py
        │   ├── market.py
        │   └── search.py
        ├── display/
        │   ├── tables.py    # rich table builders
        │   └── format.py    # number formatting ($1.2M, 94¢, ▲0.4)
        └── models.py        # dataclasses for Event, Market, Price
```

---

## 8. Installation

```bash
# Development
git clone ...
cd polymarket
uv sync
uv run polymarket dashboard

# End-user (once published to PyPI)
uv tool install polymarket-cli
polymarket dashboard
```

---

## 9. Design Decisions & Trade-offs

### Why not live streaming (v1)?
The screenshot is a static snapshot. Live streaming requires WebSocket connections to the CLOB, which adds complexity and a persistent process. This is a natural v2 feature (`polymarket watch`).

### Why not trading (v1)?
Trading requires EIP-712 signing, wallet key management, and Polygon gas. This is significant scope that deserves its own design pass. The read-only surface is immediately useful and zero-risk.

### TTY detection for agent friendliness
Following the Unix philosophy: when piped, output clean JSON. When in a terminal, render rich tables. Agents calling `polymarket markets` in a shell get JSON automatically; humans get color tables.

### Price delta approximation
Fetching price history for every outcome in the dashboard adds N*M HTTP calls. Strategy: batch the calls with `asyncio.gather()` for parallelism, cache results with a 60-second TTL in `/tmp/polymarket_cache/`. For the dashboard, cap at the top 5 outcomes per event to keep it bounded.

### Truncation
Event titles can be long. Truncate to a configurable column width (default 35 chars) with ellipsis, matching the screenshot behavior.

---

## 10. Future Work (v2+)

| Feature | Notes |
|---|---|
| `polymarket watch` | Auto-refresh dashboard every N seconds (like `watch -n5`) |
| `polymarket positions <wallet>` | Read open positions for any public wallet address via subgraph |
| `polymarket trade buy/sell` | CLOB order placement with wallet key via env var `POLYMARKET_PRIVATE_KEY` |
| `polymarket notify` | Alert when a market price crosses a threshold |
| MCP server | Expose all commands as an MCP tool for Claude/Codex agents |
| Shell completions | `polymarket --install-completion` for zsh/bash/fish |
| Config file | `~/.config/polymarket/config.toml` for defaults |

---

## 11. Open Questions

1. **Price delta source**: Should `Δ24h` per outcome come from CLOB price history, or should we derive it from a locally-cached previous snapshot? Price history is more accurate but slower; local cache is instant but requires the tool to have been run before.

2. **Geo-restrictions**: Polymarket blocks US IPs for trading. The Gamma API (read-only market data) appears to be unrestricted. Worth confirming before shipping.

3. **PyPI package name**: `polymarket-cli` is likely taken or squatted. Alternatives: `pmkt`, `poly-cli`, `polymarket-terminal`.

4. **Dashboard refresh rate**: For a `--watch` flag on `dashboard`, what's an acceptable poll interval that won't rate-limit us? (Gamma API rate limits are not publicly documented.)
