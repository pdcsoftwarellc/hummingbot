"""
Forward collect Hyperliquid L2 execution features into 1-minute rows.

The output schema intentionally matches scripts/backfill_hyperliquid_s3_l2_features.py
so S3 historical L2 and live-collected L2 can be merged/deduped later.

Example:
    conda run -n hummingbot python scripts/collect_hyperliquid_l2_features.py --coin SOL --once
"""
import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Dict, Optional
from urllib.request import Request, urlopen


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.backfill_hyperliquid_s3_l2_features import FIELDNAMES, MinuteBucket, snapshot_features


INFO_URL = "https://api.hyperliquid.xyz/info"
DEFAULT_OUTPUT = "data/microstructure/hyperliquid_SOL_l2_execution_live_1m.csv"


def post_info(payload: Dict) -> object:
    data = json.dumps(payload).encode("utf-8")
    request = Request(
        INFO_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def l2_book(coin: str) -> Dict:
    return post_info({"type": "l2Book", "coin": coin})


def minute_timestamp_from_book(book: Dict) -> int:
    timestamp_ms = book.get("time")
    if timestamp_ms is None:
        timestamp_ms = int(time.time() * 1000)
    return int(timestamp_ms // 1000 // 60 * 60)


def snapshot_from_book(book: Dict) -> Dict:
    return {"raw": {"data": book}}


def read_last_timestamp(path: str) -> Optional[int]:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return None
    try:
        with open(path, newline="") as file:
            rows = list(csv.DictReader(file))
    except (OSError, csv.Error):
        return None
    for row in reversed(rows[-500:]):
        try:
            return int(row["timestamp"])
        except (KeyError, TypeError, ValueError):
            continue
    return None


def append_row(path: str, row: Dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_header = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def collect_snapshot(coin: str, depth_levels: int) -> Optional[Dict]:
    book = l2_book(coin)
    return {
        "minute_timestamp": minute_timestamp_from_book(book),
        "features": snapshot_features(snapshot_from_book(book), depth_levels),
    }


def default_output_path(coin: str) -> str:
    return f"data/microstructure/hyperliquid_{coin}_l2_execution_live_1m.csv"


def flush_bucket(output_path: str, bucket: MinuteBucket, last_written_timestamp: Optional[int]) -> Optional[int]:
    if bucket.update_count == 0:
        return last_written_timestamp
    row = bucket.row()
    timestamp = int(row["timestamp"])
    if last_written_timestamp is not None and timestamp <= last_written_timestamp:
        print(f"Skipping already-written minute {row['iso_time']} {bucket.coin}", flush=True)
        return last_written_timestamp
    append_row(output_path, row)
    print(
        f"{row['iso_time']} {bucket.coin} updates={row['update_count']} "
        f"spread_bps={row['spread_bps']} depth_top5={row['depth_top5_usd']} "
        f"buy_10k_slip_bps={row['buy_10k_slippage_bps']}",
        flush=True,
    )
    return timestamp


def collect_once(coin: str, output_path: str, depth_levels: int) -> Dict:
    result = collect_snapshot(coin, depth_levels)
    if result is None or result["features"] is None:
        raise RuntimeError("No L2 features produced")
    bucket = MinuteBucket(timestamp=result["minute_timestamp"], coin=coin)
    bucket.add(result["features"])
    row = bucket.row()
    append_row(output_path, row)
    return row


def main():
    parser = argparse.ArgumentParser(description="Forward collect Hyperliquid rich L2 execution features")
    parser.add_argument("--coin", default="SOL", help="Hyperliquid coin symbol")
    parser.add_argument("--output", default=None, help="CSV output path")
    parser.add_argument("--interval-seconds", type=float, default=5.0, help="Polling interval for loop mode")
    parser.add_argument("--depth-levels", type=int, default=5, help="Book levels per side used for depth_top5 features")
    parser.add_argument("--once", action="store_true", help="Collect one snapshot row and exit")
    args = parser.parse_args()

    output_path = args.output or default_output_path(args.coin)
    if args.once:
        row = collect_once(args.coin, output_path, args.depth_levels)
        print(
            f"{row['iso_time']} {args.coin} updates={row['update_count']} "
            f"spread_bps={row['spread_bps']} depth_top5={row['depth_top5_usd']}",
            flush=True,
        )
        return

    current_bucket: Optional[MinuteBucket] = None
    last_written_timestamp = read_last_timestamp(output_path)
    while True:
        try:
            result = collect_snapshot(args.coin, args.depth_levels)
            if result is None or result["features"] is None:
                print(f"{datetime.now(timezone.utc).isoformat()} no usable L2 features", flush=True)
            else:
                minute_timestamp = result["minute_timestamp"]
                if current_bucket is None:
                    current_bucket = MinuteBucket(timestamp=minute_timestamp, coin=args.coin)
                elif minute_timestamp > current_bucket.timestamp:
                    last_written_timestamp = flush_bucket(output_path, current_bucket, last_written_timestamp)
                    current_bucket = MinuteBucket(timestamp=minute_timestamp, coin=args.coin)
                current_bucket.add(result["features"])
        except Exception as exc:
            print(f"{datetime.now(timezone.utc).isoformat()} collect failed: {exc}", flush=True)
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
