"""
Build a joined market research table for strategy mining.

Default usage builds the SOL 5m table:
    conda run -n hummingbot python scripts/build_joined_research_table.py
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd
from pandas.api.indexers import FixedForwardWindowIndexer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hummingbot.strategy_v2.utils.market_signal_features import (  # noqa: E402
    MarketSignalFeatureConfig,
    enrich_market_signal_features,
)
from hummingbot.strategy_v2.utils.market_signals import MarketSignalConfig, MarketSignalDetector  # noqa: E402


DEFAULT_CANDLES = "data/candles/binance_perpetual_SOL-USDT_5m.csv"
DEFAULT_REGIME = "data/regimes/binance_perpetual_SOL-USDT_1h_sol_1h_5y_hl_context.csv"
DEFAULT_CONTEXT = "data/context/hyperliquid_SOL_merged_context.csv"
DEFAULT_L2 = "data/microstructure/hyperliquid_SOL_l2_1m_20260501_20260601.csv"
DEFAULT_OUTPUT = "data/research/sol_5m_joined_research.csv"


REGIME_COLUMNS = [
    "regime_label",
    "regime_action",
    "grid_bias",
    "confidence",
    "allow_longs",
    "allow_shorts",
    "risk_multiplier",
    "long_risk_multiplier",
    "short_risk_multiplier",
    "modifiers",
    "regime_reason",
    "context_available",
]


REGIME_FEATURE_COLUMNS = [
    "prior_range_high",
    "prior_range_low",
    "atr_pct",
    "realized_vol",
    "vol_ratio",
    "trend_slope_pct",
    "range_width_pct",
    "higher_highs",
    "higher_lows",
    "lower_highs",
    "lower_lows",
    "accepted_above_range",
    "accepted_below_range",
    "raw_accepted_above_range",
    "raw_accepted_below_range",
    "inside_range",
    "clear_boundaries",
    "rejected_above_range",
    "rejected_below_range",
    "liquidity_score",
    "liquidity_bad",
    "funding_extreme",
    "liquidity_thin",
    "post_liquidation_flush",
    "squeeze_risk",
    "high_vol_danger",
]


CONTEXT_CANONICAL_COLUMNS = [
    "funding_rate",
    "premium",
    "open_interest",
    "open_interest_change_pct",
    "mark_price",
    "oracle_price",
    "mid_price",
    "spread_pct",
    "bid_depth_usd",
    "ask_depth_usd",
    "depth_usd",
]


def epoch_to_utc(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def normalize_timestamp_column(frame: pd.DataFrame, source: str) -> pd.DataFrame:
    frame = frame.copy()
    if "timestamp" not in frame.columns:
        raise ValueError(f"{source} must include a timestamp column")
    raw_timestamp = frame["timestamp"].astype(str).str.strip()
    numeric_timestamp = pd.to_numeric(frame["timestamp"], errors="coerce")
    if numeric_timestamp.notna().all() and raw_timestamp.str.fullmatch(r"\d+(\.\d+)?").all():
        frame["timestamp"] = numeric_timestamp.astype(float).astype(int)
        return frame

    parsed_timestamp = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    if parsed_timestamp.isna().any():
        bad_count = int(parsed_timestamp.isna().sum())
        raise ValueError(f"{source} has {bad_count} invalid timestamp values")
    epoch_start = pd.Timestamp("1970-01-01", tz="UTC")
    frame["timestamp"] = (parsed_timestamp - epoch_start).dt.total_seconds().astype(int)
    return frame


def load_frame(path: str, source: str) -> pd.DataFrame:
    frame = normalize_timestamp_column(pd.read_csv(path, low_memory=False), source)
    return frame.sort_values("timestamp").drop_duplicates(subset=["timestamp"]).reset_index(drop=True)


def parse_int_list(value: str) -> List[int]:
    return [int(item.strip()) for item in value.split(",") if item.strip()]


def parse_stop_take_pairs(value: str) -> List[Tuple[float, float]]:
    pairs = []
    for item in value.split(","):
        if not item.strip():
            continue
        stop, take = item.split(":")
        pairs.append((float(stop), float(take)))
    return pairs


def parse_optional_timestamp(value: Optional[str], end_of_day: bool = False) -> Optional[int]:
    if not value:
        return None
    parsed = pd.to_datetime(value, utc=True)
    if end_of_day and parsed.time() == datetime.min.time():
        parsed = parsed + pd.Timedelta(days=1)
    epoch_start = pd.Timestamp("1970-01-01", tz="UTC")
    return int((parsed - epoch_start).total_seconds())


def filter_time_range(frame: pd.DataFrame, start: Optional[str], end: Optional[str]) -> pd.DataFrame:
    start_timestamp = parse_optional_timestamp(start)
    end_timestamp = parse_optional_timestamp(end, end_of_day=True)
    filtered = frame
    if start_timestamp is not None:
        filtered = filtered[filtered["timestamp"] >= start_timestamp]
    if end_timestamp is not None:
        filtered = filtered[filtered["timestamp"] < end_timestamp]
    if filtered.empty:
        raise ValueError("No candle rows remain after applying --start/--end")
    return filtered.reset_index(drop=True)


def pct_token(value: float) -> str:
    return f"{value * 100:.2f}".rstrip("0").rstrip(".").replace(".", "p")


def merge_regime(
    base: pd.DataFrame,
    regime_csv: str,
    availability_lag_seconds: int,
    max_staleness_seconds: int,
) -> pd.DataFrame:
    regime = load_frame(regime_csv, "regime CSV")
    available_columns = [column for column in REGIME_COLUMNS if column in regime.columns]
    feature_columns = [column for column in REGIME_FEATURE_COLUMNS if column in regime.columns]
    keep = ["timestamp", *available_columns, *feature_columns]
    regime = regime[keep].copy()
    regime["regime_source_timestamp"] = regime["timestamp"]
    regime["timestamp"] = regime["timestamp"] + availability_lag_seconds
    regime = regime.rename(columns={
        column: f"regime_{column}"
        for column in feature_columns
    })
    regime = regime.rename(columns={"context_available": "regime_context_available"})

    merged = pd.merge_asof(
        base.sort_values("timestamp"),
        regime.sort_values("timestamp"),
        on="timestamp",
        direction="backward",
        tolerance=max_staleness_seconds,
    )
    merged["regime_available"] = merged["regime_source_timestamp"].notna()
    return merged.reset_index(drop=True)


def merge_context(base: pd.DataFrame, context_csv: Optional[str], max_staleness_seconds: int) -> pd.DataFrame:
    if context_csv is None:
        base["context_available"] = False
        return base
    context = load_frame(context_csv, "context CSV")
    context["context_source_timestamp"] = context["timestamp"]
    rename_map = {
        column: f"context_{column}"
        for column in context.columns
        if column != "timestamp"
    }
    context = context.rename(columns=rename_map)
    merged = pd.merge_asof(
        base.sort_values("timestamp"),
        context.sort_values("timestamp"),
        on="timestamp",
        direction="backward",
        tolerance=max_staleness_seconds,
    )
    merged["context_available"] = merged["context_context_source_timestamp"].notna()
    for column in CONTEXT_CANONICAL_COLUMNS:
        context_column = f"context_{column}"
        if context_column in merged.columns:
            merged[column] = merged[context_column]
    return merged.reset_index(drop=True)


def merge_l2(base: pd.DataFrame, l2_csv: Optional[str], max_staleness_seconds: int) -> pd.DataFrame:
    if l2_csv is None or not os.path.exists(l2_csv):
        base["l2_available"] = False
        return base
    l2 = load_frame(l2_csv, "L2 CSV")
    l2["l2_source_timestamp"] = l2["timestamp"]
    l2 = l2.rename(columns={
        column: f"l2_{column}"
        for column in l2.columns
        if column != "timestamp"
    })
    merged = pd.merge_asof(
        base.sort_values("timestamp"),
        l2.sort_values("timestamp"),
        on="timestamp",
        direction="backward",
        tolerance=max_staleness_seconds,
    )
    merged["l2_available"] = merged["l2_l2_source_timestamp"].notna()
    return merged.reset_index(drop=True)


def label_market_signals(frame: pd.DataFrame, detector: MarketSignalDetector) -> pd.DataFrame:
    rows = []
    for _, row in frame.iterrows():
        report = detector.evaluate(row.to_dict())
        rows.append({
            "market_signals": ",".join(signal.value for signal in report.signals),
            "long_signal_score": report.long_score,
            "short_signal_score": report.short_score,
            "risk_off_signal": report.risk_off,
            "signal_scores": json.dumps({signal.value: score for signal, score in report.scores.items()}, sort_keys=True),
            "signal_reasons": json.dumps({signal.value: reason for signal, reason in report.reasons.items()}, sort_keys=True),
        })
    return pd.concat([frame.reset_index(drop=True), pd.DataFrame(rows)], axis=1)


def add_forward_outcomes(
    frame: pd.DataFrame,
    horizons: Iterable[int],
    stop_take_pairs: Iterable[Tuple[float, float]],
) -> pd.DataFrame:
    df = frame.copy()
    open_ = pd.to_numeric(df["open"], errors="coerce").to_numpy(dtype="float64")
    high = pd.to_numeric(df["high"], errors="coerce").to_numpy(dtype="float64")
    low = pd.to_numeric(df["low"], errors="coerce").to_numpy(dtype="float64")
    close = pd.to_numeric(df["close"], errors="coerce").to_numpy(dtype="float64")
    n = len(df)

    for horizon in horizons:
        if horizon < 1:
            continue
        entry = np.full(n, np.nan)
        future_close = np.full(n, np.nan)
        future_high_max = np.full(n, np.nan)
        future_low_min = np.full(n, np.nan)

        valid_count = n - horizon
        if valid_count <= 0:
            continue

        entry[:valid_count] = open_[1:valid_count + 1]
        future_close[:valid_count] = close[horizon:]

        forward_indexer = FixedForwardWindowIndexer(window_size=horizon)
        future_high = pd.Series(high).shift(-1).rolling(forward_indexer, min_periods=horizon).max().to_numpy()
        future_low = pd.Series(low).shift(-1).rolling(forward_indexer, min_periods=horizon).min().to_numpy()
        future_high_max[:valid_count] = future_high[:valid_count]
        future_low_min[:valid_count] = future_low[:valid_count]

        df[f"entry_next_open_h{horizon}"] = entry
        df[f"forward_return_h{horizon}"] = future_close / entry - 1
        df[f"mfe_long_h{horizon}"] = future_high_max / entry - 1
        df[f"mae_long_h{horizon}"] = future_low_min / entry - 1
        df[f"mfe_short_h{horizon}"] = entry / future_low_min - 1
        df[f"mae_short_h{horizon}"] = entry / future_high_max - 1

        high_windows = np.lib.stride_tricks.sliding_window_view(high[1:], horizon)
        low_windows = np.lib.stride_tricks.sliding_window_view(low[1:], horizon)
        close_exit = future_close[:valid_count]
        entry_valid = entry[:valid_count]

        for stop_pct, take_pct in stop_take_pairs:
            token = f"sl{pct_token(stop_pct)}_tp{pct_token(take_pct)}"
            long_take_hit = high_windows >= (entry_valid[:, None] * (1 + take_pct))
            long_stop_hit = low_windows <= (entry_valid[:, None] * (1 - stop_pct))
            short_take_hit = low_windows <= (entry_valid[:, None] * (1 - take_pct))
            short_stop_hit = high_windows >= (entry_valid[:, None] * (1 + stop_pct))

            long_take_any = long_take_hit.any(axis=1)
            long_stop_any = long_stop_hit.any(axis=1)
            short_take_any = short_take_hit.any(axis=1)
            short_stop_any = short_stop_hit.any(axis=1)

            no_hit_index = horizon + 1
            long_take_first = np.where(long_take_any, long_take_hit.argmax(axis=1), no_hit_index)
            long_stop_first = np.where(long_stop_any, long_stop_hit.argmax(axis=1), no_hit_index)
            short_take_first = np.where(short_take_any, short_take_hit.argmax(axis=1), no_hit_index)
            short_stop_first = np.where(short_stop_any, short_stop_hit.argmax(axis=1), no_hit_index)

            long_return = close_exit / entry_valid - 1
            long_exit = np.full(valid_count, "timeout", dtype=object)
            long_stopped = long_stop_first <= long_take_first
            long_taken = long_take_first < long_stop_first
            long_return = np.where(long_stopped, -stop_pct, long_return)
            long_return = np.where(long_taken, take_pct, long_return)
            long_exit[long_stopped] = "stop"
            long_exit[long_taken] = "take"

            short_return = entry_valid / close_exit - 1
            short_exit = np.full(valid_count, "timeout", dtype=object)
            short_stopped = short_stop_first <= short_take_first
            short_taken = short_take_first < short_stop_first
            short_return = np.where(short_stopped, -stop_pct, short_return)
            short_return = np.where(short_taken, take_pct, short_return)
            short_exit[short_stopped] = "stop"
            short_exit[short_taken] = "take"

            long_column = f"long_{token}_h{horizon}_return"
            short_column = f"short_{token}_h{horizon}_return"
            df[long_column] = np.nan
            df[short_column] = np.nan
            df.loc[:valid_count - 1, long_column] = long_return
            df.loc[:valid_count - 1, short_column] = short_return
            df.loc[:valid_count - 1, f"long_{token}_h{horizon}_exit"] = long_exit
            df.loc[:valid_count - 1, f"short_{token}_h{horizon}_exit"] = short_exit

    return df


def summarize(frame: pd.DataFrame) -> str:
    lines = [
        f"Rows:  {len(frame)}",
        f"First: {epoch_to_utc(int(frame['timestamp'].min()))}",
        f"Last:  {epoch_to_utc(int(frame['timestamp'].max()))}",
    ]
    for column in ["regime_available", "context_available", "l2_available"]:
        if column in frame.columns:
            count = int(frame[column].fillna(False).sum())
            lines.append(f"{column}: {count} ({count / len(frame):.2%})")
    if "market_signals" in frame.columns:
        signal_counts = frame["market_signals"].fillna("").str.split(",").explode()
        signal_counts = signal_counts[signal_counts != ""].value_counts()
        if not signal_counts.empty:
            lines.append("Signals:")
            for signal, count in signal_counts.head(10).items():
                lines.append(f"  {signal}: {count} ({count / len(frame):.2%})")
    return "\n".join(lines)


def parse_args():
    parser = argparse.ArgumentParser(description="Build joined research table")
    parser.add_argument("--candles-csv", default=DEFAULT_CANDLES)
    parser.add_argument("--regime-csv", default=DEFAULT_REGIME)
    parser.add_argument("--context-csv", default=DEFAULT_CONTEXT)
    parser.add_argument("--l2-csv", default=DEFAULT_L2)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--start", help="Optional inclusive candle start, e.g. 2024-01-01")
    parser.add_argument("--end", help="Optional exclusive candle end, e.g. 2026-07-01")
    parser.add_argument("--regime-availability-lag-seconds", type=int, default=3600)
    parser.add_argument("--regime-max-staleness-seconds", type=int, default=3900)
    parser.add_argument("--context-max-staleness-seconds", type=int, default=3600)
    parser.add_argument("--l2-max-staleness-seconds", type=int, default=300)
    parser.add_argument("--horizons", default="12,24,48", help="Forward outcome horizons in base candle bars")
    parser.add_argument("--stop-take-pairs", default="0.005:0.015,0.01:0.03,0.015:0.045")
    parser.add_argument("--ema-fast", type=int, default=20)
    parser.add_argument("--ema-slow", type=int, default=50)
    parser.add_argument("--rolling-vwap-window", type=int, default=48)
    parser.add_argument("--rsi-length", type=int, default=14)
    parser.add_argument("--roc-periods", default="6,12")
    parser.add_argument("--volume-window", type=int, default=48)
    parser.add_argument("--funding-trend-window", type=int, default=48)
    parser.add_argument("--oi-change-window", type=int, default=48)
    parser.add_argument("--premium-trend-window", type=int, default=48)
    parser.add_argument("--trap-lookback", type=int, default=12)
    return parser.parse_args()


def main():
    args = parse_args()
    candles = filter_time_range(load_frame(args.candles_csv, "candles CSV"), args.start, args.end)
    joined = merge_regime(
        base=candles,
        regime_csv=args.regime_csv,
        availability_lag_seconds=args.regime_availability_lag_seconds,
        max_staleness_seconds=args.regime_max_staleness_seconds,
    )
    joined = merge_context(joined, args.context_csv, args.context_max_staleness_seconds)
    joined = merge_l2(joined, args.l2_csv, args.l2_max_staleness_seconds)

    feature_config = MarketSignalFeatureConfig(
        ema_fast=args.ema_fast,
        ema_slow=args.ema_slow,
        rolling_vwap_window=args.rolling_vwap_window,
        rsi_length=args.rsi_length,
        roc_periods=parse_int_list(args.roc_periods),
        volume_window=args.volume_window,
        funding_trend_window=args.funding_trend_window,
        oi_change_window=args.oi_change_window,
        premium_trend_window=args.premium_trend_window,
        trap_lookback=args.trap_lookback,
    )
    joined = enrich_market_signal_features(joined, feature_config)
    joined = label_market_signals(joined, MarketSignalDetector(MarketSignalConfig()))
    joined = add_forward_outcomes(
        joined,
        horizons=parse_int_list(args.horizons),
        stop_take_pairs=parse_stop_take_pairs(args.stop_take_pairs),
    )

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    joined.to_csv(args.output, index=False)
    print("Joined research table complete:")
    print(summarize(joined))
    print(f"CSV:   {args.output}")


if __name__ == "__main__":
    main()
