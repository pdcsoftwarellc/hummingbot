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

## Key Reminder

The detector is not the strategy. It gives the strategy a market-structure map.
The controller still decides entries, exits, sizing, leverage, cooldowns, and
when to pause.
