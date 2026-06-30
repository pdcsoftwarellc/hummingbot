"""
Forward collect Hyperliquid market context for regime detection.

Example:
    conda run -n hummingbot python scripts/collect_hyperliquid_context.py --coin SOL --once
"""
import argparse
import csv
import json
import os
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from urllib.request import Request, urlopen


INFO_URL = "https://api.hyperliquid.xyz/info"
DEFAULT_OUTPUT = "data/context/hyperliquid_SOL_context.csv"
FIELDNAMES = [
    "timestamp",
    "iso_time",
    "coin",
    "funding_rate",
    "premium",
    "open_interest",
    "open_interest_change_pct",
    "mark_price",
    "oracle_price",
    "mid_price",
    "prev_day_price",
    "day_ntl_volume",
    "day_base_volume",
    "impact_bid_price",
    "impact_ask_price",
    "best_bid",
    "best_ask",
    "spread_pct",
    "bid_depth_usd",
    "ask_depth_usd",
    "depth_usd",
    "book_time_ms",
]


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


def as_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def asset_context(coin: str) -> Dict:
    response = post_info({"type": "metaAndAssetCtxs"})
    universe, contexts = response
    for index, asset in enumerate(universe["universe"]):
        if asset["name"] == coin:
            return contexts[index]
    raise ValueError(f"coin not found in metaAndAssetCtxs: {coin}")


def l2_book(coin: str) -> Dict:
    return post_info({"type": "l2Book", "coin": coin})


def depth_usd(levels: List[Dict], mid_price: float, side: str, depth_bps: float) -> float:
    if mid_price <= 0:
        return 0.0
    if side == "bid":
        min_price = mid_price * (1 - depth_bps / 10_000)
        eligible = [level for level in levels if as_float(level.get("px")) is not None and as_float(level.get("px")) >= min_price]
    else:
        max_price = mid_price * (1 + depth_bps / 10_000)
        eligible = [level for level in levels if as_float(level.get("px")) is not None and as_float(level.get("px")) <= max_price]
    total = 0.0
    for level in eligible:
        price = as_float(level.get("px")) or 0.0
        size = as_float(level.get("sz")) or 0.0
        total += price * size
    return total


def read_last_open_interest(path: str, coin: str) -> Optional[float]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, newline="") as file:
            rows = list(csv.DictReader(file))
    except (OSError, csv.Error):
        return None
    for row in reversed(rows[-500:]):
        if row.get("coin") != coin:
            continue
        open_interest = as_float(row.get("open_interest"))
        if open_interest is not None:
            return open_interest
    return None


def context_row(coin: str, output_path: str, depth_bps: float) -> Dict:
    ctx = asset_context(coin)
    book = l2_book(coin)
    bids, asks = book["levels"]
    best_bid = as_float(bids[0]["px"]) if bids else None
    best_ask = as_float(asks[0]["px"]) if asks else None
    mid_price = as_float(ctx.get("midPx"))
    if mid_price is None and best_bid is not None and best_ask is not None:
        mid_price = (best_bid + best_ask) / 2

    spread_pct = None
    if best_bid is not None and best_ask is not None and mid_price and mid_price > 0:
        spread_pct = (best_ask - best_bid) / mid_price

    bid_depth = depth_usd(bids, mid_price or 0.0, "bid", depth_bps)
    ask_depth = depth_usd(asks, mid_price or 0.0, "ask", depth_bps)
    open_interest = as_float(ctx.get("openInterest"))
    previous_open_interest = read_last_open_interest(output_path, coin)
    open_interest_change_pct = None
    if open_interest is not None and previous_open_interest and previous_open_interest > 0:
        open_interest_change_pct = open_interest / previous_open_interest - 1

    impact_prices = ctx.get("impactPxs") or [None, None]
    timestamp = int((book.get("time") or time.time() * 1000) / 1000)
    return {
        "timestamp": timestamp,
        "iso_time": datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(),
        "coin": coin,
        "funding_rate": as_float(ctx.get("funding")),
        "premium": as_float(ctx.get("premium")),
        "open_interest": open_interest,
        "open_interest_change_pct": open_interest_change_pct,
        "mark_price": as_float(ctx.get("markPx")),
        "oracle_price": as_float(ctx.get("oraclePx")),
        "mid_price": mid_price,
        "prev_day_price": as_float(ctx.get("prevDayPx")),
        "day_ntl_volume": as_float(ctx.get("dayNtlVlm")),
        "day_base_volume": as_float(ctx.get("dayBaseVlm")),
        "impact_bid_price": as_float(impact_prices[0]),
        "impact_ask_price": as_float(impact_prices[1]),
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread_pct": spread_pct,
        "bid_depth_usd": bid_depth,
        "ask_depth_usd": ask_depth,
        "depth_usd": bid_depth + ask_depth,
        "book_time_ms": book.get("time"),
    }


def append_row(path: str, row: Dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_header = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def collect_once(coin: str, output_path: str, depth_bps: float) -> Dict:
    row = context_row(coin=coin, output_path=output_path, depth_bps=depth_bps)
    append_row(output_path, row)
    return row


def main():
    parser = argparse.ArgumentParser(description="Forward collect Hyperliquid market context")
    parser.add_argument("--coin", default="SOL", help="Hyperliquid coin symbol")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="CSV output path")
    parser.add_argument("--interval-seconds", type=float, default=60.0, help="Polling interval for loop mode")
    parser.add_argument("--depth-bps", type=float, default=10.0, help="Book depth window around mid price")
    parser.add_argument("--once", action="store_true", help="Collect one row and exit")
    args = parser.parse_args()

    while True:
        try:
            row = collect_once(args.coin, args.output, args.depth_bps)
            print(
                f"{row['iso_time']} {args.coin} "
                f"funding={row['funding_rate']} spread={row['spread_pct']} depth={row['depth_usd']}",
                flush=True,
            )
        except Exception as exc:
            print(f"{datetime.now(timezone.utc).isoformat()} collect failed: {exc}", flush=True)
            if args.once:
                raise
        if args.once:
            return
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
