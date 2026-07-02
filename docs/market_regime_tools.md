# Market Regime Tooling Breadcrumbs

Quick reference for the regime-detection tools added during SOL/Hyperliquid
strategy research.

For the broader research stack, use `docs/strategy_research_mental_model.md`.

## Scope

This doc is only for the market-regime layer and its immediate support tools:

1. Regime classification from higher-timeframe candles.
2. Optional Hyperliquid context used by the regime classifier.
3. Regime dataset backfills, audits, and threshold sweeps.
4. The companion signal-labeling layer, because it is built directly on top of
   enriched regime/candle rows.

## Core Regime Files

- `hummingbot/strategy_v2/utils/market_regime.py`
  - Generic market-regime detector.
  - Turns candles plus optional context into labels like `uptrend`,
    `range_chop`, `breakdown`, `high_volatility_danger`, and `no_trade`.
  - Also returns side gates, risk multipliers, confidence, modifiers, and raw
    features for strategy controllers to consume.
  - Keeps the pure chart structure separate from the final bot posture:
    `price_regime` is the OHLCV-only map, while `regime_label` remains the
    context/risk-adjusted execution label for backward compatibility. Risk
    overlays are exposed through `risk_state`, `execution_posture`, and
    `blocked_by`.
  - Liquidity scoring distinguishes hard blocks from soft sizing risk. Depth
    below the hard floor or a thin spread can still block, while tight-spread
    depth below the ideal SOL threshold becomes `liquidity_thin` and reduces
    risk rather than forcing `bot_off`.

- `scripts/regime_configs/sol_1h.yml`
  - SOL 1h threshold preset.
  - Keeps market-specific detector tuning out of the generic detector code.

- `hummingbot/strategy_v2/utils/market_execution_policy.py`
  - Shared execution gate for strategies and research scripts.
  - Converts a `MarketRegimeReport` or labeled CSV row into reusable answers:
    hard-blocked or not, allowed side, side risk multiplier, and directional
    signal.
  - Strategy-specific danger rules should be passed as extra hard regimes or
    extra hard modifiers instead of reimplementing liquidity, risk-state, or
    side-gate parsing.

- `hummingbot/strategy_v2/utils/market_regime_context.py`
  - Converts raw context inputs like funding, spread/depth, crowding, and
    liquidations into the detector's `MarketContext`.
  - Includes SOL 1h defaults via `MarketContextBuilder.sol_1h()`.

- `scripts/backfill_market_regimes.py`
  - Fetches OHLCV candles, caches raw candles under `data/candles/`, labels each
    row with the detector, and writes labeled CSVs under `data/regimes/`.
  - Optional context flags: `--context-csv`, `--context-builder sol_1h`,
    `--context-max-staleness-seconds`.

- `scripts/analyze_market_regimes.py`
  - Audits labeled regime CSVs against forward returns and adverse excursion.
  - Writes label, modifier, and long-vs-short outcome summaries under
    `data/regimes/analysis/`.

- `scripts/sweep_market_regime_config.py`
  - Fast threshold sweep using feature columns from a labeled regime CSV.
  - Ranks SOL map candidates before doing a full backfill.
  - Writes sweep results under `data/regimes/analysis/`.

## Context Support

- `scripts/collect_hyperliquid_context.py`
  - Forward-collects public Hyperliquid SOL context every minute.
  - Writes `data/context/hyperliquid_SOL_context.csv`.
  - Fields include funding, premium, OI, OI change, mark/oracle/mid, spread, and
    bid/ask/depth USD.

- `scripts/backfill_hyperliquid_s3_context.py`
  - Backfills Hyperliquid S3 `asset_ctxs` history into the same context schema.
  - Cached archives live under `data/s3/hyperliquid/asset_ctxs/`.
  - SOL output: `data/context/hyperliquid_SOL_s3_context.csv`.
  - S3 is requester-pays and published monthly.

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
  - Stops and removes the context collector LaunchAgent.

- `scripts/uninstall_hl_sol_context_refresh_service.sh`
  - Stops and removes the monthly refresh LaunchAgent.

## Signal Companion Layer

- `hummingbot/strategy_v2/utils/market_signal_features.py`
  - Reusable feature enrichment for candles or labeled regime rows.
  - Adds EMA bias, rolling/session VWAP distance, ROC/RSI, volume expansion,
    taker-buy imbalance/CVD proxy, funding/OI/premium trends, spread/depth risk,
    and failed-breakout trap flags.

- `hummingbot/strategy_v2/utils/market_signals.py`
  - Reusable signal interpretation layer kept separate from the regime
    classifier.
  - Turns enriched feature rows into named reports like strong continuation,
    squeeze risk, weak breakout traps, and risk-off.

- `scripts/enrich_market_signal_features.py`
  - Enriches any candle/regime CSV with reusable signal-discovery columns.
  - Optional `--context-csv` merges raw Hyperliquid context as-of so derivatives
    fields like OI, premium, spread, and depth are available when the data exists.

- `scripts/label_market_signals.py`
  - Labels enriched feature CSVs with `market_signals`, long/short signal scores,
    risk-off flags, and signal reasons.
  - Keeps signal rules reusable across markets and out of the regime classifier.

## Basic Flow

1. Cache/fetch candles with `scripts/backfill_market_regimes.py`.
2. Build optional market context with `MarketContextBuilder.sol_1h()`.
3. Label candles with the generic detector plus `scripts/regime_configs/sol_1h.yml`.
4. Optionally enrich labeled rows with reusable signal features.
5. Optionally label named signals from the enriched features.
6. Audit regime labels with `scripts/analyze_market_regimes.py`.
7. Sweep threshold candidates with `scripts/sweep_market_regime_config.py`.

## Current SOL Regime Data

- Storage: repo `data/` is a symlink to external SSD path
  `/Volumes/Extreme Pro/hummingbot-market-data/data`.
- Price proxy: Binance perpetual `SOL-USDT` 1h candles, `2021-07-01` through
  `2026-06-30`.
- Hyperliquid S3 context: SOL `asset_ctxs`, `2023-05-20` through `2026-06-01`.
- Forward context collector: fills live Hyperliquid SOL context from its start
  time onward into `data/context/hyperliquid_SOL_context.csv`.
- Canonical context input: `data/context/hyperliquid_SOL_merged_context.csv`.
- Labeled regime dataset:
  `data/regimes/binance_perpetual_SOL-USDT_1h_sol_1h_5y_hl_context.csv`.
- Enriched regime dataset:
  `data/regimes/binance_perpetual_SOL-USDT_1h_sol_1h_5y_hl_context_features.csv`.
- Signal-labeled regime dataset:
  `data/regimes/binance_perpetual_SOL-USDT_1h_sol_1h_5y_hl_context_signals.csv`.

## Running Context Collector

- Start/reload: `scripts/install_hl_sol_context_service.sh`
- Stop/remove: `scripts/uninstall_hl_sol_context_service.sh`
- Check service: `launchctl print gui/$(id -u)/com.hyperion.hummingbot.hl-sol-context`
- Watch output: `tail -f logs/hyperliquid_sol_context.out.log`
- Watch errors: `tail -f logs/hyperliquid_sol_context.err.log`
- Context CSV: `data/context/hyperliquid_SOL_context.csv`
- The collector does not backfill missed history. It protects the dataset from
  its start time forward.
- Before regime research, rerun `scripts/merge_hyperliquid_context.py` so the
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

## Common Commands

- Backfill context:
  `conda run -n hummingbot python scripts/backfill_hyperliquid_s3_context.py --coin SOL --start 2023-05-20 --end 2026-06-01`
- Label regimes with context:
  add `--context-csv data/context/hyperliquid_SOL_merged_context.csv --context-builder sol_1h` to `scripts/backfill_market_regimes.py`
- Enrich with signal features:
  `conda run -n hummingbot python scripts/enrich_market_signal_features.py --input data/regimes/binance_perpetual_SOL-USDT_1h_sol_1h_5y_hl_context.csv --context-csv data/context/hyperliquid_SOL_merged_context.csv --output data/regimes/binance_perpetual_SOL-USDT_1h_sol_1h_5y_hl_context_features.csv`
- Label named signals:
  `conda run -n hummingbot python scripts/label_market_signals.py --input data/regimes/binance_perpetual_SOL-USDT_1h_sol_1h_5y_hl_context_features.csv --output data/regimes/binance_perpetual_SOL-USDT_1h_sol_1h_5y_hl_context_signals.csv`

## Tests

- `test/hummingbot/strategy_v2/utils/test_market_regime.py`
  - Unit coverage for detector behavior, modifiers, and config-model conversion.

- `test/hummingbot/strategy_v2/utils/test_market_regime_context.py`
  - Unit coverage for SOL context input normalization.

- `test/hummingbot/strategy_v2/utils/test_market_signal_features.py`
  - Unit coverage for reusable trend, VWAP, momentum, volume, derivatives, risk,
    and trap-detection feature enrichment.

- `test/hummingbot/strategy_v2/utils/test_market_signals.py`
  - Unit coverage for named continuation, squeeze, trap, and risk-off signals.

## Key Reminder

The detector is not the strategy. It gives the strategy a market-structure map.
Signal features and signal reports are separate evidence layers. The controller
still decides entries, exits, sizing, leverage, cooldowns, and when to pause.
