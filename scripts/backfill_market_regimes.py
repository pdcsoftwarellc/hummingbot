"""
Backfill OHLCV market regime labels.

Usage:
    conda run -n hummingbot python scripts/backfill_market_regimes.py
    conda run -n hummingbot python scripts/backfill_market_regimes.py --days 90 --interval 15m
    conda run -n hummingbot python scripts/backfill_market_regimes.py --connector hyperliquid_perpetual --trading-pair SOL-USD
"""
import argparse
import asyncio
import os
import sys
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd

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
from hummingbot.strategy_v2.utils.market_regime import (  # noqa: E402
    MarketRegimeConfig,
    MarketRegimeDetector,
)


DEFAULT_FEATURE_COLUMNS = [
    "atr_pct",
    "realized_vol",
    "vol_ratio",
    "trend_slope_pct",
    "prior_trend_slope_pct",
    "range_width_pct",
    "boundary_touch_count",
    "pullback_from_recent_high_atr",
    "pullback_from_recent_low_atr",
    "distance_from_trend_mean_atr",
    "breakout_distance_pct",
    "breakdown_distance_pct",
]


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
        candles = candles.sort_values("timestamp").drop_duplicates(subset=["timestamp"]).reset_index(drop=True)
        return candles

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
    candles = candles.sort_values("timestamp").drop_duplicates(subset=["timestamp"]).reset_index(drop=True)
    return candles


def build_detector(args: argparse.Namespace) -> MarketRegimeDetector:
    config = MarketRegimeConfig(
        high_vol_atr_pct=args.high_vol_atr_pct,
        high_vol_multiplier=args.high_vol_multiplier,
        max_chop_range_width_pct=args.max_chop_range_width_pct,
        min_trend_slope_pct=args.min_trend_slope_pct,
    )
    return MarketRegimeDetector(config)


def label_candles(candles: pd.DataFrame, detector: MarketRegimeDetector) -> pd.DataFrame:
    labeled = candles.copy().sort_values("timestamp").reset_index(drop=True)
    reports: List[Dict[str, object]] = []
    window_size = detector.config.min_records
    for index in range(len(labeled)):
        window = labeled.iloc[max(0, index + 1 - window_size):index + 1]
        report = detector.classify(window)
        row = {
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
    label_counts = labeled["regime_label"].value_counts()
    lines.append("Regime counts:")
    for label, count in label_counts.items():
        pct = count / len(labeled) if len(labeled) else 0
        lines.append(f"  {label:24s} {count:6d} ({pct:6.2%})")

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

    print(f"Fetching candles: {args.connector} {args.trading_pair} @ {args.interval}")
    print(f"  Start: {epoch_to_utc(start_time)}")
    print(f"  End:   {epoch_to_utc(end_time)}")
    print(f"  Estimated candles: {estimated_candles}")

    candles = await fetch_historical_candles(
        connector=args.connector,
        trading_pair=args.trading_pair,
        interval=args.interval,
        start_time=start_time,
        end_time=end_time,
        max_records=max_records,
        chunk_records=args.chunk_records,
    )
    if candles.empty:
        raise RuntimeError("No candles returned for requested market/range")

    detector = build_detector(args)
    labeled = label_candles(candles, detector)

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
    parser.add_argument("--max-records", type=int, default=500)
    parser.add_argument(
        "--chunk-records",
        type=int,
        default=None,
        help="Fetch forward in fixed-size REST chunks. Useful for long ranges that begin before market listing.",
    )
    parser.add_argument("--high-vol-atr-pct", type=float, default=MarketRegimeConfig.high_vol_atr_pct)
    parser.add_argument("--high-vol-multiplier", type=float, default=MarketRegimeConfig.high_vol_multiplier)
    parser.add_argument(
        "--max-chop-range-width-pct",
        type=float,
        default=MarketRegimeConfig.max_chop_range_width_pct,
    )
    parser.add_argument("--min-trend-slope-pct", type=float, default=MarketRegimeConfig.min_trend_slope_pct)
    asyncio.run(main(parser.parse_args()))
