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

## What We Built

This is the short reload list for the additional research parts added around
the SOL/Hyperliquid work:

- Higher-timeframe regime map:
  `hummingbot/strategy_v2/utils/market_regime.py` plus
  `scripts/regime_configs/sol_1h.yml`.
- Market context layer:
  `hummingbot/strategy_v2/utils/market_regime_context.py`,
  `scripts/collect_hyperliquid_context.py`,
  `scripts/backfill_hyperliquid_s3_context.py`, and
  `scripts/merge_hyperliquid_context.py`.
- Signal feature and interpretation layer:
  `hummingbot/strategy_v2/utils/market_signal_features.py`,
  `hummingbot/strategy_v2/utils/market_signals.py`,
  `scripts/enrich_market_signal_features.py`, and
  `scripts/label_market_signals.py`.
- Joined research table builder:
  `scripts/build_joined_research_table.py`, which joins lower-timeframe candles,
  lagged 1h regimes, Hyperliquid context, optional L2, optional trade flow,
  signal features, signal labels, and forward stop/take outcomes.
- Slice/ranking tools:
  `scripts/analyze_joined_research_table.py` for broad signal slice scans and
  `scripts/mine_research_edges.py` for stricter train/test edge mining.
- Execution replay:
  `scripts/simulate_research_candidates.py`, which replays ranked candidates
  chronologically with one open trade per candidate and taker or maker-entry
  execution assumptions.
- SOL swing hypothesis backtester:
  `scripts/backtest_sol_swing_hypothesis.py`, which replays side-specific SOL
  swing books from the canonical 5m joined table. It always reports YTD,
  1 Year, and Full windows when the data supports them, prints `Backtest
  Timeframe`, marks truncated current trades as `data_end`, and keeps the
  promoted baseline separate from experimental books.
- L2 microstructure pipeline:
  `scripts/backfill_hyperliquid_s3_l2_features.py`,
  `scripts/backfill_hyperliquid_s3_l2_monthly.py`, and
  `scripts/collect_hyperliquid_l2_features.py`.
- Trade-flow/CVD pipeline:
  `scripts/collect_hyperliquid_trades.py`, forward-only from the live websocket
  because historical S3 trade files were not found in the checked archive path.
- Service visibility:
  `scripts/hummingbot_services_dashboard.py`, which writes
  `reports/hummingbot_services_dashboard.html` with LaunchAgent status, logs,
  output files, manifest progress, and partial outputs.
- Shared research helpers:
  `scripts/research_utils.py` holds common signal sets, outcome parsing,
  timestamp normalization, bool parsing, and list parsing for the research CLIs.

## Data Reality

- The long SOL L2 history backfill finished cleanly.
- Live collectors protect current/future coverage, but they do not fill old
  gaps. S3 backfills close historical gaps only after Hyperliquid publishes the
  archive files.
- Trade-flow/CVD is forward-only for now, so older joined tables may have no
  `trade_*` coverage even when context and L2 exist.
- Important current gap: Hyperliquid has not published June 30, 2026 S3
  `asset_ctxs` or `l2Book` yet.
  - Context gap: `2026-06-30 00:00:00` -> `2026-06-30 14:21:34 UTC`.
  - L2 gap: `2026-06-30 00:00:00` -> `2026-07-01 21:15:00 UTC`.

## Data We Have

- Storage: repo `data/` is symlinked to
  `/Volumes/Extreme Pro/hummingbot-market-data/data`.
- Binance SOL-USDT 1m: `2021-07-01 00:00` through
  `2026-07-02 16:05 UTC`, gap-free.
- Binance SOL-USDT 5m: `2021-07-01 00:00` through
  `2026-07-02 16:05 UTC`, gap-free.
- Binance SOL-USDT 1h: `2021-07-01 00:00` through
  `2026-07-02 16:00 UTC`, gap-free.
- Hyperliquid SOL-USD native 1h: `2025-12-04 07:00` through
  `2026-07-02 16:00 UTC`.
- Hyperliquid SOL asset context:
  - S3: `2023-05-20 02:50:04` through `2026-06-29 23:59 UTC`.
  - Live: starts `2026-06-30 14:21:34 UTC`.
  - Merged canonical file:
    `data/context/hyperliquid_SOL_merged_context.csv`.
- Hyperliquid SOL L2 execution features:
  - S3 monthly archive: `2023-04-15` through `2026-06-29`, plus a
    `2026-06-30` chunk with `0` rows because S3 returned 404 for all hours.
  - Live: starts `2026-07-01 21:15 UTC`.
  - Canonical merged file:
    `data/microstructure/hyperliquid_SOL_l2_execution_1m_merged.csv`.
- Hyperliquid live rich L2 collector:
  `data/microstructure/hyperliquid_SOL_l2_execution_live_1m.csv`, same schema
  as S3 L2 feature backfills.
- Hyperliquid L2 monthly manifest:
  `data/microstructure/hyperliquid_l2_monthly/SOL/manifest.csv`.
- Latest audited counts on `2026-07-02`:
  - S3 context rows: `1,614,207`.
  - Merged context rows: `1,617,137`.
  - S3 L2 manifest rows: `1,661,552`.
  - Merged L2 rows: `1,649,762`.

## Data We Still Want

- True historical Hyperliquid trades for real CVD and aggressive flow. The
  checked requester-pays S3 market-data path exposes `l2Book`; trade flow is
  forward-collected from the live websocket instead.
- Hyperliquid S3 publication for June 30, 2026 `asset_ctxs` and `l2Book`, to
  close the current archive gap.
- Better liquidation-cluster data or heatmap data.
- More native Hyperliquid candle history if we can avoid REST rate limits.

## Research Rule

Do not optimize a strategy only on raw indicators. Test setups as:

`1h regime + modifier posture + 1m/5m signal + derivatives context + liquidity filter`

Then evaluate PnL with collateral, leverage, fees, funding, slippage, and
liquidation assumptions.

Keep deployable baselines and experiments separate in reports. For the SOL
swing work, `combined` is the current baseline, while names such as
`combined_with_breakout` are experimental sidecars until their full-history
return, drawdown, and Sharpe justify promotion.

Leverage should be confidence-scaled in research and deployment candidates.
Treat the configured leverage as a cap, not a default entitlement: low-confidence
signals should either be skipped or run near minimum leverage, and full leverage
should require an explicit confidence threshold plus clean liquidity/funding
context.

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
- Use canonical L2 explicitly:
  `--l2-csv data/microstructure/hyperliquid_SOL_l2_execution_1m_merged.csv`.
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

## Services To Remember

- Live context collector:
  `com.hyperion.hummingbot.hl-sol-context`.
- Live L2 collector:
  `com.hyperion.hummingbot.hl-sol-l2-forward`.
- Service dashboard:
  `conda run -n hummingbot python scripts/hummingbot_services_dashboard.py`
  then open `reports/hummingbot_services_dashboard.html`.
- Historical L2 backfill service:
  `com.hyperion.hummingbot.hl-sol-l2-backfill` finished and should be idle.
