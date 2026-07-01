# Market Regime Tooling Breadcrumbs

Quick reference for the regime-detection tools added during SOL/Hyperliquid
strategy research.

Start here for the bigger picture:
`docs/strategy_research_mental_model.md`.

## Files

- `hummingbot/strategy_v2/utils/market_regime.py`
  - Generic market-regime detector.
  - Turns candles plus optional context into labels like `uptrend`,
    `range_chop`, `breakdown`, `high_volatility_danger`, and `no_trade`.
  - Also returns side gates, risk multipliers, confidence, modifiers, and raw
    features for strategy controllers to consume.

- `scripts/regime_configs/sol_1h.yml`
  - SOL 1h threshold preset.
  - Keeps market-specific detector tuning out of the generic detector code.

- `hummingbot/strategy_v2/utils/market_regime_context.py`
  - Converts raw context inputs like funding, spread/depth, crowding, and
    liquidations into the detector's `MarketContext`.
  - Includes SOL 1h defaults via `MarketContextBuilder.sol_1h()`.

- `hummingbot/strategy_v2/utils/market_signal_features.py`
  - Reusable feature enrichment for strategy research across markets.
  - Adds EMA bias, rolling/session VWAP distance, ROC/RSI, volume expansion,
    taker-buy imbalance/CVD proxy, funding/OI/premium trends, spread/depth risk,
    and failed-breakout trap flags.

- `hummingbot/strategy_v2/utils/market_signals.py`
  - Reusable signal interpretation layer kept separate from the regime
    classifier.
  - Turns enriched feature rows into named reports like strong continuation,
    squeeze risk, weak breakout traps, and risk-off.

- `scripts/collect_hyperliquid_context.py`
  - Forward-collects public Hyperliquid SOL context every minute.
  - Writes `data/context/hyperliquid_SOL_context.csv`.
  - Fields include funding, premium, OI, OI change, mark/oracle/mid, spread, and
    bid/ask/depth USD.

- `scripts/backfill_hyperliquid_s3_context.py`
  - Backfills Hyperliquid S3 `asset_ctxs` history into the same context schema.
  - Cached archives live under `data/s3/hyperliquid/asset_ctxs/`.
  - SOL output: `data/context/hyperliquid_SOL_s3_context.csv`.
  - S3 is requester-pays and published monthly; latest checked archive spans
    `2023-05-20` through `2026-06-01`.

- `scripts/merge_hyperliquid_context.py`
  - Merges S3 context plus live collector context into one deduped CSV.
  - SOL output: `data/context/hyperliquid_SOL_merged_context.csv`.
  - Exact timestamp overlaps prefer live rows by default.

- `scripts/refresh_hl_sol_context.sh`
  - Optional catch-up/recovery helper.
  - Finds the latest published S3 archive, refreshes S3 context, then rebuilds
    the merged context CSV.

- `scripts/install_hl_sol_context_service.sh`
  - Installs the macOS LaunchAgent for the SOL context collector.
  - Service label: `com.hyperion.hummingbot.hl-sol-context`.
  - Logs: `logs/hyperliquid_sol_context.out.log` and
    `logs/hyperliquid_sol_context.err.log`.

- `scripts/install_hl_sol_context_refresh_service.sh`
  - Installs an optional monthly macOS LaunchAgent for S3 catch-up plus merge.
  - Service label: `com.hyperion.hummingbot.hl-sol-context-refresh`.
  - Runs on day 3 monthly at 06:15 local time.

- `scripts/uninstall_hl_sol_context_service.sh`
  - Stops and removes the LaunchAgent.

- `scripts/uninstall_hl_sol_context_refresh_service.sh`
  - Stops and removes the monthly refresh LaunchAgent.

- `scripts/backfill_market_regimes.py`
  - Fetches OHLCV candles, caches raw candles under `data/candles/`, labels each
    row with the detector, and writes labeled CSVs under `data/regimes/`.
  - Optional context flags: `--context-csv`, `--context-builder sol_1h`,
    `--context-max-staleness-seconds`.
  - Useful for creating historical regime datasets before strategy work.

- `scripts/backfill_market_candles.py`
  - Fetches and caches raw OHLCV candles without regime labeling.
  - Use this for lower-timeframe execution datasets like SOL 1m/5m before
    strategy mining.

- `scripts/backfill_hyperliquid_s3_l2_features.py`
  - Downloads Hyperliquid S3 `market_data/<day>/<hour>/l2Book/<coin>.lz4`.
  - Aggregates snapshots into 1-minute L2 features: best bid/ask, spread,
    top-of-book depth, top-3/5/10 depth, depth bands, imbalance, book-thinning,
    estimated 10k/100k taker slippage, and update count.
  - Keeps raw per-hour `SOL.lz4` files under
    `data/s3/hyperliquid/market_data/l2Book/SOL/`.

- `scripts/backfill_hyperliquid_s3_l2_monthly.py`
  - Resumable monthly wrapper for long S3 L2 backfills.
  - SOL output directory:
    `data/microstructure/hyperliquid_l2_monthly/SOL/`.
  - Manifest:
    `data/microstructure/hyperliquid_l2_monthly/SOL/manifest.csv`.

- `scripts/collect_hyperliquid_trades.py`
  - Forward-collects public Hyperliquid trades from the websocket.
  - Aggregates true aggressive buy/sell flow, VWAP, net volume, and CVD into
    1-minute rows.
  - SOL output: `data/microstructure/hyperliquid_SOL_trades_1m.csv`.

- `scripts/build_joined_research_table.py`
  - Builds the mining table for strategy research.
  - Joins 5m/1m candles with lagged 1h regimes, latest Hyperliquid context,
    optional L2 liquidity features, optional trade-flow features, reusable
    signal features/signals, and forward outcome columns.
  - Supports `--start` and `--end` to build bounded lower-timeframe research
    tables before scaling up.
  - Default SOL output: `data/research/sol_5m_joined_research.csv`.

- `scripts/analyze_joined_research_table.py`
  - Ranks side-aware signal/regime slices from the joined research table.
  - Uses simulated stop/take outcomes, configurable leverage, and an assumed
    per-side fee rate to estimate margin-return expectancy.
  - Default outputs:
    `data/research/analysis/joined_5m_signal_outcomes.csv` and
    `data/research/analysis/joined_5m_signal_outcomes_top.csv`.

- `scripts/simulate_research_candidates.py`
  - Replays ranked slices chronologically with one open trade per candidate.
  - Supports taker and maker-entry/taker-exit assumptions, configurable fees,
    fixed/dynamic slippage, notional size, passive fill checks, and calendar
    bps reporting.

- `scripts/enrich_market_signal_features.py`
  - Enriches any candle/regime CSV with reusable signal-discovery columns.
  - Optional `--context-csv` merges raw Hyperliquid context as-of so derivatives
    fields like OI, premium, spread, and depth are available when the data exists.
  - SOL output example:
    `data/regimes/binance_perpetual_SOL-USDT_1h_sol_1h_5y_hl_context_features.csv`.

- `scripts/label_market_signals.py`
  - Labels enriched feature CSVs with `market_signals`, long/short signal scores,
    risk-off flags, and signal reasons.
  - Keeps signal rules reusable across markets and out of the regime classifier.

- `scripts/analyze_market_regimes.py`
  - Audits labeled regime CSVs against forward returns and adverse excursion.
  - Writes label, modifier, and long-vs-short outcome summaries under
    `data/regimes/analysis/`.

- `scripts/sweep_market_regime_config.py`
  - Fast threshold sweep using feature columns from a labeled regime CSV.
  - Ranks SOL map candidates before doing a full backfill.
  - Writes sweep results under `data/regimes/analysis/`.

- `test/hummingbot/strategy_v2/utils/test_market_regime.py`
  - Unit coverage for detector behavior, modifiers, and config-model conversion.

- `test/hummingbot/strategy_v2/utils/test_market_regime_context.py`
  - Unit coverage for SOL context input normalization.

- `test/hummingbot/strategy_v2/utils/test_market_signal_features.py`
  - Unit coverage for reusable trend, VWAP, momentum, volume, derivatives, risk,
    and trap-detection feature enrichment.

- `test/hummingbot/strategy_v2/utils/test_market_signals.py`
  - Unit coverage for named continuation, squeeze, trap, and risk-off signals.

## Flow

1. Cache/fetch candles with `scripts/backfill_market_regimes.py`.
2. Build optional market context with `MarketContextBuilder.sol_1h()`.
3. Label candles with a generic detector plus a market preset.
4. Enrich labels with reusable signal features.
5. Label named signals from the enriched features.
6. Analyze labels with `scripts/analyze_market_regimes.py`.
7. Use regimes plus signals to design a Strategy V2 controller policy.

## Current Data Map

- Storage: repo `data/` is a symlink to external SSD path
  `/Volumes/Extreme Pro/hummingbot-market-data/data`.
- The external SSD was erased before migration and now holds the Hummingbot
  market-data cache. Keep large candles/context/S3/microstructure data there.
- Price proxy: Binance perpetual `SOL-USDT` 1h candles, `2021-07-01` through
  `2026-06-30`.
- Execution candles: Binance perpetual `SOL-USDT` 1m and 5m candles,
  `2021-07-01` through `2026-07-01`, gap-free in the latest audit.
- Hyperliquid S3 context: SOL `asset_ctxs`, `2023-05-20` through `2026-06-01`.
- Hyperliquid L2 features: latest published sample month,
  `2026-05-01` through `2026-06-01`, stored at
  `data/microstructure/hyperliquid_SOL_l2_1m_20260501_20260601.csv`.
  S3 `l2Book` has natural missing minutes; do not forward-fill blindly.
- Hyperliquid execution L2 features: richer May-June sample stored at
  `data/microstructure/hyperliquid_SOL_l2_execution_1m_20260501_20260601.csv`.
- Hyperliquid live execution L2 features:
  `data/microstructure/hyperliquid_SOL_l2_execution_live_1m.csv`.
  This uses the same 50-column schema as the S3 L2 backfill: spread, top-level
  depth, bps-band depth, imbalance, book-thinning, and 10k/100k slippage.
- Hyperliquid trade-flow collector output:
  `data/microstructure/hyperliquid_SOL_trades_1m.csv`.
  Historical S3 trade files were not found under the checked `market_data`
  archive path; this starts filling true CVD only once the collector is running.
- Forward collector: fills live Hyperliquid SOL context from its start time
  onward into `data/context/hyperliquid_SOL_context.csv`.
- Canonical context input: `data/context/hyperliquid_SOL_merged_context.csv`.
- Normal maintenance path: keep the forward collector running, then rerun the
  merge before research/backtests.
- Labeled dataset: 5y Binance proxy candles plus `context_available` flags;
  26,382 of 43,800 hourly rows currently have Hyperliquid context.
- Enriched SOL research dataset:
  `data/regimes/binance_perpetual_SOL-USDT_1h_sol_1h_5y_hl_context_features.csv`.
- Signal-labeled SOL research dataset:
  `data/regimes/binance_perpetual_SOL-USDT_1h_sol_1h_5y_hl_context_signals.csv`.
- Joined SOL 5m research dataset:
  `data/research/sol_5m_joined_research.csv`.
  Latest build has 525,889 rows, 214 columns, gap-free 5m price rows,
  60.13% Hyperliquid context coverage, and 1.75% L2 feature coverage.
- Joined SOL 1m research dataset for first snipe pass:
  `data/research/sol_1m_joined_research_2025_2026.csv`.
  Latest build has 786,241 rows from `2025-01-01` through `2026-07-01`,
  94.64% Hyperliquid context coverage, and 5.85% L2 feature coverage.

## Running Collector

- Services/data dashboard:
  `conda run -n hummingbot python scripts/hummingbot_services_dashboard.py`
- Dashboard output:
  `reports/hummingbot_services_dashboard.html`
- Use this before adding or stopping collectors; it shows known LaunchAgents,
  purpose/necessity, logs, output files, manifest progress, and partial outputs.

- Start/reload: `scripts/install_hl_sol_context_service.sh`
- Stop/remove: `scripts/uninstall_hl_sol_context_service.sh`
- Check service: `launchctl print gui/$(id -u)/com.hyperion.hummingbot.hl-sol-context`
- Watch output: `tail -f logs/hyperliquid_sol_context.out.log`
- Context CSV: `data/context/hyperliquid_SOL_context.csv`
- The collector does not backfill missed history. It protects the dataset from
  its start time forward.
- Before research/backtests, rerun `scripts/merge_hyperliquid_context.py` so the
  merged context CSV includes the latest collector rows.

## Running SOL Rich L2 Forward Collector

- Start/reload: `scripts/install_hl_sol_l2_forward_service.sh`
- Stop/remove: `scripts/uninstall_hl_sol_l2_forward_service.sh`
- Check service:
  `launchctl print gui/$(id -u)/com.hyperion.hummingbot.hl-sol-l2-forward`
- Watch output: `tail -f logs/hyperliquid_sol_l2_forward.out.log`
- Watch errors: `tail -f logs/hyperliquid_sol_l2_forward.err.log`
- Live L2 CSV:
  `data/microstructure/hyperliquid_SOL_l2_execution_live_1m.csv`
- Manual smoke test:
  `conda run -n hummingbot python scripts/collect_hyperliquid_l2_features.py --coin SOL --once --output /tmp/hyperliquid_SOL_l2_live_smoke.csv`
- This collector protects current/future rich L2 coverage. It does not backfill
  missed history; use the S3 L2 monthly backfill for recoverable `l2Book`
  archive history.

## Running SOL L2 Backfill

- Current one-shot LaunchAgent label:
  `com.hyperion.hummingbot.hl-sol-l2-backfill`.
- Current target: SOL L2 from `2023-04-15` through `2026-06-01`.
- Check status:
  `launchctl print gui/$(id -u)/com.hyperion.hummingbot.hl-sol-l2-backfill`
- Watch output:
  `tail -f logs/hyperliquid_sol_l2_backfill.out.log`
- Watch errors:
  `tail -f logs/hyperliquid_sol_l2_backfill.err.log`
- Stop:
  `launchctl bootout gui/$(id -u) /Users/hyperionpett/code/hummingbot/logs/com.hyperion.hummingbot.hl-sol-l2-backfill.plist`
- Resume manually:
  `conda run -n hummingbot python scripts/backfill_hyperliquid_s3_l2_monthly.py --coin SOL --start 2023-04-15 --end 2026-06-01`

## Optional S3 Catch-Up

- Not required for normal forward collection if the collector stays running.
- Use this when S3 publishes a missing archive month, or if the collector was
  down and you want to recover history from the archive.
- Optional install: `scripts/install_hl_sol_context_refresh_service.sh`
- Stop/remove: `scripts/uninstall_hl_sol_context_refresh_service.sh`
- Manual run: `scripts/refresh_hl_sol_context.sh`
- Watch output: `tail -f logs/hyperliquid_sol_context_refresh.out.log`
- Merged CSV: `data/context/hyperliquid_SOL_merged_context.csv`
- Use the merged CSV as `--context-csv` for regime backfills.

## S3 Backfill

- Backfill context: `conda run -n hummingbot python scripts/backfill_hyperliquid_s3_context.py --coin SOL --start 2023-05-20 --end 2026-06-01`
- Label with context: add `--context-csv data/context/hyperliquid_SOL_merged_context.csv --context-builder sol_1h` to `scripts/backfill_market_regimes.py`
- Enrich with signal features: `conda run -n hummingbot python scripts/enrich_market_signal_features.py --input data/regimes/binance_perpetual_SOL-USDT_1h_sol_1h_5y_hl_context.csv --context-csv data/context/hyperliquid_SOL_merged_context.csv --output data/regimes/binance_perpetual_SOL-USDT_1h_sol_1h_5y_hl_context_features.csv`
- Label named signals: `conda run -n hummingbot python scripts/label_market_signals.py --input data/regimes/binance_perpetual_SOL-USDT_1h_sol_1h_5y_hl_context_features.csv --output data/regimes/binance_perpetual_SOL-USDT_1h_sol_1h_5y_hl_context_signals.csv`
- Long proxy output: `data/regimes/binance_perpetual_SOL-USDT_1h_sol_1h_5y_hl_context.csv`
- Current SOL S3 context covers `2023-05-20` through `2026-06-01`.
- L2 feature sample: `conda run -n hummingbot python scripts/backfill_hyperliquid_s3_l2_features.py --coin SOL --start 2026-05-01 --end 2026-06-01 --output data/microstructure/hyperliquid_SOL_l2_1m_20260501_20260601.csv`
- Rich L2 execution sample: `conda run -n hummingbot python scripts/backfill_hyperliquid_s3_l2_features.py --coin SOL --start 2026-05-01 --end 2026-06-01 --output data/microstructure/hyperliquid_SOL_l2_execution_1m_20260501_20260601.csv`
- Live rich L2 collector: `conda run -n hummingbot python scripts/collect_hyperliquid_l2_features.py --coin SOL`
- Live trade-flow collector: `conda run -n hummingbot python scripts/collect_hyperliquid_trades.py --coin SOL`
- Raw candle cache: `conda run -n hummingbot python scripts/backfill_market_candles.py --connector binance_perpetual --trading-pair SOL-USDT --interval 1m --start 2021-07-01 --end 2026-07-01 --chunk-records 1000`
- Joined 5m research table: `conda run -n hummingbot python scripts/build_joined_research_table.py`
- Analyze joined 5m outcomes: `conda run -n hummingbot python scripts/analyze_joined_research_table.py`
- Joined bounded 1m research table example: `conda run -n hummingbot python scripts/build_joined_research_table.py --candles-csv data/candles/binance_perpetual_SOL-USDT_1m.csv --output data/research/sol_1m_joined_research_2025_2026.csv --start 2025-01-01 --end 2026-07-01 --horizons 15,30,60 --stop-take-pairs 0.0025:0.0075,0.005:0.015,0.01:0.03 --rolling-vwap-window 120 --volume-window 120 --funding-trend-window 60 --oi-change-window 60 --premium-trend-window 60 --trap-lookback 30`
- Joined L2-rich execution table example: `conda run -n hummingbot python scripts/build_joined_research_table.py --candles-csv data/candles/binance_perpetual_SOL-USDT_1m.csv --l2-csv data/microstructure/hyperliquid_SOL_l2_execution_1m_20260501_20260601.csv --output data/research/sol_1m_execution_research_20260501_20260602.csv --start 2026-05-01 --end 2026-06-02 --horizons 15,30,60 --stop-take-pairs 0.0025:0.0075,0.005:0.015,0.01:0.03 --rolling-vwap-window 120 --volume-window 120 --funding-trend-window 60 --oi-change-window 60 --premium-trend-window 60 --trap-lookback 30`
- Current cache has 1,109 archive days and 1,573,887 SOL rows.
- The archive is not perfectly minute-uniform: 858 days have exactly 1,440 rows,
  199 days are below, and 52 days are above. Worst known low day is `2026-05-30`
  with 418 rows.
- The 5y Binance SOL proxy regime file has 26,382 context-matched hourly rows,
  from `2023-05-20 03:00 UTC` through `2026-06-02 00:00 UTC`.
- Latest SOL map tuning made labels stricter, moved more rows to no-trade/high-vol
  danger, and lowered funding-extreme sensitivity so HL context can reduce risk.

## Gaps

- Pre-`2023-05-20`: no Hyperliquid S3 context found; use price-only regimes.
- `2026-06-02` through collector start: only recoverable if the June S3 archive
  fills it later.
- Current and future data: context and rich L2 forward collectors are enough as
  long as they stay up; trade-flow still needs its own collector if CVD matters.
- Still needed for strategy work: wire regime outputs into an actual controller
  policy and backtest PnL with collateral, leverage, funding, fees, slippage,
  and liquidation assumptions.

## Key Reminder

The detector is not the strategy. It gives the strategy a market-structure map.
Signal features and signal reports are separate evidence layers. The controller
still decides entries, exits, sizing, leverage, cooldowns, and when to pause.
