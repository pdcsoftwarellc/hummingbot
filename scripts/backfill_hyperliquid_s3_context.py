"""
Backfill Hyperliquid S3 asset context into regime-context CSV format.

Example:
    conda run -n hummingbot python scripts/backfill_hyperliquid_s3_context.py \
        --coin SOL --start 2026-06-01 --end 2026-06-01
"""
import argparse
import csv
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional

# Ensure repo root is on the path when executed as a script.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.collect_hyperliquid_context import FIELDNAMES, as_float


DEFAULT_CACHE_DIR = "data/s3/hyperliquid/asset_ctxs"
BUCKET = "s3://hyperliquid-archive"


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def date_range(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def timestamp_from_iso(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def run_command(command: List[str], env: Optional[Dict[str, str]] = None):
    subprocess.run(command, check=True, env=env)


def aws_env() -> Dict[str, str]:
    env = os.environ.copy()
    expat_path = "/opt/homebrew/opt/expat/lib"
    existing = env.get("DYLD_LIBRARY_PATH")
    env["DYLD_LIBRARY_PATH"] = expat_path if not existing else f"{expat_path}:{existing}"
    return env


def download_asset_ctxs(day: date, cache_dir: str, aws_command: str) -> str:
    os.makedirs(cache_dir, exist_ok=True)
    day_key = day.strftime("%Y%m%d")
    local_path = os.path.join(cache_dir, f"{day_key}.csv.lz4")
    if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
        return local_path

    s3_path = f"{BUCKET}/asset_ctxs/{day_key}.csv.lz4"
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
    return local_path


def read_coin_rows(lz4_path: str, coin: str, lz4_command: str) -> Iterable[Dict[str, str]]:
    process = subprocess.Popen(
        [lz4_command, "-dc", lz4_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    reader = csv.DictReader(process.stdout)
    for row in reader:
        if row.get("coin") == coin:
            yield row
    stderr = process.stderr.read() if process.stderr is not None else ""
    return_code = process.wait()
    if return_code != 0:
        raise RuntimeError(f"lz4 failed for {lz4_path}: {stderr.strip()}")


def context_row(row: Dict[str, str], previous_open_interest: Optional[float]) -> Dict:
    open_interest = as_float(row.get("open_interest"))
    mid_price = as_float(row.get("mid_px"))
    impact_bid = as_float(row.get("impact_bid_px"))
    impact_ask = as_float(row.get("impact_ask_px"))
    spread_pct = None
    if mid_price and mid_price > 0 and impact_bid is not None and impact_ask is not None:
        spread_pct = max(0.0, impact_ask - impact_bid) / mid_price

    open_interest_change_pct = None
    if open_interest is not None and previous_open_interest and previous_open_interest > 0:
        open_interest_change_pct = open_interest / previous_open_interest - 1

    timestamp = timestamp_from_iso(row["time"])
    return {
        "timestamp": timestamp,
        "iso_time": datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(),
        "coin": row.get("coin"),
        "funding_rate": as_float(row.get("funding")),
        "premium": as_float(row.get("premium")),
        "open_interest": open_interest,
        "open_interest_change_pct": open_interest_change_pct,
        "mark_price": as_float(row.get("mark_px")),
        "oracle_price": as_float(row.get("oracle_px")),
        "mid_price": mid_price,
        "prev_day_price": as_float(row.get("prev_day_px")),
        "day_ntl_volume": as_float(row.get("day_ntl_vlm")),
        "day_base_volume": None,
        "impact_bid_price": impact_bid,
        "impact_ask_price": impact_ask,
        "best_bid": None,
        "best_ask": None,
        "spread_pct": spread_pct,
        "bid_depth_usd": None,
        "ask_depth_usd": None,
        "depth_usd": None,
        "book_time_ms": None,
    }


def write_rows(output_path: str, rows: List[Dict]):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    rows = sorted(rows, key=lambda item: item["timestamp"])
    with open(output_path, "w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def default_output_path(coin: str) -> str:
    return f"data/context/hyperliquid_{coin}_s3_context.csv"


def main():
    parser = argparse.ArgumentParser(description="Backfill Hyperliquid S3 asset context")
    parser.add_argument("--coin", default="SOL", help="Hyperliquid coin symbol")
    parser.add_argument("--start", required=True, type=parse_date, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, type=parse_date, help="End date YYYY-MM-DD")
    parser.add_argument("--output", default=None, help="Context CSV output path")
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR, help="Compressed S3 asset_ctxs cache directory")
    parser.add_argument("--aws-command", default="aws", help="AWS CLI command")
    parser.add_argument("--lz4-command", default="lz4", help="lz4 command")
    args = parser.parse_args()

    if args.start > args.end:
        raise ValueError("--start must be <= --end")

    output_path = args.output or default_output_path(args.coin)
    rows = []
    non_standard_days = {}
    previous_open_interest = None
    for day in date_range(args.start, args.end):
        print(f"Processing {day} {args.coin}", flush=True)
        lz4_path = download_asset_ctxs(day, args.cache_dir, args.aws_command)
        day_count = 0
        for raw_row in read_coin_rows(lz4_path, args.coin, args.lz4_command):
            row = context_row(raw_row, previous_open_interest)
            previous_open_interest = row["open_interest"]
            rows.append(row)
            day_count += 1
        print(f"  rows: {day_count}", flush=True)
        if day_count != 1440:
            non_standard_days[day.isoformat()] = day_count

    write_rows(output_path, rows)
    print(f"Wrote {len(rows)} rows to {output_path}", flush=True)
    if non_standard_days:
        print("Non-standard archive days:", flush=True)
        for day, count in non_standard_days.items():
            print(f"  {day}: {count} rows", flush=True)


if __name__ == "__main__":
    main()
