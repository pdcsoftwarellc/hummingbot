"""
Forward collect Hyperliquid public trades into 1-minute flow features.

Hyperliquid's requester-pays S3 archive does not appear to expose historical
trade files alongside l2Book snapshots. This collector fills true trade-flow
features from its start time forward.

Usage:
    conda run -n hummingbot python scripts/collect_hyperliquid_trades.py --coin SOL
"""
import argparse
import asyncio
import csv
import os
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional, Set

import aiohttp


WS_URL = "wss://api.hyperliquid.xyz/ws"
DEFAULT_OUTPUT = "data/microstructure/hyperliquid_SOL_trades_1m.csv"
FIELDNAMES = [
    "timestamp",
    "iso_time",
    "coin",
    "trade_count",
    "buy_count",
    "sell_count",
    "buy_base_volume",
    "sell_base_volume",
    "buy_quote_volume",
    "sell_quote_volume",
    "net_base_volume",
    "net_quote_volume",
    "taker_buy_ratio",
    "vwap",
    "first_price",
    "last_price",
    "high_price",
    "low_price",
    "cvd_base",
    "cvd_quote",
]


@dataclass
class MinuteTradeBucket:
    timestamp: int
    coin: str
    trade_count: int = 0
    buy_count: int = 0
    sell_count: int = 0
    buy_base_volume: float = 0.0
    sell_base_volume: float = 0.0
    buy_quote_volume: float = 0.0
    sell_quote_volume: float = 0.0
    first_price: Optional[float] = None
    last_price: Optional[float] = None
    high_price: Optional[float] = None
    low_price: Optional[float] = None

    def add_trade(self, price: float, size: float, aggressive_buy: bool):
        quote = price * size
        self.trade_count += 1
        if aggressive_buy:
            self.buy_count += 1
            self.buy_base_volume += size
            self.buy_quote_volume += quote
        else:
            self.sell_count += 1
            self.sell_base_volume += size
            self.sell_quote_volume += quote
        if self.first_price is None:
            self.first_price = price
        self.last_price = price
        self.high_price = price if self.high_price is None else max(self.high_price, price)
        self.low_price = price if self.low_price is None else min(self.low_price, price)

    def row(self, cvd_base: float, cvd_quote: float) -> Dict:
        total_base = self.buy_base_volume + self.sell_base_volume
        total_quote = self.buy_quote_volume + self.sell_quote_volume
        return {
            "timestamp": self.timestamp,
            "iso_time": datetime.fromtimestamp(self.timestamp, tz=timezone.utc).isoformat(),
            "coin": self.coin,
            "trade_count": self.trade_count,
            "buy_count": self.buy_count,
            "sell_count": self.sell_count,
            "buy_base_volume": self.buy_base_volume,
            "sell_base_volume": self.sell_base_volume,
            "buy_quote_volume": self.buy_quote_volume,
            "sell_quote_volume": self.sell_quote_volume,
            "net_base_volume": self.buy_base_volume - self.sell_base_volume,
            "net_quote_volume": self.buy_quote_volume - self.sell_quote_volume,
            "taker_buy_ratio": self.buy_base_volume / total_base if total_base else None,
            "vwap": total_quote / total_base if total_base else None,
            "first_price": self.first_price,
            "last_price": self.last_price,
            "high_price": self.high_price,
            "low_price": self.low_price,
            "cvd_base": cvd_base,
            "cvd_quote": cvd_quote,
        }


def as_float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def minute_timestamp(timestamp_ms: int) -> int:
    return int(timestamp_ms // 1000 // 60 * 60)


def read_last_cvd(path: str, coin: str) -> tuple[float, float]:
    if not os.path.exists(path):
        return 0.0, 0.0
    try:
        with open(path, newline="") as file:
            rows = list(csv.DictReader(file))
    except (OSError, csv.Error):
        return 0.0, 0.0
    for row in reversed(rows[-500:]):
        if row.get("coin") != coin:
            continue
        return as_float(row.get("cvd_base")) or 0.0, as_float(row.get("cvd_quote")) or 0.0
    return 0.0, 0.0


def append_row(path: str, row: Dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_header = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def trade_id(trade: Dict) -> str:
    return str(trade.get("hash") or f"{trade.get('time')}:{trade.get('px')}:{trade.get('sz')}:{trade.get('side')}")


class SeenTradeIds:
    def __init__(self, maxlen: int = 50_000):
        self._ids: Set[str] = set()
        self._order = deque(maxlen=maxlen)

    def add(self, value: str) -> bool:
        if value in self._ids:
            return False
        if len(self._order) == self._order.maxlen:
            old = self._order.popleft()
            self._ids.discard(old)
        self._order.append(value)
        self._ids.add(value)
        return True


async def subscribe_and_collect(coin: str, output_path: str, flush_delay_seconds: int, duration_seconds: Optional[int]):
    cvd_base, cvd_quote = read_last_cvd(output_path, coin)
    seen = SeenTradeIds()
    buckets: Dict[int, MinuteTradeBucket] = {}
    started = time.time()
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(WS_URL, heartbeat=30) as ws:
            await ws.send_json({"method": "subscribe", "subscription": {"type": "trades", "coin": coin}})
            async for message in ws:
                if duration_seconds is not None and time.time() - started >= duration_seconds:
                    break
                if message.type == aiohttp.WSMsgType.ERROR:
                    raise RuntimeError(f"websocket error: {ws.exception()}")
                if message.type != aiohttp.WSMsgType.TEXT:
                    continue
                payload = message.json()
                if payload.get("channel") != "trades":
                    continue
                now_minute = minute_timestamp(int(time.time() * 1000))
                for trade in payload.get("data", []):
                    if trade.get("coin") != coin:
                        continue
                    if not seen.add(trade_id(trade)):
                        continue
                    price = as_float(trade.get("px"))
                    size = as_float(trade.get("sz"))
                    timestamp_ms = trade.get("time")
                    if price is None or size is None or timestamp_ms is None:
                        continue
                    aggressive_buy = trade.get("side") == "B"
                    bucket = buckets.setdefault(
                        minute_timestamp(int(timestamp_ms)),
                        MinuteTradeBucket(timestamp=minute_timestamp(int(timestamp_ms)), coin=coin),
                    )
                    bucket.add_trade(price=price, size=size, aggressive_buy=aggressive_buy)

                flush_before = now_minute - flush_delay_seconds
                for minute in sorted(list(buckets)):
                    if minute >= flush_before:
                        continue
                    bucket = buckets.pop(minute)
                    cvd_base += bucket.buy_base_volume - bucket.sell_base_volume
                    cvd_quote += bucket.buy_quote_volume - bucket.sell_quote_volume
                    row = bucket.row(cvd_base=cvd_base, cvd_quote=cvd_quote)
                    append_row(output_path, row)
                    print(
                        f"{row['iso_time']} {coin} trades={row['trade_count']} "
                        f"net_base={row['net_base_volume']:.4f} cvd_base={row['cvd_base']:.4f}",
                        flush=True,
                    )

    for minute in sorted(buckets):
        bucket = buckets[minute]
        cvd_base += bucket.buy_base_volume - bucket.sell_base_volume
        cvd_quote += bucket.buy_quote_volume - bucket.sell_quote_volume
        append_row(output_path, bucket.row(cvd_base=cvd_base, cvd_quote=cvd_quote))


async def main_async():
    parser = argparse.ArgumentParser(description="Forward collect Hyperliquid public trade-flow features")
    parser.add_argument("--coin", default="SOL", help="Hyperliquid coin symbol")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="CSV output path")
    parser.add_argument("--flush-delay-seconds", type=int, default=60, help="Delay before closing a minute bucket")
    parser.add_argument("--duration-seconds", type=int, default=None, help="Optional test duration before exit")
    args = parser.parse_args()

    while True:
        try:
            await subscribe_and_collect(
                coin=args.coin,
                output_path=args.output,
                flush_delay_seconds=args.flush_delay_seconds,
                duration_seconds=args.duration_seconds,
            )
            if args.duration_seconds is not None:
                return
        except Exception as exc:
            print(f"{datetime.now(timezone.utc).isoformat()} collect failed: {exc}", flush=True)
            await asyncio.sleep(5)


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
