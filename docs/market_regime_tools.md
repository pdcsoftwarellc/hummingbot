# Market Regime Tooling Breadcrumbs

Quick reference for the regime-detection tools added during SOL/Hyperliquid
strategy research.

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

- `scripts/enrich_market_signal_features.py`
  - Enriches any candle/regime CSV with reusable signal-discovery columns.
  - Optional `--context-csv` merges raw Hyperliquid context as-of so derivatives
    fields like OI, premium, spread, and depth are available when the data exists.
  - SOL output example:
    `data/regimes/binance_perpetual_SOL-USDT_1h_sol_1h_5y_hl_context_features.csv`.

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

## Flow

1. Cache/fetch candles with `scripts/backfill_market_regimes.py`.
2. Build optional market context with `MarketContextBuilder.sol_1h()`.
3. Label candles with a generic detector plus a market preset.
4. Enrich labels with reusable signal features.
5. Analyze labels with `scripts/analyze_market_regimes.py`.
6. Use the findings to design a Strategy V2 controller policy.

## Current Data Map

- Price proxy: Binance perpetual `SOL-USDT` 1h candles, `2021-07-01` through
  `2026-06-30`.
- Hyperliquid S3 context: SOL `asset_ctxs`, `2023-05-20` through `2026-06-01`.
- Forward collector: fills live Hyperliquid SOL context from its start time
  onward into `data/context/hyperliquid_SOL_context.csv`.
- Canonical context input: `data/context/hyperliquid_SOL_merged_context.csv`.
- Normal maintenance path: keep the forward collector running, then rerun the
  merge before research/backtests.
- Labeled dataset: 5y Binance proxy candles plus `context_available` flags;
  26,382 of 43,800 hourly rows currently have Hyperliquid context.
- Enriched SOL research dataset:
  `data/regimes/binance_perpetual_SOL-USDT_1h_sol_1h_5y_hl_context_features.csv`.

## Running Collector

- Start/reload: `scripts/install_hl_sol_context_service.sh`
- Stop/remove: `scripts/uninstall_hl_sol_context_service.sh`
- Check service: `launchctl print gui/$(id -u)/com.hyperion.hummingbot.hl-sol-context`
- Watch output: `tail -f logs/hyperliquid_sol_context.out.log`
- Context CSV: `data/context/hyperliquid_SOL_context.csv`
- The collector does not backfill missed history. It protects the dataset from
  its start time forward.
- Before research/backtests, rerun `scripts/merge_hyperliquid_context.py` so the
  merged context CSV includes the latest collector rows.

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
- Long proxy output: `data/regimes/binance_perpetual_SOL-USDT_1h_sol_1h_5y_hl_context.csv`
- Current SOL S3 context covers `2023-05-20` through `2026-06-01`.
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
- Current and future data: forward collector is enough as long as it stays up.
- Still needed for strategy work: wire regime outputs into an actual controller
  policy and backtest PnL with collateral, leverage, funding, fees, slippage,
  and liquidation assumptions.

## Key Reminder

The detector is not the strategy. It gives the strategy a market-structure map.
The controller still decides entries, exits, sizing, leverage, cooldowns, and
when to pause.
