"""
Analyze labeled market regimes against forward returns and adverse excursion.

Usage:
    conda run -n hummingbot python scripts/analyze_market_regimes.py \
        --input data/regimes/binance_perpetual_SOL-USDT_1h_sol_1h_v1_5y.csv
"""
import argparse
import math
import os
from typing import List

import pandas as pd


def parse_horizons(value: str) -> List[int]:
    return [int(item.strip()) for item in value.split(",") if item.strip()]


def add_forward_metrics(df: pd.DataFrame, horizons: List[int]) -> pd.DataFrame:
    df = df.copy().sort_values("timestamp").reset_index(drop=True)
    for horizon in horizons:
        future_close = df["close"].shift(-horizon)
        future_high = df["high"].shift(-1)[::-1].rolling(horizon, min_periods=1).max()[::-1]
        future_low = df["low"].shift(-1)[::-1].rolling(horizon, min_periods=1).min()[::-1]
        valid_window = future_close.notna()

        df[f"ret_{horizon}h"] = future_close / df["close"] - 1
        df[f"long_mfe_{horizon}h"] = future_high / df["close"] - 1
        df[f"long_mae_{horizon}h"] = future_low / df["close"] - 1
        df[f"short_mfe_{horizon}h"] = df["close"] / future_low - 1
        df[f"short_mae_{horizon}h"] = df["close"] / future_high - 1
        df[f"policy_ret_{horizon}h"] = pd.NA
        df.loc[df["allow_longs"] & ~df["allow_shorts"], f"policy_ret_{horizon}h"] = df[f"ret_{horizon}h"]
        df.loc[df["allow_shorts"] & ~df["allow_longs"], f"policy_ret_{horizon}h"] = -df[f"ret_{horizon}h"]
        forward_columns = [
            f"ret_{horizon}h",
            f"long_mfe_{horizon}h",
            f"long_mae_{horizon}h",
            f"short_mfe_{horizon}h",
            f"short_mae_{horizon}h",
            f"policy_ret_{horizon}h",
        ]
        df.loc[~valid_window, forward_columns] = pd.NA
    return df


def summarize_by_label(df: pd.DataFrame, horizons: List[int]) -> pd.DataFrame:
    rows = []
    for label, group in df.groupby("regime_label"):
        row = {
            "regime_label": label,
            "rows": len(group),
            "risk_enabled_pct": (group["risk_multiplier"] > 0).mean() * 100,
            "avg_risk_multiplier": group["risk_multiplier"].mean(),
        }
        for horizon in horizons:
            returns = group[f"ret_{horizon}h"].dropna()
            policy_returns = group[f"policy_ret_{horizon}h"].dropna()
            row[f"mean_ret_{horizon}h_pct"] = returns.mean() * 100
            row[f"median_ret_{horizon}h_pct"] = returns.median() * 100
            row[f"positive_ret_{horizon}h_pct"] = (returns > 0).mean() * 100
            row[f"policy_mean_ret_{horizon}h_pct"] = policy_returns.mean() * 100
            row[f"policy_positive_ret_{horizon}h_pct"] = (policy_returns > 0).mean() * 100
            row[f"long_mae_p05_{horizon}h_pct"] = group[f"long_mae_{horizon}h"].quantile(0.05) * 100
            row[f"short_mae_p05_{horizon}h_pct"] = group[f"short_mae_{horizon}h"].quantile(0.05) * 100
        rows.append(row)
    return pd.DataFrame(rows).sort_values("rows", ascending=False)


def summarize_modifiers(df: pd.DataFrame, horizons: List[int]) -> pd.DataFrame:
    rows = []
    exploded = df.copy()
    exploded["modifier"] = exploded["modifiers"].fillna("").str.split(",")
    exploded = exploded.explode("modifier")
    exploded = exploded[exploded["modifier"] != ""]
    for modifier, group in exploded.groupby("modifier"):
        row = {
            "modifier": modifier,
            "rows": len(group),
        }
        for horizon in horizons:
            policy_returns = group[f"policy_ret_{horizon}h"].dropna()
            row[f"policy_mean_ret_{horizon}h_pct"] = policy_returns.mean() * 100
            row[f"policy_positive_ret_{horizon}h_pct"] = (policy_returns > 0).mean() * 100
        rows.append(row)
    return pd.DataFrame(rows).sort_values("rows", ascending=False)


def summarize_sides(df: pd.DataFrame, horizons: List[int]) -> pd.DataFrame:
    rows = []
    for label, group in df.groupby("regime_label"):
        row = {
            "regime_label": label,
            "rows": len(group),
        }
        for horizon in horizons:
            returns = group[f"ret_{horizon}h"].dropna()
            long_mae = group[f"long_mae_{horizon}h"].dropna()
            short_mae = group[f"short_mae_{horizon}h"].dropna()
            long_mfe = group[f"long_mfe_{horizon}h"].dropna()
            short_mfe = group[f"short_mfe_{horizon}h"].dropna()

            long_mean = returns.mean() * 100
            short_mean = -returns.mean() * 100
            row[f"long_mean_ret_{horizon}h_pct"] = long_mean
            row[f"long_positive_{horizon}h_pct"] = (returns > 0).mean() * 100
            row[f"long_avg_mfe_{horizon}h_pct"] = long_mfe.mean() * 100
            row[f"long_mae_p05_{horizon}h_pct"] = long_mae.quantile(0.05) * 100
            row[f"short_mean_ret_{horizon}h_pct"] = short_mean
            row[f"short_positive_{horizon}h_pct"] = (returns < 0).mean() * 100
            row[f"short_avg_mfe_{horizon}h_pct"] = short_mfe.mean() * 100
            row[f"short_mae_p05_{horizon}h_pct"] = short_mae.quantile(0.05) * 100
            if math.isnan(long_mean) or math.isnan(short_mean):
                row[f"best_side_{horizon}h"] = "unknown"
            elif long_mean > short_mean:
                row[f"best_side_{horizon}h"] = "long"
            elif short_mean > long_mean:
                row[f"best_side_{horizon}h"] = "short"
            else:
                row[f"best_side_{horizon}h"] = "flat"
        rows.append(row)
    return pd.DataFrame(rows).sort_values("rows", ascending=False)


def print_compact_summary(
    label_summary: pd.DataFrame,
    modifier_summary: pd.DataFrame,
    side_summary: pd.DataFrame,
    horizon: int,
):
    label_columns = [
        "regime_label",
        "rows",
        "risk_enabled_pct",
        f"mean_ret_{horizon}h_pct",
        f"positive_ret_{horizon}h_pct",
        f"policy_mean_ret_{horizon}h_pct",
        f"policy_positive_ret_{horizon}h_pct",
        f"long_mae_p05_{horizon}h_pct",
        f"short_mae_p05_{horizon}h_pct",
    ]
    print(f"\nRegime outcome summary ({horizon}h)")
    print(label_summary[label_columns].to_string(index=False, float_format=lambda value: f"{value:8.3f}"))

    if not modifier_summary.empty:
        modifier_columns = [
            "modifier",
            "rows",
            f"policy_mean_ret_{horizon}h_pct",
            f"policy_positive_ret_{horizon}h_pct",
        ]
        print(f"\nModifier policy summary ({horizon}h)")
        print(modifier_summary[modifier_columns].to_string(index=False, float_format=lambda value: f"{value:8.3f}"))

    side_columns = [
        "regime_label",
        f"best_side_{horizon}h",
        f"long_mean_ret_{horizon}h_pct",
        f"long_positive_{horizon}h_pct",
        f"long_avg_mfe_{horizon}h_pct",
        f"long_mae_p05_{horizon}h_pct",
        f"short_mean_ret_{horizon}h_pct",
        f"short_positive_{horizon}h_pct",
        f"short_avg_mfe_{horizon}h_pct",
        f"short_mae_p05_{horizon}h_pct",
    ]
    print(f"\nLong/short opportunity summary ({horizon}h)")
    print(side_summary[side_columns].to_string(index=False, float_format=lambda value: f"{value:8.3f}"))


def main():
    parser = argparse.ArgumentParser(description="Analyze regime labels against forward returns")
    parser.add_argument("--input", required=True, help="Labeled regime CSV")
    parser.add_argument("--horizons", default="6,12,24,72", help="Comma-separated forward horizons in rows/hours")
    parser.add_argument("--output-dir", default=None, help="Optional directory for summary CSVs")
    parser.add_argument("--print-horizon", type=int, default=24, help="Horizon to print in compact console summary")
    args = parser.parse_args()

    horizons = parse_horizons(args.horizons)
    df = pd.read_csv(args.input)
    for column in ["timestamp", "open", "high", "low", "close", "volume"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df.dropna(subset=["timestamp", "open", "high", "low", "close", "volume"])
    labeled = add_forward_metrics(df, horizons)
    label_summary = summarize_by_label(labeled, horizons)
    modifier_summary = summarize_modifiers(labeled, horizons)
    side_summary = summarize_sides(labeled, horizons)

    if args.output_dir:
        os.makedirs(args.output_dir, exist_ok=True)
        base_name = os.path.splitext(os.path.basename(args.input))[0]
        label_summary.to_csv(os.path.join(args.output_dir, f"{base_name}_label_outcomes.csv"), index=False)
        modifier_summary.to_csv(os.path.join(args.output_dir, f"{base_name}_modifier_outcomes.csv"), index=False)
        side_summary.to_csv(os.path.join(args.output_dir, f"{base_name}_side_outcomes.csv"), index=False)

    print(f"Input: {args.input}")
    print(f"Rows: {len(labeled)}")
    print(f"Horizons: {horizons}")
    print_compact_summary(label_summary, modifier_summary, side_summary, args.print_horizon)


if __name__ == "__main__":
    main()
