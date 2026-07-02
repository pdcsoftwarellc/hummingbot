"""
Enrich candle/regime CSVs with reusable signal-discovery features.

Usage:
    conda run -n hummingbot python scripts/enrich_market_signal_features.py \
        --input data/regimes/binance_perpetual_SOL-USDT_1h_sol_1h_5y_hl_context.csv \
        --context-csv data/context/hyperliquid_SOL_merged_context.csv \
        --output data/regimes/binance_perpetual_SOL-USDT_1h_sol_1h_5y_hl_context_features.csv
"""

import argparse
import os
import sys
from typing import Optional

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hummingbot.strategy_v2.utils.market_signal_features import (  # noqa: E402
    MarketSignalFeatureConfig,
    enrich_market_signal_features,
)
from scripts.research_utils import epoch_to_utc, load_csv_frame, parse_int_list  # noqa: E402


def load_frame(path: str, source: str) -> pd.DataFrame:
    return load_csv_frame(path, source)


def merge_context(
    candles: pd.DataFrame,
    context_csv: Optional[str],
    max_staleness_seconds: Optional[int],
) -> pd.DataFrame:
    if context_csv is None:
        return candles
    context = load_frame(context_csv, "context CSV")
    if context.empty:
        return candles

    rename_map = {
        column: f"context_{column}"
        for column in context.columns
        if column != "timestamp" and column in candles.columns
    }
    context = context.rename(columns=rename_map)
    merged = pd.merge_asof(
        candles.sort_values("timestamp"),
        context.sort_values("timestamp"),
        on="timestamp",
        direction="backward",
        tolerance=max_staleness_seconds,
    )

    # Promote raw context columns that do not already exist. Preserve detector
    # columns like funding_rate/liquidity_score when they are already present.
    for raw_name in ["funding_rate", "premium", "open_interest", "open_interest_change_pct", "spread_pct", "depth_usd"]:
        context_name = f"context_{raw_name}"
        if context_name in merged.columns and raw_name not in merged.columns:
            merged[raw_name] = merged[context_name]
    return merged.reset_index(drop=True)


def parse_periods(value: str):
    return parse_int_list(value)


def parse_args():
    parser = argparse.ArgumentParser(description="Enrich market CSV with reusable signal features")
    parser.add_argument("--input", required=True, help="Input candles or labeled regime CSV")
    parser.add_argument("--output", required=True, help="Output enriched CSV")
    parser.add_argument("--context-csv", default=None, help="Optional raw context CSV to merge as-of")
    parser.add_argument(
        "--context-max-staleness-seconds",
        type=int,
        default=3600,
        help="Maximum context age for as-of merge",
    )
    parser.add_argument("--ema-fast", type=int, default=20)
    parser.add_argument("--ema-slow", type=int, default=50)
    parser.add_argument("--rolling-vwap-window", type=int, default=24)
    parser.add_argument("--rsi-length", type=int, default=14)
    parser.add_argument("--roc-periods", default="6,12")
    parser.add_argument("--volume-window", type=int, default=24)
    parser.add_argument("--funding-trend-window", type=int, default=24)
    parser.add_argument("--oi-change-window", type=int, default=24)
    parser.add_argument("--premium-trend-window", type=int, default=24)
    parser.add_argument("--trap-lookback", type=int, default=6)
    return parser.parse_args()


def main():
    args = parse_args()
    candles = load_frame(args.input, "input CSV")
    candles = merge_context(
        candles=candles,
        context_csv=args.context_csv,
        max_staleness_seconds=args.context_max_staleness_seconds,
    )
    config = MarketSignalFeatureConfig(
        ema_fast=args.ema_fast,
        ema_slow=args.ema_slow,
        rolling_vwap_window=args.rolling_vwap_window,
        rsi_length=args.rsi_length,
        roc_periods=parse_periods(args.roc_periods),
        volume_window=args.volume_window,
        funding_trend_window=args.funding_trend_window,
        oi_change_window=args.oi_change_window,
        premium_trend_window=args.premium_trend_window,
        trap_lookback=args.trap_lookback,
    )
    enriched = enrich_market_signal_features(candles, config)
    output_dir = os.path.dirname(args.output)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    enriched.to_csv(args.output, index=False)

    first_ts = int(enriched["timestamp"].iloc[0])
    last_ts = int(enriched["timestamp"].iloc[-1])
    feature_columns = [column for column in enriched.columns if column not in candles.columns]
    print("Feature enrichment complete:")
    print(f"  Rows:     {len(enriched)}")
    print(f"  First:    {epoch_to_utc(first_ts)}")
    print(f"  Last:     {epoch_to_utc(last_ts)}")
    print(f"  Features: {len(feature_columns)} new columns")
    print(f"  CSV:      {args.output}")


if __name__ == "__main__":
    main()
