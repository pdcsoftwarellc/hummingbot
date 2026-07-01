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
- Liquidity context: Hyperliquid S3 L2 1m features where backfilled, plus live
  rich L2 rows once `collect_hyperliquid_l2_features.py` is running.

## Data We Have

- Binance SOL-USDT 1m: `2021-07-01` through `2026-07-01`, gap-free.
- Binance SOL-USDT 5m: `2021-07-01` through `2026-07-01`, gap-free.
- Binance SOL-USDT 1h: `2021-07-01` through `2026-06-30`.
- Hyperliquid SOL asset context: starts `2023-05-20`; S3 currently through
  `2026-06-01`, merged with live collector where available.
- Hyperliquid SOL L2 feature sample: `2026-05-01` through `2026-06-01`.
- Hyperliquid live rich L2 collector:
  `data/microstructure/hyperliquid_SOL_l2_execution_live_1m.csv`, same schema
  as S3 L2 feature backfills.

## Data We Still Want

- True historical Hyperliquid trades for real CVD and aggressive flow. The
  checked requester-pays S3 market-data path exposes `l2Book`; trade flow is
  forward-collected from the live websocket instead.
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

Current default build:

- Script: `scripts/build_joined_research_table.py`
- Output: `data/research/sol_5m_joined_research.csv`
- Important assumption: the 1h regime is joined with a one-hour availability
  lag so the table does not use the current unfinished 1h candle as known data.

Current first-pass scan:

- Script: `scripts/analyze_joined_research_table.py`
- Output: `data/research/analysis/joined_5m_signal_outcomes_top.csv`
- Early read: broad 5m signal slices are weak after leverage and fee assumptions;
  use the scan to find hypotheses, not to approve a live strategy.

Current 1m research table:

- Script: `scripts/build_joined_research_table.py`
- Useful flags: `--candles-csv`, `--start`, `--end`, `--horizons`,
  `--stop-take-pairs`.
- Output built for first snipe pass:
  `data/research/sol_1m_joined_research_2025_2026.csv`.
- Early best lead: short `strong_short_continuation` in
  `high_volatility_danger`, `mixed` VWAP alignment, `high_expansion` volume,
  `0.25%` stop / `0.75%` take over `60` one-minute bars. It replays around
  `5` bps/day at `5x`, below target and not robust enough yet.

Execution-aware pass:

- Richer L2 script: `scripts/backfill_hyperliquid_s3_l2_features.py`
  now adds depth bands, top-of-book depth, book-thinning, and estimated 10k/100k
  taker slippage.
- Live rich L2 script: `scripts/collect_hyperliquid_l2_features.py`
  forward-fills that same spread/depth/imbalance/slippage schema from live
  Hyperliquid `l2Book`.
- Live trade flow: `scripts/collect_hyperliquid_trades.py` writes 1-minute
  true CVD/aggressive-flow features from the Hyperliquid websocket.
- Joined table hook: `scripts/build_joined_research_table.py` joins optional
  `trade_*` flow columns when the collector CSV exists.
- Execution replay: `scripts/simulate_research_candidates.py` can compare taker
  vs maker-entry/taker-exit assumptions with configurable fees, slippage,
  notional size, and passive fill checks.
- First L2-rich sample:
  `data/research/sol_1m_execution_research_20260501_20260602.csv`.
  It has 46,080 rows, 99.88% L2 coverage, and no historical trade-flow coverage.
- Early read: local May-June short continuation can reach roughly `13` bps/day
  taker or `17` bps/day maker-entry at `5x`, but this is one month only and
  still below the 25 bps/day target.
