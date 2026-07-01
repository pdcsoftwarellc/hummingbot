"""
Backfill Hyperliquid S3 L2 features in resumable monthly chunks.

This wraps scripts/backfill_hyperliquid_s3_l2_features.py so long historical
backfills do not depend on one giant CSV completing successfully.

Usage:
    conda run -n hummingbot python scripts/backfill_hyperliquid_s3_l2_monthly.py \
        --coin SOL --start 2023-04-15 --end 2026-06-01
"""
import argparse
import csv
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable, List


DEFAULT_OUTPUT_DIR = "data/microstructure/hyperliquid_l2_monthly"
DEFAULT_CACHE_DIR = "data/s3/hyperliquid/market_data/l2Book"


@dataclass(frozen=True)
class MonthChunk:
    start: date
    end: date

    def output_name(self, coin: str) -> str:
        return (
            f"hyperliquid_{coin}_l2_execution_1m_"
            f"{self.start.strftime('%Y%m%d')}_{self.end.strftime('%Y%m%d')}.csv"
        )


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def month_end(value: date) -> date:
    if value.month == 12:
        return date(value.year, 12, 31)
    return date(value.year, value.month + 1, 1) - timedelta(days=1)


def month_chunks(start: date, end: date) -> Iterable[MonthChunk]:
    current = start
    while current <= end:
        chunk_end = min(month_end(current), end)
        yield MonthChunk(start=current, end=chunk_end)
        current = chunk_end + timedelta(days=1)


def count_rows(path: str) -> int:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return 0
    with open(path, newline="") as file:
        return max(0, sum(1 for _ in file) - 1)


def write_manifest(path: str, rows: List[dict]):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["coin", "start", "end", "output", "rows", "status"])
        writer.writeheader()
        writer.writerows(rows)


def run_chunk(args, chunk: MonthChunk, output_path: str):
    command = [
        sys.executable,
        "scripts/backfill_hyperliquid_s3_l2_features.py",
        "--coin",
        args.coin,
        "--start",
        chunk.start.isoformat(),
        "--end",
        chunk.end.isoformat(),
        "--output",
        output_path,
        "--cache-dir",
        args.cache_dir,
        "--aws-command",
        args.aws_command,
        "--lz4-command",
        args.lz4_command,
        "--depth-levels",
        str(args.depth_levels),
    ]
    subprocess.run(command, check=True)


def parse_args():
    parser = argparse.ArgumentParser(description="Backfill Hyperliquid S3 L2 features by month")
    parser.add_argument("--coin", default="SOL", help="Hyperliquid coin symbol")
    parser.add_argument("--start", required=True, type=parse_date, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, type=parse_date, help="End date YYYY-MM-DD")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR)
    parser.add_argument("--aws-command", default="aws")
    parser.add_argument("--lz4-command", default="lz4")
    parser.add_argument("--depth-levels", type=int, default=5)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--stop-on-error", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.start > args.end:
        raise ValueError("--start must be <= --end")

    output_dir = os.path.join(args.output_dir, args.coin)
    os.makedirs(output_dir, exist_ok=True)
    manifest_rows = []
    manifest_path = os.path.join(output_dir, "manifest.csv")

    for chunk in month_chunks(args.start, args.end):
        output_path = os.path.join(output_dir, chunk.output_name(args.coin))
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0 and not args.overwrite:
            rows = count_rows(output_path)
            print(f"Skipping {chunk.start} -> {chunk.end}: {rows} rows already at {output_path}", flush=True)
            manifest_rows.append({
                "coin": args.coin,
                "start": chunk.start.isoformat(),
                "end": chunk.end.isoformat(),
                "output": output_path,
                "rows": rows,
                "status": "skipped",
            })
            write_manifest(manifest_path, manifest_rows)
            continue

        print(f"Backfilling {args.coin} L2 {chunk.start} -> {chunk.end}", flush=True)
        try:
            run_chunk(args, chunk, output_path)
            rows = count_rows(output_path)
            status = "ok"
        except subprocess.CalledProcessError:
            rows = count_rows(output_path)
            status = "failed"
            if args.stop_on_error:
                manifest_rows.append({
                    "coin": args.coin,
                    "start": chunk.start.isoformat(),
                    "end": chunk.end.isoformat(),
                    "output": output_path,
                    "rows": rows,
                    "status": status,
                })
                write_manifest(manifest_path, manifest_rows)
                raise

        manifest_rows.append({
            "coin": args.coin,
            "start": chunk.start.isoformat(),
            "end": chunk.end.isoformat(),
            "output": output_path,
            "rows": rows,
            "status": status,
        })
        write_manifest(manifest_path, manifest_rows)

    print(f"Manifest: {manifest_path}", flush=True)


if __name__ == "__main__":
    main()
