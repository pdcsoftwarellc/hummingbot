"""
Backfill compact Hyperliquid L2 order-book features from requester-pays S3.

The archive stores one compressed l2Book JSONL file per day/hour/coin. This
script downloads the requested coin only and aggregates snapshots into 1-minute
features suitable for strategy mining.

Usage:
    conda run -n hummingbot python scripts/backfill_hyperliquid_s3_l2_features.py \
        --coin SOL --start 2026-06-01 --end 2026-06-01
"""
import argparse
import csv
import json
import os
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional


BUCKET = "s3://hyperliquid-archive"
DEFAULT_CACHE_DIR = "data/s3/hyperliquid/market_data/l2Book"
FIELDNAMES = [
    "timestamp",
    "iso_time",
    "coin",
    "update_count",
    "best_bid",
    "best_ask",
    "mid_price",
    "spread",
    "spread_pct",
    "mean_spread_pct",
    "max_spread_pct",
    "bid_depth_top5_usd",
    "ask_depth_top5_usd",
    "depth_top5_usd",
    "mean_depth_top5_usd",
    "min_depth_top5_usd",
    "imbalance_top5",
    "mean_imbalance_top5",
]


@dataclass
class MinuteBucket:
    timestamp: int
    coin: str
    update_count: int = 0
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    mid_price: Optional[float] = None
    spread: Optional[float] = None
    spread_pct: Optional[float] = None
    bid_depth_top5_usd: Optional[float] = None
    ask_depth_top5_usd: Optional[float] = None
    depth_top5_usd: Optional[float] = None
    imbalance_top5: Optional[float] = None
    spread_pct_values: List[float] = field(default_factory=list)
    depth_top5_values: List[float] = field(default_factory=list)
    imbalance_top5_values: List[float] = field(default_factory=list)

    def add(self, features: Dict[str, Optional[float]]):
        self.update_count += 1
        for key in [
            "best_bid",
            "best_ask",
            "mid_price",
            "spread",
            "spread_pct",
            "bid_depth_top5_usd",
            "ask_depth_top5_usd",
            "depth_top5_usd",
            "imbalance_top5",
        ]:
            setattr(self, key, features[key])
        if features["spread_pct"] is not None:
            self.spread_pct_values.append(features["spread_pct"])
        if features["depth_top5_usd"] is not None:
            self.depth_top5_values.append(features["depth_top5_usd"])
        if features["imbalance_top5"] is not None:
            self.imbalance_top5_values.append(features["imbalance_top5"])

    def row(self) -> Dict[str, Optional[float]]:
        return {
            "timestamp": self.timestamp,
            "iso_time": datetime.fromtimestamp(self.timestamp, tz=timezone.utc).isoformat(),
            "coin": self.coin,
            "update_count": self.update_count,
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "mid_price": self.mid_price,
            "spread": self.spread,
            "spread_pct": self.spread_pct,
            "mean_spread_pct": mean(self.spread_pct_values),
            "max_spread_pct": max(self.spread_pct_values) if self.spread_pct_values else None,
            "bid_depth_top5_usd": self.bid_depth_top5_usd,
            "ask_depth_top5_usd": self.ask_depth_top5_usd,
            "depth_top5_usd": self.depth_top5_usd,
            "mean_depth_top5_usd": mean(self.depth_top5_values),
            "min_depth_top5_usd": min(self.depth_top5_values) if self.depth_top5_values else None,
            "imbalance_top5": self.imbalance_top5,
            "mean_imbalance_top5": mean(self.imbalance_top5_values),
        }


def mean(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return sum(values) / len(values)


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def date_range(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def aws_env() -> Dict[str, str]:
    env = os.environ.copy()
    expat_path = "/opt/homebrew/opt/expat/lib"
    existing = env.get("DYLD_LIBRARY_PATH")
    env["DYLD_LIBRARY_PATH"] = expat_path if not existing else f"{expat_path}:{existing}"
    return env


def run_command(command: List[str], env: Optional[Dict[str, str]] = None):
    subprocess.run(command, check=True, env=env)


def local_l2_path(cache_dir: str, coin: str, day: date, hour: int) -> str:
    return os.path.join(cache_dir, coin, day.strftime("%Y%m%d"), f"{hour:02d}.lz4")


def download_l2_book(day: date, hour: int, coin: str, cache_dir: str, aws_command: str) -> Optional[str]:
    local_path = local_l2_path(cache_dir, coin, day, hour)
    if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
        return local_path

    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    s3_path = f"{BUCKET}/market_data/{day.strftime('%Y%m%d')}/{hour}/l2Book/{coin}.lz4"
    try:
        run_command([
            aws_command,
            "s3",
            "cp",
            s3_path,
            local_path,
            "--request-payer",
            "requester",
            "--only-show-errors",
        ], env=aws_env())
    except subprocess.CalledProcessError:
        if os.path.exists(local_path):
            os.remove(local_path)
        return None
    return local_path


def as_float(value: str) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def depth_usd(levels: List[Dict[str, str]], count: int) -> Optional[float]:
    total = 0.0
    seen = 0
    for level in levels[:count]:
        px = as_float(level.get("px"))
        size = as_float(level.get("sz"))
        if px is None or size is None:
            continue
        total += px * size
        seen += 1
    return total if seen else None


def snapshot_features(snapshot: Dict, depth_levels: int) -> Optional[Dict[str, Optional[float]]]:
    data = snapshot.get("raw", {}).get("data", {})
    levels = data.get("levels") or []
    if len(levels) < 2 or not levels[0] or not levels[1]:
        return None

    bids, asks = levels[0], levels[1]
    best_bid = as_float(bids[0].get("px"))
    best_ask = as_float(asks[0].get("px"))
    if best_bid is None or best_ask is None or best_bid <= 0 or best_ask <= 0:
        return None

    mid_price = (best_bid + best_ask) / 2
    spread = max(0.0, best_ask - best_bid)
    spread_pct = spread / mid_price if mid_price > 0 else None
    bid_depth = depth_usd(bids, depth_levels)
    ask_depth = depth_usd(asks, depth_levels)
    depth_total = None
    imbalance = None
    if bid_depth is not None and ask_depth is not None:
        depth_total = bid_depth + ask_depth
        if depth_total > 0:
            imbalance = (bid_depth - ask_depth) / depth_total

    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid_price": mid_price,
        "spread": spread,
        "spread_pct": spread_pct,
        "bid_depth_top5_usd": bid_depth,
        "ask_depth_top5_usd": ask_depth,
        "depth_top5_usd": depth_total,
        "imbalance_top5": imbalance,
    }


def read_l2_minute_rows(lz4_path: str, coin: str, depth_levels: int, lz4_command: str) -> Iterable[Dict]:
    process = subprocess.Popen(
        [lz4_command, "-dc", lz4_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    buckets: Dict[int, MinuteBucket] = {}
    for line in process.stdout:
        snapshot = json.loads(line)
        data = snapshot.get("raw", {}).get("data", {})
        timestamp_ms = data.get("time")
        if timestamp_ms is None:
            continue
        minute_timestamp = int(timestamp_ms // 1000 // 60 * 60)
        features = snapshot_features(snapshot, depth_levels)
        if features is None:
            continue
        bucket = buckets.setdefault(minute_timestamp, MinuteBucket(timestamp=minute_timestamp, coin=coin))
        bucket.add(features)

    stderr = process.stderr.read() if process.stderr is not None else ""
    return_code = process.wait()
    if return_code != 0:
        raise RuntimeError(f"lz4 failed for {lz4_path}: {stderr.strip()}")

    for minute in sorted(buckets):
        yield buckets[minute].row()


def write_rows(output_path: str, rows: Iterable[Dict]):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    temp_path = f"{output_path}.tmp"
    count = 0
    with open(temp_path, "w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
            count += 1
    os.replace(temp_path, output_path)
    print(f"Wrote {count} rows to {output_path}", flush=True)


def default_output_path(coin: str) -> str:
    return f"data/microstructure/hyperliquid_{coin}_l2_1m.csv"


def main():
    parser = argparse.ArgumentParser(description="Backfill Hyperliquid S3 l2Book 1m features")
    parser.add_argument("--coin", default="SOL", help="Hyperliquid coin symbol")
    parser.add_argument("--start", required=True, type=parse_date, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, type=parse_date, help="End date YYYY-MM-DD")
    parser.add_argument("--output", default=None, help="Feature CSV output path")
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR, help="Compressed S3 l2Book cache directory")
    parser.add_argument("--aws-command", default="aws", help="AWS CLI command")
    parser.add_argument("--lz4-command", default="lz4", help="lz4 command")
    parser.add_argument("--depth-levels", type=int, default=5, help="Book levels per side used for depth features")
    args = parser.parse_args()

    if args.start > args.end:
        raise ValueError("--start must be <= --end")

    output_path = args.output or default_output_path(args.coin)

    def row_iter():
        missing_hours = 0
        for day in date_range(args.start, args.end):
            day_rows = 0
            for hour in range(24):
                lz4_path = download_l2_book(day, hour, args.coin, args.cache_dir, args.aws_command)
                if lz4_path is None:
                    missing_hours += 1
                    continue
                for row in read_l2_minute_rows(lz4_path, args.coin, args.depth_levels, args.lz4_command):
                    day_rows += 1
                    yield row
            print(f"Processed {day} {args.coin}: {day_rows} minute rows", flush=True)
        if missing_hours:
            print(f"Missing archive hours: {missing_hours}", flush=True)

    write_rows(output_path, row_iter())


if __name__ == "__main__":
    main()
