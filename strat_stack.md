# Quant Strategy Stack (Refactor Notes)

## 1. Cross-Market Divergence
**Goal:** Trade price dislocations of the same asset across venues.

**Core idea:**  
When identical assets trade at different prices across markets, the spread is expected to revert.

**Signal:**
- Spread = Price_A − Price_B  
- Trade when z-score of spread is extreme

**Use cases:**
- BTC Coinbase vs Binance
- Spot vs Futures
- Regional arbitrage proxies

**Pseudo-flow:**
- Compute spread
- Z-score normalize
- Trade mean reversion on extremes

---

## 2. Pair / Network Correlation Model
**Goal:** Generalize pairs trading into a multi-asset graph.

**Core idea:**
Assets form a correlation network; mispricing is relative to a cluster, not just one pair.

**Steps:**
1. Build correlation matrix (returns)
2. Identify strong edges (high correlation pairs)
3. Construct spreads using beta-adjusted relationships
4. Detect deviations via z-score

**Enhancements:**
- PCA for factor decomposition
- Community detection for clusters
- Cointegration for stability filtering

---

## 3. Capital Velocity Regime Filter
**Goal:** Detect market behavior regime (momentum vs mean reversion).

**Definition:**
Capital Velocity ≈ Volume / Open Interest

**Interpretation:**
- High velocity → fast speculation → momentum works better
- Low velocity → sticky capital → mean reversion works better

**Usage:**
- Acts as global strategy switch or weighting factor

---

## 4. Disposition Effect Proxy (Behavioral Layer)
**Goal:** Model retail behavioral pressure zones.

**Core idea:**
Traders tend to:
- take profits too early
- hold losers too long

**Implications:**
- Creates liquidity pockets near breakeven levels
- Predicts resistance/support weakening or strengthening

**Use in system:**
- Identify likely liquidation / exit clusters
- Filter or bias trade direction near crowded levels

---

# Unified Strategy Architecture

## Layer 1: Regime Detection
- Capital velocity → selects market mode:
  - Momentum regime
  - Mean reversion regime

---

## Layer 2: Signal Generation
- Cross-market spreads (venue inefficiency)
- Pair spreads (relative mispricing)
- Network deviations (cluster anomalies)

---

## Layer 3: Behavioral Adjustment
- Disposition proxy zones
- Liquidity traps / crowding effects

---

## Layer 4: Execution Logic
- Only trade when:
  - spread is statistically significant
  - regime supports strategy
  - liquidity conditions are valid

---

# Implementation Skeleton

```python
def compute_regime(volume, open_interest):
    velocity = volume / open_interest
    return "momentum" if velocity > THRESHOLD else "mean_reversion"


def cross_market_signal(a, b):
    spread = a - b
    z = (spread - spread.mean()) / spread.std()
    return z


def pair_signal(asset_x, asset_y, beta):
    spread = asset_x - beta * asset_y
    z = (spread - spread.mean()) / spread.std()
    return z


def final_signal(regime, signals):
    if regime == "momentum":
        return weighted_momentum(signals)
    else:
        return weighted_mean_reversion(signals)