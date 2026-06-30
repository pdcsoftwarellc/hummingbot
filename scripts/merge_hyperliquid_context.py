"""
Merge Hyperliquid S3 context with forward-collected live context.

Example:
    conda run -n hummingbot python scripts/merge_hyperliquid_context.py \
        --coin SOL \
        --s3-csv data/context/hyperliquid_SOL_s3_context.csv \
        --live-csv data/context/hyperliquid_SOL_context.csv \
        --output data/context/hyperliquid_SOL_merged_context.csv
"""
import argparse
import os
import sys
from typing import List, Optional, Tuple

import pandas as pd

# Ensure repo root is on the path when executed as a script.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.collect_hyperliquid_context import FIELDNAMES


DEFAULT_S3_CSV = "data/context/hyperliquid_SOL_s3_context.csv"
DEFAULT_LIVE_CSV = "data/context/hyperliquid_SOL_context.csv"
DEFAULT_OUTPUT = "data/context/hyperliquid_SOL_merged_context.csv"


def read_context(path: str, source: str, coin: Optional[str]) -> pd.DataFrame:
    if not path or not os.path.exists(path) or os.path.getsize(path) == 0:
        return pd.DataFrame(columns=FIELDNAMES + ["_source"])

    frame = pd.read_csv(path)
    if frame.empty:
        return pd.DataFrame(columns=FIELDNAMES + ["_source"])

    frame = frame.copy()
    frame["timestamp"] = pd.to_numeric(frame["timestamp"], errors="coerce")
    frame = frame.dropna(subset=["timestamp"])
    frame["timestamp"] = frame["timestamp"].astype(int)

    if coin and "coin" in frame.columns:
        frame = frame[frame["coin"] == coin]

    for field in FIELDNAMES:
        if field not in frame.columns:
            frame[field] = pd.NA
    frame = frame[FIELDNAMES].copy()
    frame["_source"] = source
    return frame


def merge_context_frames(
    s3_frame: pd.DataFrame,
    live_frame: pd.DataFrame,
    prefer: str = "live",
) -> Tuple[pd.DataFrame, int]:
    priority = {"s3": 1, "live": 2} if prefer == "live" else {"live": 1, "s3": 2}
    combined = pd.concat([s3_frame, live_frame], ignore_index=True)
    if combined.empty:
        return pd.DataFrame(columns=FIELDNAMES), 0

    combined["_priority"] = combined["_source"].map(priority).fillna(0)
    duplicate_rows = int(combined.duplicated(subset=["timestamp"], keep=False).sum())
    merged = (
        combined
        .sort_values(["timestamp", "_priority"])
        .drop_duplicates(subset=["timestamp"], keep="last")
        .sort_values("timestamp")
        .reset_index(drop=True)
    )
    return merged[FIELDNAMES], duplicate_rows


def write_context(path: str, frame: pd.DataFrame):
    output_dir = os.path.dirname(path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    frame.to_csv(path, index=False)


def summarize(frame: pd.DataFrame, s3_rows: int, live_rows: int, duplicate_rows: int, output: str) -> List[str]:
    lines = [
        "Merge complete:",
        f"  S3 rows:       {s3_rows}",
        f"  Live rows:     {live_rows}",
        f"  Duplicate rows:{duplicate_rows:8d}",
        f"  Output rows:   {len(frame)}",
        f"  CSV:           {output}",
    ]
    if not frame.empty:
        first = pd.to_datetime(frame["timestamp"].min(), unit="s", utc=True)
        last = pd.to_datetime(frame["timestamp"].max(), unit="s", utc=True)
        lines.extend([
            f"  First:         {first}",
            f"  Last:          {last}",
        ])
    return lines


def main():
    parser = argparse.ArgumentParser(description="Merge Hyperliquid S3 and live context CSVs")
    parser.add_argument("--coin", default="SOL", help="Hyperliquid coin symbol")
    parser.add_argument("--s3-csv", default=DEFAULT_S3_CSV, help="S3 backfilled context CSV")
    parser.add_argument("--live-csv", default=DEFAULT_LIVE_CSV, help="Forward-collected live context CSV")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Merged context CSV output")
    parser.add_argument(
        "--prefer",
        choices=["live", "s3"],
        default="live",
        help="Which source wins when exact timestamps overlap",
    )
    args = parser.parse_args()

    s3_frame = read_context(args.s3_csv, source="s3", coin=args.coin)
    live_frame = read_context(args.live_csv, source="live", coin=args.coin)
    merged, duplicate_rows = merge_context_frames(s3_frame, live_frame, prefer=args.prefer)
    write_context(args.output, merged)
    for line in summarize(merged, len(s3_frame), len(live_frame), duplicate_rows, args.output):
        print(line, flush=True)


if __name__ == "__main__":
    main()
