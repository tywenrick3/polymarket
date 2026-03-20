# Execution Plan

How to add trade execution to the Polymarket CLI.

## The Big Picture

The CLI already has signals → now we wire them to orders. The gap is auth + the `py-clob-client` SDK.

```
recommend (signals) ──► execute (orders)
                            │
                        py-clob-client
                            │
                    CLOB API (Polygon)
```

## Prerequisites

- A Polygon wallet funded with **USDC.e** (for trades) and **POL** (for gas)
- Private key stored in `POLYMARKET_PRIVATE_KEY` env var
- One-time on-chain token approvals for the exchange contracts

## Dependencies

```bash
pip install py-clob-client  # Polymarket's official Python SDK
```

## Auth Flow

Two-tier system, both handled by the SDK:

1. **L1 (one-time)**: EIP-712 wallet signature → generates API credentials (`apiKey`, `secret`, `passphrase`)
2. **L2 (every request)**: HMAC-SHA256 signed requests using those credentials

```python
from py_clob_client.client import ClobClient

client = ClobClient(
    "https://clob.polymarket.com",
    key=os.environ["POLYMARKET_PRIVATE_KEY"],
    chain_id=137,
)

# One-time: derive and save creds
creds = client.create_or_derive_api_creds()
# Store creds locally (~/.polymarket/creds.json) so you don't re-derive every run
```

## New Files

```
src/polymarket_cli/
├── api/
│   └── executor.py       # ClobClient wrapper, auth, order placement
├── commands/
│   └── execute.py        # `polymarket execute` command
└── config.py             # credential storage (~/.polymarket/creds.json)
```

## Phase 1 — Auth & Config

**`config.py`** — manage credentials:
- Load private key from `POLYMARKET_PRIVATE_KEY` env var
- Cache API creds in `~/.polymarket/creds.json`
- `polymarket auth` command to initialize (derive creds, approve tokens)

**`api/executor.py`** — thin wrapper around `py-clob-client`:
```python
class Executor:
    def __init__(self):
        self.client = ClobClient(
            "https://clob.polymarket.com",
            key=load_private_key(),
            chain_id=137,
            creds=load_cached_creds(),
        )

    def place_order(self, token_id, side, price, size, order_type="GTC"):
        return self.client.create_and_post_order(
            OrderArgs(token_id=token_id, price=price, size=size, side=side),
            options={"tick_size": "0.01", "neg_risk": False},
            order_type=OrderType.GTC,
        )

    def cancel_order(self, order_id):
        return self.client.cancel(order_id)

    def get_positions(self):
        return self.client.get_balances()
```

## Phase 2 — Execute Command

Wire signals directly to orders:

```bash
# Execute top composite signal with $10
polymarket execute --amount 10

# Execute a specific strategy signal
polymarket execute -s momentum --amount 5

# Dry run (show what would happen, don't place)
polymarket execute --amount 10 --dry-run

# Execute against a specific market
polymarket execute --market "fed-rate-cut" --side BUY --amount 25
```

**`commands/execute.py`** flow:
1. Run the existing `recommend` scan to get signals
2. Show the top signal + proposed order details
3. Prompt for confirmation (unless `--yes` flag)
4. Place the order via `Executor.place_order()`
5. Print order confirmation or error

```python
@app.callback(invoke_without_command=True)
def execute(
    strategy: str = "composite",
    amount: float = 10.0,  # USDC
    dry_run: bool = False,
    yes: bool = False,     # skip confirmation
):
    # 1. Get signals (reuse recommend logic)
    signals = asyncio.run(scan(strategy))

    if not signals:
        print("No actionable signals.")
        return

    signal = signals[0]
    size = amount / signal.outcome.price  # number of shares

    # 2. Show what we're about to do
    print(f"{'[DRY RUN] ' if dry_run else ''}"
          f"{signal.direction} {signal.outcome.name} "
          f"@ {signal.outcome.price:.2f} "
          f"× {size:.1f} shares = ${amount:.2f}")

    if dry_run:
        return

    # 3. Confirm
    if not yes:
        confirm = input("Execute? [y/N] ")
        if confirm.lower() != "y":
            return

    # 4. Place order
    executor = Executor()
    result = executor.place_order(
        token_id=signal.outcome.token_id,
        side=signal.direction,
        price=signal.outcome.price,
        size=size,
    )
    print(f"Order placed: {result}")
```

## Phase 3 — Risk Management

Simple guardrails before going live:

- **Max position size**: cap per-trade and total exposure (e.g. `--max-position 50`)
- **Confidence floor**: only execute signals above a threshold (e.g. `--min-confidence 0.6`)
- **Cooldown**: prevent rapid-fire trades on the same outcome
- **Rate limiting**: SDK enforces 60 orders/min, but add client-side throttle too
- **Kill switch**: `polymarket cancel-all` to cancel all open orders

Config in `~/.polymarket/config.json`:
```json
{
  "max_trade_usd": 50,
  "max_total_exposure_usd": 500,
  "min_confidence": 0.6,
  "cooldown_minutes": 30,
  "allowed_strategies": ["composite"]
}
```

## Phase 4 — Portfolio & Monitoring

```bash
polymarket portfolio              # show current positions + P&L
polymarket orders                 # show open orders
polymarket orders --cancel <id>   # cancel a specific order
```

Uses the existing subgraph API (already researched) for position data, plus CLOB API for open orders.

## What the Bots on X Are Actually Doing

Most of them are running one of these loops:

### Loop 1 — Signal Bot (what we'd build)
```
every 5 min:
  scan markets → generate signals → filter by confidence
  if signal.confidence > threshold:
    place limit order
    tweet signal
```

### Loop 2 — Arbitrage Bot
```
every 30 sec:
  for each market:
    if YES_price + NO_price < 0.98:
      buy both sides → guaranteed profit
```

### Loop 3 — Copy-Trade Bot
```
watch whale wallets on-chain (subgraph)
when whale buys:
  immediately mirror the trade
  tweet "whale alert 🐋"
```

### Loop 4 — Market Maker
```
continuously:
  place bid at mid - spread
  place ask at mid + spread
  cancel & replace on price moves
```

## Implementation Order

1. **Auth + config** — get `polymarket auth` working, creds cached
2. **Dry-run execute** — signal → proposed order, no actual placement
3. **Live execute** — actually place orders with confirmation prompt
4. **Risk limits** — position caps, confidence floor, cooldown
5. **Portfolio view** — show positions and open orders
6. **Auto mode** — `polymarket auto --budget 100` runs the loop unattended

## Notes

- The `py-clob-client` handles all the EIP-712 signing complexity
- Orders are off-chain signed, on-chain settled — fast submission, atomic execution
- Start with limit orders (GTC) not market orders — less slippage
- The paper trading system (`paper_positions.json`) is a good testbed before going live
- Polymarket is now legal in the US (CFTC approved late 2025) but requires KYC through regulated intermediaries
