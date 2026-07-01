"""
Label enriched market-feature CSVs with reusable signal reports.

Usage:
    conda run -n hummingbot python scripts/label_market_signals.py \
        --input data/regimes/binance_perpetual_SOL-USDT_1h_sol_1h_5y_hl_context_features.csv \
        --output data/regimes/binance_perpetual_SOL-USDT_1h_sol_1h_5y_hl_context_signals.csv
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hummingbot.strategy_v2.utils.market_signals import MarketSignalConfig, MarketSignalDetector  # noqa: E402


def epoch_to_utc(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def label_market_signals(frame: pd.DataFrame, detector: MarketSignalDetector) -> pd.DataFrame:
    rows = []
    for _, row in frame.iterrows():
        report = detector.evaluate(row.to_dict())
        rows.append({
            "market_signals": ",".join(signal.value for signal in report.signals),
            "long_signal_score": report.long_score,
            "short_signal_score": report.short_score,
            "risk_off_signal": report.risk_off,
            "signal_scores": json.dumps({signal.value: score for signal, score in report.scores.items()}, sort_keys=True),
            "signal_reasons": json.dumps({signal.value: reason for signal, reason in report.reasons.items()}, sort_keys=True),
        })
    return pd.concat([frame.reset_index(drop=True), pd.DataFrame(rows)], axis=1)


def parse_args():
    parser = argparse.ArgumentParser(description="Label enriched market features with named signals")
    parser.add_argument("--input", required=True, help="Input enriched feature CSV")
    parser.add_argument("--output", required=True, help="Output signal-labeled CSV")
    parser.add_argument("--min-continuation-confirmations", type=int, default=6)
    parser.add_argument("--min-squeeze-confirmations", type=int, default=4)
    parser.add_argument("--min-signal-score", type=float, default=0.65)
    parser.add_argument("--min-volume-expansion", type=float, default=1.1)
    parser.add_argument("--min-taker-imbalance", type=float, default=0.02)
    parser.add_argument("--funding-extreme-rate", type=float, default=0.001)
    parser.add_argument("--min-liquidity-score", type=float, default=0.5)
    parser.add_argument("--max-spread-bps", type=float, default=10.0)
    parser.add_argument("--high-atr-pct", type=float, default=0.035)
    parser.add_argument("--high-realized-vol", type=float, default=0.04)
    return parser.parse_args()


def main():
    args = parse_args()
    frame = pd.read_csv(args.input, low_memory=False)
    config = MarketSignalConfig(
        min_continuation_confirmations=args.min_continuation_confirmations,
        min_squeeze_confirmations=args.min_squeeze_confirmations,
        min_signal_score=args.min_signal_score,
        min_volume_expansion=args.min_volume_expansion,
        min_taker_imbalance=args.min_taker_imbalance,
        funding_extreme_rate=args.funding_extreme_rate,
        min_liquidity_score=args.min_liquidity_score,
        max_spread_bps=args.max_spread_bps,
        high_atr_pct=args.high_atr_pct,
        high_realized_vol=args.high_realized_vol,
    )
    labeled = label_market_signals(frame, MarketSignalDetector(config))
    output_dir = os.path.dirname(args.output)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    labeled.to_csv(args.output, index=False)

    first_ts = int(labeled["timestamp"].iloc[0]) if "timestamp" in labeled.columns else None
    last_ts = int(labeled["timestamp"].iloc[-1]) if "timestamp" in labeled.columns else None
    signal_counts = (
        labeled["market_signals"]
        .fillna("")
        .str.split(",")
        .explode()
    )
    signal_counts = signal_counts[signal_counts != ""].value_counts()
    print("Market signal labeling complete:")
    print(f"  Rows:  {len(labeled)}")
    if first_ts is not None and last_ts is not None:
        print(f"  First: {epoch_to_utc(first_ts)}")
        print(f"  Last:  {epoch_to_utc(last_ts)}")
    print(f"  CSV:   {args.output}")
    if not signal_counts.empty:
        print("\nSignal counts:")
        for signal, count in signal_counts.items():
            print(f"  {signal:28s} {count:6d} ({count / len(labeled):6.2%})")


if __name__ == "__main__":
    main()
