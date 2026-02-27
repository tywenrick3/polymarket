# Polymarket CLI

A terminal interface for browsing [Polymarket](https://polymarket.com) prediction markets. Read-only, no API key or account required.

Inspired by [@karpathy's tweet](https://x.com/karpathy/status/2026360908398862478) — CLIs are agent-native interfaces.

---

## Installation

Requires Python 3.11+.

```bash
git clone <repo>
cd polymarket
pip3 install -e .
```

Verify it works:

```bash
polymarket --help
```

---

## Commands

### `dashboard` — Top markets at a glance

Shows the top markets by 24-hour volume with live price deltas (how much each outcome moved in the last day).

```bash
polymarket dashboard
```

**Options:**

| Flag | Default | Description |
|------|---------|-------------|
| `--limit N` / `-n N` | `10` | Number of markets to show |
| `--sort FIELD` | `volume_24hr` | Sort by: `volume_24hr`, `volume`, `liquidity` |
| `--no-deltas` | off | Skip price history fetch — loads faster, no delta column |
| `--format json` | `table` | Force JSON output even in a terminal |

```bash
polymarket dashboard --limit 20
polymarket dashboard --no-deltas          # faster, skips CLOB fetch
polymarket dashboard --sort liquidity
```

---

### `markets` — Browse all markets by volume

Lists markets sorted by total lifetime volume. Good for finding large, established markets.

```bash
polymarket markets
```

**Options:**

| Flag | Default | Description |
|------|---------|-------------|
| `--limit N` / `-n N` | `20` | Number of results |
| `--sort FIELD` | `volume` | Sort by: `volume`, `volume_24hr`, `liquidity`, `end_date` |
| `--format json` | `table` | Force JSON output |

```bash
polymarket markets --sort volume_24hr --limit 5
polymarket markets --sort end_date --limit 50
```

---

### `market <slug>` — Detail view for one event

Shows full detail for a single event: all markets, all outcomes, prices, and 24hr deltas. The slug comes from the Polymarket URL — e.g. `polymarket.com/event/who-wins-the-2028-election` → slug is `who-wins-the-2028-election`.

```bash
polymarket market <slug>
```

**Options:**

| Flag | Default | Description |
|------|---------|-------------|
| `--no-deltas` | off | Skip price history fetch |
| `--format json` | `table` | Force JSON output |

```bash
polymarket market who-will-trump-nominate-as-fed-chair
polymarket market fed-rate-cut --no-deltas
polymarket market bitcoin-price-2026 --format json
```

---

### `search <query>` — Search active markets

Searches active, open markets by title. Useful when you know a topic but not the exact slug.

```bash
polymarket search "NBA 2026"
polymarket search "bitcoin"
polymarket search "Fed rate"
```

**Options:**

| Flag | Default | Description |
|------|---------|-------------|
| `--limit N` / `-n N` | `10` | Max results to show |
| `--format json` | `table` | Force JSON output |

```bash
polymarket search "election" --limit 20
polymarket search "AI" --format json
```

---

### `recommend` — Momentum-based trade signal

A standalone script that fetches the top 30 markets, scores every outcome using a momentum signal, and surfaces the single most interesting trade opportunity.

```bash
python3 recommend.py
```

**Signal formula:** `price_delta × log(volume_24hr + 1) × mid_range_weight`

- Favors outcomes that rose in price in the last 24h (momentum)
- Weighted by recent volume (high volume = stronger signal)
- Rewards mid-range prices (10–70¢ sweet spot — not near-resolved)
- Skips markets closing in < 3 days and live/sports events

> **Not financial advice.** This is a heuristic tool for exploration.

---

## JSON output & piping

Every command outputs **JSON automatically when piped**, making it easy to use with `jq`, scripts, or AI agents.

```bash
# Get titles of top 5 markets
polymarket markets --limit 5 | jq '.[].title'

# Get all outcomes for a specific event
polymarket market fed-rate-cut | jq '.markets[].outcomes'

# Search and extract slugs
polymarket search "bitcoin" | jq '.[].slug'

# Dashboard as JSON
polymarket dashboard --limit 10 | jq '.[].volume_24hr'
```

You can also force JSON in a terminal with `--format json`:

```bash
polymarket market bitcoin-price-2026 --format json | jq .
```

---

## Reading the dashboard

The dashboard table has responsive columns — the number of outcome columns shown adapts to your terminal width.

| Column | Description |
|--------|-------------|
| **Event** | Market title, truncated to fit |
| **Vol 24h** | Trading volume in the last 24 hours |
| **Vol Total** | Lifetime trading volume |
| **Outcome columns** | Each outcome name with its current price and 24hr delta |

Price deltas use `▲` (up) and `▼` (down) indicators. No delta shown when `--no-deltas` is used or for outcomes without enough price history.

---

## Tips

**Find a market's slug:** Go to `polymarket.com`, open any event, copy the last part of the URL.

```
https://polymarket.com/event/who-wins-nba-finals-2026
                              ^^^^^^^^^^^^^^^^^^^^^^^^
                              this is the slug
```

**Speed up the dashboard:** Add `--no-deltas` to skip the CLOB price history fetch. The table loads in under a second instead of 3–5 seconds.

**Watch markets for a topic over time:**
```bash
# Re-run every 60 seconds (basic watch)
watch -n 60 polymarket dashboard --no-deltas
```

**Use in shell scripts:**
```bash
#!/bin/bash
# Alert if any top market has > $500k 24hr volume
polymarket markets --limit 10 --format json \
  | jq '.[] | select(.volume_24hr > 500000) | .title'
```

---

## Requirements

- Python 3.11+
- Dependencies (installed automatically): `typer`, `rich`, `httpx`
- No API key, no account, no wallet required

Both APIs used are public:
- **Gamma API** (`gamma-api.polymarket.com`) — market data, search, volume
- **CLOB API** (`clob.polymarket.com`) — price history for 24hr deltas
