"""
Backfill and cache raw OHLCV candles without regime labeling.

Usage:
    conda run -n hummingbot python scripts/backfill_market_candles.py \
        --connector binance_perpetual --trading-pair SOL-USDT --interval 5m \
        --start 2021-07-01 --end 2026-06-30 --chunk-records 1000
"""
import argparse
import asyncio
import os
import sys
import time

# Ensure repo root is on the path when executed as a script.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hummingbot.data_feed.candles_feed.candles_base import CandlesBase  # noqa: E402
from scripts.backfill_market_regimes import (  # noqa: E402
    default_candles_cache_path,
    epoch_to_utc,
    iso_to_epoch_seconds,
    load_or_fetch_candles,
)


async def main(args: argparse.Namespace):
    end_time = iso_to_epoch_seconds(args.end) or int(time.time())
    start_time = iso_to_epoch_seconds(args.start) or end_time - int(args.days * 24 * 60 * 60)
    if start_time >= end_time:
        raise ValueError("start time must be before end time")

    interval_seconds = CandlesBase.interval_to_seconds[args.interval]
    estimated_candles = int((end_time - start_time) / interval_seconds)
    max_records = max(args.max_records, estimated_candles + 1)
    candles_cache_path = args.output or default_candles_cache_path(
        args.connector,
        args.trading_pair,
        args.interval,
    )

    print(f"Preparing raw candles: {args.connector} {args.trading_pair} @ {args.interval}")
    print(f"  Start: {epoch_to_utc(start_time)}")
    print(f"  End:   {epoch_to_utc(end_time)}")
    print(f"  Estimated candles: {estimated_candles}")
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
        no_cache=False,
        refresh_cache=args.refresh,
    )
    if candles.empty:
        raise RuntimeError("No candles returned for requested market/range")

    first_ts = int(candles["timestamp"].iloc[0])
    last_ts = int(candles["timestamp"].iloc[-1])
    print("\nBackfill complete:")
    print(f"  Rows:  {len(candles)}")
    print(f"  First: {epoch_to_utc(first_ts)}")
    print(f"  Last:  {epoch_to_utc(last_ts)}")
    print(f"  CSV:   {candles_cache_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill raw OHLCV candle cache")
    parser.add_argument("--connector", type=str, default="binance_perpetual")
    parser.add_argument("--trading-pair", type=str, default="SOL-USDT")
    parser.add_argument("--interval", type=str, default="5m")
    parser.add_argument("--days", type=float, default=180)
    parser.add_argument("--start", type=str, default=None, help="ISO timestamp or epoch seconds")
    parser.add_argument("--end", type=str, default=None, help="ISO timestamp or epoch seconds")
    parser.add_argument("--output", type=str, default=None, help="Candle cache CSV path")
    parser.add_argument("--refresh", action="store_true", help="Refetch requested range and merge cache")
    parser.add_argument("--max-records", type=int, default=500)
    parser.add_argument(
        "--chunk-records",
        type=int,
        default=None,
        help="Fetch forward in fixed-size REST chunks. Useful for long ranges.",
    )
    asyncio.run(main(parser.parse_args()))
