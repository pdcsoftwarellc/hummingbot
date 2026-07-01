# Strategy Research Mental Model

This is the short version to reload context before changing data, regimes,
signals, or strategies.

## Core Split

1. **Regime is the map**
   - Slow/higher-timeframe market structure.
   - Says what kind of market SOL is in: range, uptrend, downtrend, breakout,
     breakdown, high-vol danger, no-trade.
   - Should stay generic and reusable across markets.

2. **Modifiers adjust posture**
   - Risk overlays on top of the regime.
   - Examples: pullback, failed breakout, trend exhaustion, funding extreme,
     liquidity thin, post-liquidation flush.
   - They change sizing, side permission, cooldowns, or risk multiplier.

3. **Signals are trade evidence**
   - More actionable than regimes.
   - Built from trend, VWAP, momentum, volume, derivatives, liquidity, and trap
     features.
   - Examples: strong continuation, squeeze risk, weak breakout trap, risk-off.

4. **Strategy owns the decision**
   - The controller decides entry, exit, leverage, sizing, stop, take profit,
     cooldown, double-down rules, and pause logic.
   - Regimes and signals should inform the controller, not become the controller.

## Current SOL Stack

- Long-range structure: Binance SOL-USDT 1h proxy candles.
- Entry timing: Binance SOL-USDT 5m and 1m candles.
- Perp context: Hyperliquid SOL funding, premium, open interest, mark/oracle,
  spread/depth where available.
- Liquidity context: Hyperliquid S3 L2 1m features where backfilled.

## Data We Have

- Binance SOL-USDT 1m: `2021-07-01` through `2026-07-01`, gap-free.
- Binance SOL-USDT 5m: `2021-07-01` through `2026-07-01`, gap-free.
- Binance SOL-USDT 1h: `2021-07-01` through `2026-06-30`.
- Hyperliquid SOL asset context: starts `2023-05-20`; S3 currently through
  `2026-06-01`, merged with live collector where available.
- Hyperliquid SOL L2 feature sample: `2026-05-01` through `2026-06-01`.

## Data We Still Want

- True historical Hyperliquid trades for real CVD and aggressive flow.
- Longer Hyperliquid L2 feature history if L2 filters prove useful.
- Better liquidation-cluster data or heatmap data.
- Native Hyperliquid candles at scale if we can avoid REST rate limits.

## Research Rule

Do not optimize a strategy only on raw indicators. Test setups as:

`1h regime + modifier posture + 1m/5m signal + derivatives context + liquidity filter`

Then evaluate PnL with collateral, leverage, fees, funding, slippage, and
liquidation assumptions.

## Practical Next Dataset

Build a joined research table where each 1m or 5m row has:

- Current 1h regime and modifiers.
- Latest Hyperliquid context.
- Signal features and signal labels.
- L2 liquidity features when available.
- Forward returns, adverse excursion, favorable excursion, and simulated
  stop/take outcomes.
