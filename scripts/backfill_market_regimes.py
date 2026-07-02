"""
Backfill OHLCV market regime labels.

Usage:
    conda run -n hummingbot python scripts/backfill_market_regimes.py
    conda run -n hummingbot python scripts/backfill_market_regimes.py --days 90 --interval 15m
    conda run -n hummingbot python scripts/backfill_market_regimes.py --connector hyperliquid_perpetual --trading-pair SOL-USD
"""
import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd
import yaml

# Ensure repo root is on the path when executed as a script.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Patch broken optional dependency (injective proto mismatch) used by some imports.
try:
    from pyinjective.proto.injective.stream.v2 import query_pb2
    if not hasattr(query_pb2, "OrderFailuresFilter"):
        query_pb2.OrderFailuresFilter = type("OrderFailuresFilter", (), {})
except ImportError:
    pass

from hummingbot.data_feed.candles_feed.candles_base import CandlesBase  # noqa: E402
from hummingbot.data_feed.candles_feed.candles_factory import CandlesFactory  # noqa: E402
from hummingbot.data_feed.candles_feed.data_types import CandlesConfig, HistoricalCandlesConfig  # noqa: E402
from hummingbot.strategy_v2.utils.market_regime import MarketContext, MarketRegimeConfigModel, MarketRegimeDetector  # noqa: E402
from hummingbot.strategy_v2.utils.market_regime_context import MarketContextBuilder  # noqa: E402


DEFAULT_FEATURE_COLUMNS = [
    "last_close",
    "prior_range_high",
    "prior_range_low",
    "atr_pct",
    "realized_vol",
    "vol_ratio",
    "trend_slope_pct",
    "prior_trend_slope_pct",
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
    "balanced_prior_range",
    "rejected_above_range",
    "rejected_below_range",
    "boundary_touch_count",
    "pullback_from_recent_high_atr",
    "pullback_from_recent_low_atr",
    "distance_from_trend_mean_atr",
    "breakout_distance_pct",
    "breakdown_distance_pct",
    "liquidity_score",
    "liquidity_bad",
    "crowding_score",
    "nearest_liquidation_distance_pct",
    "liquidation_pressure_score",
    "funding_rate",
    "funding_extreme",
    "liquidity_thin",
    "liquidation_flush_score",
    "liquidation_flush_direction",
    "post_liquidation_flush",
    "squeeze_risk",
    "high_vol_danger",
]


def safe_market_file_name(connector: str, trading_pair: str, interval: str) -> str:
    return f"{connector}_{trading_pair}_{interval}.csv".replace("/", "-")


def normalize_candles(candles: pd.DataFrame) -> pd.DataFrame:
    if candles.empty:
        return candles
    candles = candles.copy()
    available_columns = [column for column in CandlesBase.columns if column in candles.columns]
    candles[available_columns] = candles[available_columns].apply(pd.to_numeric, errors="coerce")
    candles = candles.dropna(subset=["timestamp", "open", "high", "low", "close", "volume"])
    candles["timestamp"] = candles["timestamp"].astype(float).astype(int)
    candles = candles.sort_values("timestamp").drop_duplicates(subset=["timestamp"]).reset_index(drop=True)
    return candles


def normalize_timestamp_column(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    if "timestamp" not in frame.columns:
        raise ValueError("context CSV must include a timestamp column")
    raw_timestamp = frame["timestamp"].astype(str).str.strip()
    numeric_timestamp = pd.to_numeric(frame["timestamp"], errors="coerce")
    if numeric_timestamp.notna().all() and raw_timestamp.str.fullmatch(r"\d+(\.\d+)?").all():
        frame["timestamp"] = numeric_timestamp.astype(float).astype(int)
        return frame

    parsed_timestamp = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    if parsed_timestamp.isna().any():
        bad_count = int(parsed_timestamp.isna().sum())
        raise ValueError(f"context CSV has {bad_count} invalid timestamp values")
    epoch_start = pd.Timestamp("1970-01-01", tz="UTC")
    frame["timestamp"] = (parsed_timestamp - epoch_start).dt.total_seconds().astype(int)
    return frame


def iso_to_epoch_seconds(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    if value.isdigit():
        return int(value)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def epoch_to_utc(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def default_candles_cache_path(connector: str, trading_pair: str, interval: str) -> str:
    return os.path.join("data", "candles", safe_market_file_name(connector, trading_pair, interval))


async def fetch_historical_candles(
    connector: str,
    trading_pair: str,
    interval: str,
    start_time: int,
    end_time: int,
    max_records: int,
    chunk_records: Optional[int] = None,
) -> pd.DataFrame:
    candle = CandlesFactory.get_candle(CandlesConfig(
        connector=connector,
        trading_pair=trading_pair,
        interval=interval,
        max_records=max_records,
    ))
    if chunk_records is None:
        candles = await candle.get_historical_candles(HistoricalCandlesConfig(
            connector_name=connector,
            trading_pair=trading_pair,
            interval=interval,
            start_time=start_time,
            end_time=end_time,
        ))
        return normalize_candles(candles)

    interval_seconds = CandlesBase.interval_to_seconds[interval]
    chunk_records = min(chunk_records, candle.candles_max_result_per_rest_request)
    chunk_seconds = interval_seconds * chunk_records
    current_start = start_time
    frames = []
    while current_start <= end_time:
        chunk_end = min(end_time, current_start + chunk_seconds)
        candles = await candle.fetch_candles(start_time=current_start, limit=chunk_records)
        if len(candles) > 0:
            chunk = pd.DataFrame(candles, columns=CandlesBase.columns)
            chunk = chunk[(chunk["timestamp"] >= current_start) & (chunk["timestamp"] <= chunk_end)]
            if not chunk.empty:
                frames.append(chunk)
                last_timestamp = int(chunk["timestamp"].max())
                print(f"  fetched through {epoch_to_utc(last_timestamp)} ({sum(len(frame) for frame in frames)} rows)")
                current_start = last_timestamp + interval_seconds
                continue
        current_start = chunk_end + interval_seconds

    if frames:
        candles = pd.concat(frames, ignore_index=True)
    else:
        candles = pd.DataFrame(columns=CandlesBase.columns)
    return normalize_candles(candles)


async def load_or_fetch_candles(
    connector: str,
    trading_pair: str,
    interval: str,
    start_time: int,
    end_time: int,
    max_records: int,
    chunk_records: Optional[int],
    cache_path: Optional[str],
    no_cache: bool,
    refresh_cache: bool,
) -> pd.DataFrame:
    interval_seconds = CandlesBase.interval_to_seconds[interval]
    cached = pd.DataFrame()
    if cache_path and not no_cache and os.path.exists(cache_path):
        cached = normalize_candles(pd.read_csv(cache_path))
        if not cached.empty:
            print(
                f"  Cache: {cache_path} "
                f"({len(cached)} rows, {epoch_to_utc(int(cached['timestamp'].min()))} -> "
                f"{epoch_to_utc(int(cached['timestamp'].max()))})"
            )

    fetch_ranges = []
    if refresh_cache or cached.empty:
        fetch_ranges.append((start_time, end_time))
    else:
        cached_start = int(cached["timestamp"].min())
        cached_end = int(cached["timestamp"].max())
        if cached_start > start_time:
            fetch_ranges.append((start_time, min(end_time, cached_start - interval_seconds)))
        if cached_end < end_time:
            fetch_ranges.append((max(start_time, cached_end + interval_seconds), end_time))

        requested_cached = cached[(cached["timestamp"] >= start_time) & (cached["timestamp"] <= end_time)]
        if not requested_cached.empty and not (requested_cached["timestamp"].diff().dropna() == interval_seconds).all():
            print("  Cache has internal gaps in the requested range; refreshing requested range.")
            fetch_ranges = [(start_time, end_time)]

    if cache_path and not no_cache and not fetch_ranges and not cached.empty:
        print("  Cache covers requested range.")

    fetched_frames = []
    for fetch_start, fetch_end in fetch_ranges:
        if fetch_start > fetch_end:
            continue
        print(f"  Fetching missing candles: {epoch_to_utc(fetch_start)} -> {epoch_to_utc(fetch_end)}")
        fetched_frames.append(await fetch_historical_candles(
            connector=connector,
            trading_pair=trading_pair,
            interval=interval,
            start_time=fetch_start,
            end_time=fetch_end,
            max_records=max_records,
            chunk_records=chunk_records,
        ))

    frames = [frame for frame in [cached, *fetched_frames] if not frame.empty]
    if not frames:
        return pd.DataFrame(columns=CandlesBase.columns)

    merged = normalize_candles(pd.concat(frames, ignore_index=True))
    cache_changed = any(not frame.empty for frame in fetched_frames)
    if cache_path and not no_cache and cache_changed:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        merged.to_csv(cache_path, index=False)
        print(
            f"  Saved candle cache: {cache_path} "
            f"({len(merged)} rows, {epoch_to_utc(int(merged['timestamp'].min()))} -> "
            f"{epoch_to_utc(int(merged['timestamp'].max()))})"
        )

    return merged[(merged["timestamp"] >= start_time) & (merged["timestamp"] <= end_time)].reset_index(drop=True)


def load_regime_config(path: Optional[str]) -> MarketRegimeConfigModel:
    if path is None:
        return MarketRegimeConfigModel()
    with open(path) as file:
        if path.endswith(".json"):
            config_data = json.load(file) or {}
        else:
            config_data = yaml.safe_load(file) or {}
    return MarketRegimeConfigModel(**config_data)


def build_detector(args: argparse.Namespace) -> MarketRegimeDetector:
    config_model = load_regime_config(args.regime_config)
    overrides = {
        "high_vol_atr_pct": args.high_vol_atr_pct,
        "high_vol_multiplier": args.high_vol_multiplier,
        "max_chop_range_width_pct": args.max_chop_range_width_pct,
        "min_trend_slope_pct": args.min_trend_slope_pct,
    }
    overrides = {key: value for key, value in overrides.items() if value is not None}
    if overrides:
        config_model = config_model.model_copy(update=overrides)
    return MarketRegimeDetector(config_model.to_detector_config())


def build_context_builder(name: str) -> MarketContextBuilder:
    if name == "sol_1h":
        return MarketContextBuilder.sol_1h()
    if name == "generic":
        return MarketContextBuilder()
    raise ValueError(f"Unsupported context builder: {name}")


def load_context_by_timestamp(
    path: Optional[str],
    builder: MarketContextBuilder,
    candle_timestamps: pd.Series,
    max_staleness_seconds: Optional[int],
) -> Dict[int, MarketContext]:
    if path is None:
        return {}
    context_df = normalize_timestamp_column(pd.read_csv(path))
    context_df = context_df.sort_values("timestamp").drop_duplicates(subset=["timestamp"]).reset_index(drop=True)
    if context_df.empty:
        return {}

    candle_df = pd.DataFrame({"timestamp": candle_timestamps.astype(int)}).sort_values("timestamp").reset_index(drop=True)
    merged = pd.merge_asof(
        candle_df,
        context_df,
        on="timestamp",
        direction="backward",
        tolerance=max_staleness_seconds,
    )

    contexts: Dict[int, MarketContext] = {}
    context_columns = [column for column in merged.columns if column != "timestamp"]
    for _, row in merged.iterrows():
        if not context_columns or row[context_columns].isna().all():
            continue
        contexts[int(row["timestamp"])] = builder.build_from_mapping(row.to_dict())
    return contexts


def label_candles(
    candles: pd.DataFrame,
    detector: MarketRegimeDetector,
    contexts_by_timestamp: Optional[Dict[int, MarketContext]] = None,
) -> pd.DataFrame:
    labeled = candles.copy().sort_values("timestamp").reset_index(drop=True)
    contexts_by_timestamp = contexts_by_timestamp or {}
    reports: List[Dict[str, object]] = []
    window_size = detector.config.min_records
    for index in range(len(labeled)):
        window = labeled.iloc[max(0, index + 1 - window_size):index + 1]
        timestamp = int(labeled.loc[index, "timestamp"])
        context = contexts_by_timestamp.get(timestamp)
        report = detector.classify(window, context)
        row = {
            "context_available": context is not None,
            "price_regime": report.price_regime.value,
            "risk_state": report.risk_state.value,
            "execution_posture": report.execution_posture.value,
            "blocked_by": ",".join(report.blocked_by),
            "regime_label": report.label.value,
            "regime_action": report.action.value,
            "grid_bias": report.grid_bias.value,
            "confidence": report.confidence,
            "allow_longs": report.allow_longs,
            "allow_shorts": report.allow_shorts,
            "risk_multiplier": report.risk_multiplier,
            "long_risk_multiplier": report.long_risk_multiplier,
            "short_risk_multiplier": report.short_risk_multiplier,
            "modifiers": ",".join(modifier.value for modifier in report.modifiers),
            "regime_reason": report.reason,
        }
        for feature in DEFAULT_FEATURE_COLUMNS:
            row[feature] = report.features.get(feature)
        reports.append(row)

    report_df = pd.DataFrame(reports)
    return pd.concat([labeled, report_df], axis=1)


def summarize(labeled: pd.DataFrame) -> str:
    lines = []
    if "price_regime" in labeled.columns:
        price_counts = labeled["price_regime"].value_counts()
        lines.append("Price-regime counts:")
        for label, count in price_counts.items():
            pct = count / len(labeled) if len(labeled) else 0
            lines.append(f"  {label:24s} {count:6d} ({pct:6.2%})")
        lines.append("")

    label_counts = labeled["regime_label"].value_counts()
    lines.append("Final regime/posture counts:")
    for label, count in label_counts.items():
        pct = count / len(labeled) if len(labeled) else 0
        lines.append(f"  {label:24s} {count:6d} ({pct:6.2%})")

    if "risk_state" in labeled.columns:
        lines.append("\nRisk-state counts:")
        for state, count in labeled["risk_state"].value_counts().items():
            pct = count / len(labeled) if len(labeled) else 0
            lines.append(f"  {state:24s} {count:6d} ({pct:6.2%})")

    if "blocked_by" in labeled.columns:
        blockers = (
            labeled["blocked_by"]
            .dropna()
            .str.split(",")
            .explode()
        )
        blockers = blockers[blockers != ""]
        if not blockers.empty:
            lines.append("\nBlocker counts:")
            for blocker, count in blockers.value_counts().items():
                pct = count / len(labeled) if len(labeled) else 0
                lines.append(f"  {blocker:24s} {count:6d} ({pct:6.2%})")

    modifiers = (
        labeled["modifiers"]
        .dropna()
        .str.split(",")
        .explode()
    )
    modifiers = modifiers[modifiers != ""]
    if not modifiers.empty:
        lines.append("\nModifier counts:")
        for modifier, count in modifiers.value_counts().items():
            pct = count / len(labeled) if len(labeled) else 0
            lines.append(f"  {modifier:24s} {count:6d} ({pct:6.2%})")

    trade_enabled = labeled[labeled["risk_multiplier"] > 0]
    lines.append("\nRisk posture:")
    lines.append(f"  trade-enabled rows       {len(trade_enabled):6d} ({len(trade_enabled) / len(labeled):6.2%})")
    lines.append(f"  avg risk multiplier      {labeled['risk_multiplier'].mean():8.4f}")
    lines.append(f"  avg long risk multiplier {labeled['long_risk_multiplier'].mean():8.4f}")
    lines.append(f"  avg short risk multiplier{labeled['short_risk_multiplier'].mean():8.4f}")
    return "\n".join(lines)


async def main(args: argparse.Namespace):
    end_time = iso_to_epoch_seconds(args.end) or int(time.time())
    start_time = iso_to_epoch_seconds(args.start) or end_time - int(args.days * 24 * 60 * 60)
    if start_time >= end_time:
        raise ValueError("start time must be before end time")

    interval_seconds = CandlesBase.interval_to_seconds[args.interval]
    estimated_candles = int((end_time - start_time) / interval_seconds)
    max_records = max(args.max_records, estimated_candles + 1)

    candles_cache_path = args.candles_cache or default_candles_cache_path(
        args.connector,
        args.trading_pair,
        args.interval,
    )

    print(f"Preparing candles: {args.connector} {args.trading_pair} @ {args.interval}")
    print(f"  Start: {epoch_to_utc(start_time)}")
    print(f"  End:   {epoch_to_utc(end_time)}")
    print(f"  Estimated candles: {estimated_candles}")
    if not args.no_candles_cache:
        print(f"  Candle cache: {candles_cache_path}")

    candles = await load_or_fetch_candles(
        connector=args.connector,
        trading_pair=args.trading_pair,
        interval=args.interval,
        start_time=start_time,
        end_time=end_time,
        max_records=max_records,
        chunk_records=args.chunk_records,
        cache_path=candles_cache_path,
        no_cache=args.no_candles_cache,
        refresh_cache=args.refresh_candles,
    )
    if candles.empty:
        raise RuntimeError("No candles returned for requested market/range")

    detector = build_detector(args)
    if args.regime_config:
        print(f"  Regime config: {args.regime_config}")

    contexts_by_timestamp = {}
    if args.context_csv:
        max_staleness_seconds = args.context_max_staleness_seconds
        if max_staleness_seconds is None:
            max_staleness_seconds = interval_seconds
        context_builder = build_context_builder(args.context_builder)
        print(f"  Context CSV: {args.context_csv}")
        print(f"  Context builder: {args.context_builder}")
        print(f"  Context max staleness: {max_staleness_seconds}s")
        contexts_by_timestamp = load_context_by_timestamp(
            path=args.context_csv,
            builder=context_builder,
            candle_timestamps=candles["timestamp"],
            max_staleness_seconds=max_staleness_seconds,
        )
        print(f"  Context-matched rows: {len(contexts_by_timestamp)}")

    labeled = label_candles(candles, detector, contexts_by_timestamp)

    output_path = args.output or os.path.join(
        "data",
        "regimes",
        f"{args.connector}_{args.trading_pair}_{args.interval}_{start_time}_{end_time}.csv".replace("/", "-"),
    )
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    labeled.to_csv(output_path, index=False)

    first_ts = int(labeled["timestamp"].iloc[0])
    last_ts = int(labeled["timestamp"].iloc[-1])
    print("\nBackfill complete:")
    print(f"  Rows:  {len(labeled)}")
    print(f"  First: {epoch_to_utc(first_ts)}")
    print(f"  Last:  {epoch_to_utc(last_ts)}")
    print(f"  CSV:   {output_path}")
    print()
    print(summarize(labeled))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill OHLCV market regime labels")
    parser.add_argument("--connector", type=str, default="hyperliquid_perpetual")
    parser.add_argument("--trading-pair", type=str, default="SOL-USD")
    parser.add_argument("--interval", type=str, default="1h")
    parser.add_argument("--days", type=float, default=180)
    parser.add_argument("--start", type=str, default=None, help="ISO timestamp or epoch seconds")
    parser.add_argument("--end", type=str, default=None, help="ISO timestamp or epoch seconds")
    parser.add_argument("--output", type=str, default=None)
    parser.add_argument("--regime-config", type=str, default=None, help="YAML/JSON detector config preset")
    parser.add_argument("--context-csv", type=str, default=None, help="Optional CSV with timestamped market context")
    parser.add_argument(
        "--context-builder",
        type=str,
        default="sol_1h",
        choices=["generic", "sol_1h"],
        help="Market context normalization preset used with --context-csv",
    )
    parser.add_argument(
        "--context-max-staleness-seconds",
        type=int,
        default=None,
        help="Maximum age for as-of context matching. Defaults to one candle interval.",
    )
    parser.add_argument("--candles-cache", type=str, default=None, help="Raw candle cache path")
    parser.add_argument("--no-candles-cache", action="store_true", help="Fetch candles without reading/writing cache")
    parser.add_argument("--refresh-candles", action="store_true", help="Refetch requested candle range and merge cache")
    parser.add_argument("--max-records", type=int, default=500)
    parser.add_argument(
        "--chunk-records",
        type=int,
        default=None,
        help="Fetch forward in fixed-size REST chunks. Useful for long ranges that begin before market listing.",
    )
    parser.add_argument("--high-vol-atr-pct", type=float, default=None)
    parser.add_argument("--high-vol-multiplier", type=float, default=None)
    parser.add_argument(
        "--max-chop-range-width-pct",
        type=float,
        default=None,
    )
    parser.add_argument("--min-trend-slope-pct", type=float, default=None)
    asyncio.run(main(parser.parse_args()))
