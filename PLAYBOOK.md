# Trading Strategy Playbook

Step-by-step process for generating a short-term Polymarket trading plan using the CLI tools. Designed to be repeatable daily.

---

## Phase 1: Scan the Landscape (~2 min)

Get the lay of the land — what's hot, what's moving.

```bash
# Top markets by 24hr volume (where the money is flowing)
polymarket dashboard --limit 15 --format json | \
  jq -r '.[] | "\(.volume_24hr | . / 1000000 | floor)M  \(.title[:60])"' | sort -rn

# Also check what's expiring soon (near-term catalysts)
polymarket search "march" --limit 20 --format json | \
  jq -r 'sort_by(-.volume_24hr) | .[:10] | .[] | "\(.volume_24hr | . / 1000000 * 10 | floor / 10)M  \(.title[:60])"'
```

**What you're looking for:**
- Markets with >$1M 24hr volume (liquid enough to trade)
- Events expiring within your trading horizon (24hr, 1 week, etc.)
- Group events with many brackets (oil targets, BTC price levels, tweet counts) — these have the most mispricing

## Phase 2: Run All Strategy Signals (~3 min)

Fire off all four strategies. The composite is the primary signal; the individuals tell you *why*.

```bash
# Primary signal — weighted ensemble of all three strategies
polymarket recommend -s composite --top 15 --format json | \
  jq -r '.[] | "\(.confidence | . * 100 | floor)%  \(.direction)  \(.outcome)  @\(.price)  \(.rationale[:80])"'

# Individual strategies for deeper analysis
polymarket recommend -s momentum --top 10 --format json > /tmp/momentum.json
polymarket recommend -s mean-reversion --top 10 --format json > /tmp/meanrev.json
polymarket recommend -s sma --top 10 --format json > /tmp/sma.json
```

**Key fields in the output:**
- `confidence` — 0-1, how strong the signal is
- `direction` — BUY or SELL
- `score` — confidence × agreement count (composite only)
- `rationale` — human-readable breakdown of why

**Interpreting composite rationale:**
```
[2/3 agree] momentum: ▼0.70¢/hr (R²=0.90) | sma: bearish 6.8¢ gap (1.3σ) | mean_reversion: DISAGREES
```
- `2/3 agree` — 2 of 3 strategies agree on direction. Higher = more conviction.
- `R²` — trend cleanliness (>0.7 = strong, clean trend)
- `σ` — SMA gap normalized by volatility (>1.0 = significant)
- `DISAGREES` — flag when a strategy points the other way

### Signal Quality Tiers

| Composite Score | Agreement | R² | Action |
|----------------|-----------|-----|--------|
| >1.5 | 3/3 or 2/3 | >0.8 | High conviction, larger position |
| 1.0-1.5 | 2/3 | >0.65 | Medium conviction |
| 0.5-1.0 | 2/2 or 1/1 | any | Low conviction, small position or skip |
| <0.5 | any | <0.4 | Noise, skip |

**R² minimum gate:** Require R² > 0.65 for any momentum-driven trade. In paper
trading, R²=0.80 (oil) worked while R²=0.47 (BTC) was the biggest loser. Low R²
means the "trend" is indistinguishable from noise — halve the position or skip.

### Strategy Disagreements — The Most Interesting Signal

When momentum/SMA say BUY but mean-reversion says SELL (or vice versa), pay attention:

- **Momentum + SMA agree, mean-reversion disagrees:** A trend is running but may be overextended. **Halve the position size.** In paper trading, BTC had 2/3 agreement but mean-reversion flagged z=+2.45 — the trade lost 17.9%. Treat mean-reversion disagreement as a hard sizing constraint, not just a warning.
- **Mean-reversion flags, others agree with trend:** The price has moved far from its 72hr mean. Often means news-driven move. Check if the news justifies the move — if yes, mean-reversion is wrong; if no, the trend will reverse.

## Phase 3: Deep-Dive the Top Candidates (~3 min)

For each market the signals flag, get the full picture.

```bash
# Detailed view with all outcomes and 24hr deltas
polymarket market <slug> --format json | \
  jq '.markets[] | .outcomes[] | select(.price > 0.01 and .price < 0.99) | "\(.name): \(.price) (Δ\(.price_delta))"'
```

**What to look for:**
- **24hr deltas** — direction and magnitude of recent moves
- **Price level** — avoid extremes (<5¢ or >95¢) unless you have strong conviction on resolution
- **Bracket structure** — in group events, look for brackets where the probability mass is shifting

## Phase 4: News Cross-Reference (~3 min)

**This is the edge.** The algorithms only see price history. You can see the real world.

```bash
# Search for current news on the key markets
firecrawl search "<market topic> latest news today" --limit 5 -o .firecrawl/topic.json --json

# Check current asset prices to validate price-based markets
firecrawl search "crude oil WTI price today" --limit 3 -o .firecrawl/oil.json --json
firecrawl search "bitcoin price today" --limit 3 -o .firecrawl/btc.json --json
```

### Three Patterns to Look For

**1. Signal-News Alignment (Ride the Wave)**
The algo says BUY and the news supports it. Highest conviction — size up.
> Example: Momentum says SELL oil upside targets + real-world oil futures are down 5% today.

**2. Signal-News Divergence (Contrarian Edge)**
The algo says one thing, breaking news says the opposite. The algo is lagging.
> Example: Momentum says BUY Iran ceasefire (price trending up) but Iran's FM *just* rejected ceasefire talks publicly. The price hasn't corrected yet — fade the signal.

**Lesson learned:** Polymarket prices reprice news slowly — these trades need 48-72hr
horizons, not 24hr. Size conservatively (10-15%) and be patient. The Iran contra-news
trade was flat after 3.5hrs despite unambiguous news.

**3. Resolution Convergence (Near-Expiry) — THE BEST EDGE**
A market resolves within 24hrs and one bracket is pulling away. The leading bracket should converge toward $1.00 as uncertainty collapses.
> Example: Elon tweet count bracket at 57¢ with 24hrs to resolution — if it's the right bracket, it runs to 85-95¢ before snapping to $1.00.

**Lesson learned:** This was the top-performing trade type (+11.7% in 3.5hrs). Prioritize
these when scanning — they have the highest hit rate, fastest feedback, and don't depend
on algo signal quality. Look for them first in Phase 1 before running strategy signals.

## Phase 5: Build the Portfolio (~5 min)

### Position Sizing Rules

| Conviction | Signal Quality | News Alignment | % of Bankroll |
|-----------|---------------|----------------|---------------|
| High | Score >1.5, R²>0.8, 3/3 agree | Confirmed | 25-30% |
| High (convergence) | Near-expiry (<24hr), dominant bracket | N/A | 25-30% |
| Medium | Score 1.0-1.5, R²>0.65 | Neutral | 15-20% |
| Medium (contrarian) | News contradicts algo | Strong news | 10-15% |
| Low / Speculative | Mixed signals or mean-rev disagrees | Any | 5-10% |

**Sizing overrides from paper trading:**
- **Mean-reversion disagrees → cap at 10%.** BTC was sized at 20% with a mean-rev
  warning and became the portfolio's biggest drag.
- **Near-expiry convergence → allow up to 30%.** These have the best risk/reward and
  shortest feedback loop. The Musk tweet trade was the top performer at +11.7%.
- **Contrarian news trades → cap at 15% and extend horizon to 48-72hr.** The Iran
  trade was flat after 3.5hrs — Polymarket prices are stickier than expected. These
  need time to reprice.

### Diversification Checklist

Before finalizing, verify:
- [ ] No single trade >30% of bankroll
- [ ] At least 3 uncorrelated markets (don't put 60% in oil)
- [ ] Mix of trade types (momentum, contrarian, convergence)
- [ ] At least one near-term resolution (locks in gains/losses fast)
- [ ] BUY and SELL sides represented (not all directional)
- [ ] No more than 40% of bankroll in trades where mean-reversion disagrees

### The SELL Side

On Polymarket, "SELL" means buying NO shares. When the CLI says `direction: SELL`:
- You're betting the outcome **won't** happen
- Entry price = 1.0 - YES price (the NO share cost)
- The `paper snapshot` command handles this automatically

## Phase 6: Lock It In

```bash
# Record your positions at current live prices
polymarket paper snapshot

# Check results anytime
polymarket paper check

# JSON output for scripting
polymarket paper check | jq '.total_pnl'
```

The snapshot command fetches live entry prices from the Gamma API and saves
everything (token IDs, entry prices, sizes, rationale) to `paper_positions.json`.

The check command fetches current prices from CLOB + Gamma and computes per-trade
and total P&L. Markets that resolve (price hits 0 or 1) are automatically marked
WON or LOST.

---

## Quick Reference: The Full Pipeline

```bash
# 1. Scan
polymarket dashboard --limit 15 --format json > /tmp/dash.json

# 2. Signal
polymarket recommend -s composite --top 15 --format json > /tmp/signals.json

# 3. Drill down
polymarket market <slug> --format json

# 4. News check
firecrawl search "<topic> news today" --limit 5 -o .firecrawl/news.json --json

# 5. Lock positions
polymarket paper snapshot

# 6. Monitor
polymarket paper check
```

## Appendix: How the Strategies Work

### Momentum
- Fits a linear regression over the last 24hrs of hourly price data
- Slope = rate of price change (¢/hr)
- R² = how clean the trend is (0 = noise, 1 = straight line)
- High R² + steep slope = strong, reliable trend

### SMA Crossover
- Compares 6hr simple moving average vs 24hr SMA
- When 6hr > 24hr → bullish crossover (short-term momentum exceeding long-term)
- Gap normalized by price volatility (σ) — a 2¢ gap means more in a low-vol market
- >1σ gap = meaningful signal

### Mean Reversion
- Calculates z-score: how far current price is from its 72hr rolling mean
- z > +2.0 → price is extended above mean → SELL (expect pullback)
- z < -2.0 → price is extended below mean → BUY (expect bounce)
- Penalizes fast moves (likely news-driven, less likely to revert)

### Composite
- Runs all three, takes a weighted vote
- Ranks by `confidence × number_of_agreeing_strategies`
- Requires ≥2 strategies to agree for high-confidence signals
- The rationale shows each strategy's individual read and which disagree
