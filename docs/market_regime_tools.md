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

- `scripts/collect_hyperliquid_context.py`
  - Forward-collects public Hyperliquid SOL context every minute.
  - Writes `data/context/hyperliquid_SOL_context.csv`.
  - Fields include funding, premium, OI, OI change, mark/oracle/mid, spread, and
    bid/ask/depth USD.

- `scripts/backfill_hyperliquid_s3_context.py`
  - Backfills Hyperliquid S3 `asset_ctxs` history into the same context schema.
  - Cached archives live under `data/s3/hyperliquid/asset_ctxs/`.
  - SOL output: `data/context/hyperliquid_SOL_s3_context.csv`.
  - S3 is requester-pays and published monthly; latest checked archive ended
    `2026-06-01`.

- `scripts/install_hl_sol_context_service.sh`
  - Installs the macOS LaunchAgent for the SOL context collector.
  - Service label: `com.hyperion.hummingbot.hl-sol-context`.
  - Logs: `logs/hyperliquid_sol_context.out.log` and
    `logs/hyperliquid_sol_context.err.log`.

- `scripts/uninstall_hl_sol_context_service.sh`
  - Stops and removes the LaunchAgent.

- `scripts/backfill_market_regimes.py`
  - Fetches OHLCV candles, caches raw candles under `data/candles/`, labels each
    row with the detector, and writes labeled CSVs under `data/regimes/`.
  - Optional context flags: `--context-csv`, `--context-builder sol_1h`,
    `--context-max-staleness-seconds`.
  - Useful for creating historical regime datasets before strategy work.

- `scripts/analyze_market_regimes.py`
  - Audits labeled regime CSVs against forward returns and adverse excursion.
  - Writes label, modifier, and long-vs-short outcome summaries under
    `data/regimes/analysis/`.

- `test/hummingbot/strategy_v2/utils/test_market_regime.py`
  - Unit coverage for detector behavior, modifiers, and config-model conversion.

- `test/hummingbot/strategy_v2/utils/test_market_regime_context.py`
  - Unit coverage for SOL context input normalization.

## Flow

1. Cache/fetch candles with `scripts/backfill_market_regimes.py`.
2. Build optional market context with `MarketContextBuilder.sol_1h()`.
3. Label candles with a generic detector plus a market preset.
4. Analyze labels with `scripts/analyze_market_regimes.py`.
5. Use the findings to design a Strategy V2 controller policy.

## Running Collector

- Start/reload: `scripts/install_hl_sol_context_service.sh`
- Stop/remove: `scripts/uninstall_hl_sol_context_service.sh`
- Check service: `launchctl print gui/$(id -u)/com.hyperion.hummingbot.hl-sol-context`
- Watch output: `tail -f logs/hyperliquid_sol_context.out.log`
- Context CSV: `data/context/hyperliquid_SOL_context.csv`

## S3 Backfill

- Backfill context: `conda run -n hummingbot python scripts/backfill_hyperliquid_s3_context.py --coin SOL --start 2025-12-03 --end 2026-06-01`
- Label with context: add `--context-csv data/context/hyperliquid_SOL_s3_context.csv --context-builder sol_1h` to `scripts/backfill_market_regimes.py`
- Long proxy output: `data/regimes/binance_perpetual_SOL-USDT_1h_sol_1h_5y_hl_context.csv`
- Current SOL S3 cache has one partial archive day: `2026-05-30` had 418 rows.

## Key Reminder

The detector is not the strategy. It gives the strategy a market-structure map.
The controller still decides entries, exits, sizing, leverage, cooldowns, and
when to pause.
