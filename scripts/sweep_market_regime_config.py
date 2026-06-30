"""
Sweep SOL market-regime map thresholds against a labeled feature CSV.

This is a fast calibration pass: it reuses feature columns emitted by
scripts/backfill_market_regimes.py and reapplies candidate thresholds without
refetching candles or recomputing rolling windows.
"""
import argparse
import itertools
import json
import os
import sys
from typing import Dict, Iterable, List, Tuple

import pandas as pd
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.backfill_market_regimes import load_regime_config  # noqa: E402


POLICIES = {
    "range_chop": ("neutral", True, True, 1.0),
    "uptrend": ("long", True, False, 1.0),
    "downtrend": ("short", False, True, 1.0),
    "breakout": ("long", True, False, 0.7),
    "breakdown": ("short", False, True, 0.7),
    "squeeze_risk": ("none", False, False, 0.35),
    "high_volatility_danger": ("none", False, False, 0.25),
    "no_trade": ("none", False, False, 0.0),
}


def parse_values(value: str) -> List[float]:
    return [float(item.strip()) for item in value.split(",") if item.strip()]


def parse_int_values(value: str) -> List[int]:
    return [int(item.strip()) for item in value.split(",") if item.strip()]


def add_forward_returns(df: pd.DataFrame, horizons: Iterable[int]) -> pd.DataFrame:
    df = df.copy().sort_values("timestamp").reset_index(drop=True)
    for horizon in horizons:
        df[f"ret_{horizon}h"] = df["close"].shift(-horizon) / df["close"] - 1
    return df


def classify_from_features(df: pd.DataFrame, params: Dict[str, float]) -> pd.Series:
    labels = pd.Series("no_trade", index=df.index)

    liquidity_bad = df["liquidity_score"] < params["min_liquidity_score"]
    high_vol = (
        (df["atr_pct"] >= params["high_vol_atr_pct"]) |
        (df["vol_ratio"] >= params["high_vol_multiplier"])
    )
    squeeze = (
        (df["crowding_score"] >= params["squeeze_crowding_threshold"]) |
        (df["nearest_liquidation_distance_pct"] <= params["squeeze_liquidation_distance_pct"]) |
        (df["liquidation_pressure_score"] >= params["squeeze_crowding_threshold"])
    )
    balanced_prior_range = (
        (df["range_width_pct"] <= params["max_chop_range_width_pct"]) &
        (df["boundary_touch_count"] >= params["min_boundary_touches"]) &
        (df["prior_trend_slope_pct"].abs() <= params["max_balanced_range_slope_pct"])
    )
    accepted_above = (
        (df["raw_accepted_above_range"] > 0) &
        balanced_prior_range &
        (df["breakout_distance_pct"] >= params["min_breakout_distance_pct"])
    )
    accepted_below = (
        (df["raw_accepted_below_range"] > 0) &
        balanced_prior_range &
        (df["breakdown_distance_pct"] >= params["min_breakout_distance_pct"])
    )
    uptrend = (
        (df["higher_highs"] > 0) &
        (df["higher_lows"] > 0) &
        (df["trend_slope_pct"] > params["min_trend_slope_pct"]) &
        (df["confidence"] >= params["min_directional_confidence"])
    )
    downtrend = (
        (df["lower_highs"] > 0) &
        (df["lower_lows"] > 0) &
        (df["trend_slope_pct"] < -params["min_trend_slope_pct"]) &
        (df["confidence"] >= params["min_directional_confidence"])
    )
    range_chop = (
        (df["inside_range"] > 0) &
        (df["range_width_pct"] <= params["max_chop_range_width_pct"]) &
        (df["boundary_touch_count"] >= params["min_boundary_touches"])
    )

    eligible = ~liquidity_bad
    labels.loc[eligible & high_vol] = "high_volatility_danger"
    eligible &= ~high_vol
    labels.loc[eligible & squeeze] = "squeeze_risk"
    eligible &= ~squeeze
    labels.loc[eligible & accepted_above] = "breakout"
    eligible &= ~accepted_above
    labels.loc[eligible & accepted_below] = "breakdown"
    eligible &= ~accepted_below
    labels.loc[eligible & uptrend] = "uptrend"
    eligible &= ~uptrend
    labels.loc[eligible & downtrend] = "downtrend"
    eligible &= ~downtrend
    labels.loc[eligible & range_chop] = "range_chop"
    return labels


def evaluate(df: pd.DataFrame, labels: pd.Series, params: Dict[str, float], horizons: List[int]) -> Dict[str, float]:
    working = df.copy()
    working["candidate_label"] = labels
    working["candidate_risk"] = 0.0
    working["candidate_long_risk"] = 0.0
    working["candidate_short_risk"] = 0.0
    for label, (_, allow_longs, allow_shorts, base_risk) in POLICIES.items():
        mask = working["candidate_label"] == label
        working.loc[mask, "candidate_risk"] = base_risk
        if allow_longs:
            working.loc[mask, "candidate_long_risk"] = base_risk
        if allow_shorts:
            working.loc[mask, "candidate_short_risk"] = base_risk

    if params["use_context_modifiers"]:
        thin = (
            (working["liquidity_score"] >= params["min_liquidity_score"]) &
            (working["liquidity_score"] < params["thin_liquidity_score"])
        )
        working.loc[thin, ["candidate_risk", "candidate_long_risk", "candidate_short_risk"]] *= params[
            "thin_liquidity_risk_multiplier"
        ]

        funding_extreme = working["funding_rate"].abs() >= params["funding_extreme_rate"]
        working.loc[funding_extreme, "candidate_risk"] *= params["funding_extreme_risk_multiplier"]
        working.loc[funding_extreme & (working["funding_rate"] > 0), "candidate_long_risk"] *= params[
            "funding_extreme_risk_multiplier"
        ]
        working.loc[funding_extreme & (working["funding_rate"] < 0), "candidate_short_risk"] *= params[
            "funding_extreme_risk_multiplier"
        ]

        failed_breakout = (working["rejected_above_range"] > 0) | (working["rejected_below_range"] > 0)
        working.loc[failed_breakout, ["candidate_risk", "candidate_long_risk", "candidate_short_risk"]] *= params[
            "failed_breakout_risk_multiplier"
        ]

        directional = working["candidate_label"].isin(["uptrend", "downtrend", "breakout", "breakdown"])
        exhausted = (
            directional &
            (
                (working["distance_from_trend_mean_atr"] >= params["trend_exhaustion_atr_multiple"]) |
                (working["trend_slope_pct"].abs() >= params["trend_exhaustion_slope_pct"])
            )
        )
        working.loc[exhausted, ["candidate_risk", "candidate_long_risk", "candidate_short_risk"]] *= params[
            "trend_exhaustion_risk_multiplier"
        ]

    row: Dict[str, float] = {
        "rows": len(working),
        "trade_enabled_pct": float((working["candidate_risk"] > 0).mean() * 100),
        "no_trade_pct": float((working["candidate_label"] == "no_trade").mean() * 100),
        "context_rows": int(working["context_available"].astype(bool).sum()),
    }
    for label in POLICIES:
        row[f"{label}_rows"] = int((working["candidate_label"] == label).sum())

    score = 0.0
    penalty = 0.0
    for horizon in horizons:
        ret_col = f"ret_{horizon}h"
        direction = pd.Series(0.0, index=working.index)
        direction.loc[working["candidate_long_risk"] > working["candidate_short_risk"]] = 1.0
        direction.loc[working["candidate_short_risk"] > working["candidate_long_risk"]] = -1.0
        policy = direction * working[ret_col] * working["candidate_risk"]
        policy = policy[(working["candidate_risk"] > 0) & policy.notna()]
        row[f"policy_mean_{horizon}h_pct"] = float(policy.mean() * 100) if not policy.empty else 0.0
        row[f"policy_positive_{horizon}h_pct"] = float((policy > 0).mean() * 100) if not policy.empty else 0.0

        for label, expected_direction in [
            ("uptrend", 1),
            ("downtrend", -1),
            ("breakout", 1),
            ("breakdown", -1),
        ]:
            group = working[working["candidate_label"] == label]
            directional = expected_direction * group[ret_col]
            row[f"{label}_mean_{horizon}h_pct"] = float(directional.mean() * 100) if not group.empty else 0.0
            row[f"{label}_positive_{horizon}h_pct"] = float((directional > 0).mean() * 100) if not group.empty else 0.0
            if horizon == 24 and len(group) >= params["min_label_rows"] and row[f"{label}_mean_{horizon}h_pct"] < 0:
                penalty += abs(row[f"{label}_mean_{horizon}h_pct"])

    score += row["policy_mean_24h_pct"] * 2.0
    score += row["policy_mean_6h_pct"]
    score += row["policy_mean_72h_pct"] * 0.5
    score += max(0.0, row["uptrend_mean_24h_pct"])
    score += max(0.0, row["downtrend_mean_24h_pct"])
    score += max(0.0, row["breakout_mean_24h_pct"]) * 0.5
    score += max(0.0, row["breakdown_mean_24h_pct"]) * 0.5
    score -= penalty * 1.5
    if row["trade_enabled_pct"] < params["min_trade_enabled_pct"]:
        score -= params["min_trade_enabled_pct"] - row["trade_enabled_pct"]
    if row["trade_enabled_pct"] > params["max_trade_enabled_pct"]:
        score -= row["trade_enabled_pct"] - params["max_trade_enabled_pct"]
    row["score"] = float(score)
    return row


def candidate_params(args: argparse.Namespace, base: Dict[str, float]) -> Iterable[Dict[str, float]]:
    grid = {
        "min_trend_slope_pct": parse_values(args.min_trend_slope_pct),
        "min_directional_confidence": parse_values(args.min_directional_confidence),
        "max_chop_range_width_pct": parse_values(args.max_chop_range_width_pct),
        "max_balanced_range_slope_pct": parse_values(args.max_balanced_range_slope_pct),
        "high_vol_atr_pct": parse_values(args.high_vol_atr_pct),
        "high_vol_multiplier": parse_values(args.high_vol_multiplier),
        "funding_extreme_rate": parse_values(args.funding_extreme_rate),
        "thin_liquidity_score": parse_values(args.thin_liquidity_score),
        "min_breakout_distance_pct": parse_values(args.min_breakout_distance_pct),
        "min_boundary_touches": parse_int_values(args.min_boundary_touches),
    }
    for values in itertools.product(*grid.values()):
        params = dict(base)
        params.update(dict(zip(grid.keys(), values)))
        params["use_context_modifiers"] = args.use_context_modifiers
        params["min_label_rows"] = args.min_label_rows
        params["min_trade_enabled_pct"] = args.min_trade_enabled_pct
        params["max_trade_enabled_pct"] = args.max_trade_enabled_pct
        yield params


def write_candidate_config(base_config_path: str, output_path: str, best: Dict[str, float]):
    model = load_regime_config(base_config_path)
    config = model.model_dump()
    for key in [
        "min_trend_slope_pct",
        "max_chop_range_width_pct",
        "max_balanced_range_slope_pct",
        "high_vol_atr_pct",
        "high_vol_multiplier",
        "funding_extreme_rate",
        "thin_liquidity_score",
        "min_boundary_touches",
    ]:
        if key in best:
            value = best[key]
            config[key] = int(value) if key == "min_boundary_touches" else float(value)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as file:
        yaml.safe_dump(config, file, sort_keys=False)


def main():
    parser = argparse.ArgumentParser(description="Sweep regime-map thresholds against existing feature CSV")
    parser.add_argument("--input", required=True, help="Regime CSV with feature columns")
    parser.add_argument("--base-config", default="scripts/regime_configs/sol_1h.yml")
    parser.add_argument("--output", default="data/regimes/analysis/sol_1h_threshold_sweep.csv")
    parser.add_argument("--candidate-config-output", default="data/regimes/analysis/sol_1h_tuned.yml")
    parser.add_argument("--horizons", default="6,24,72")
    parser.add_argument("--min-trend-slope-pct", default="0.006,0.01,0.015")
    parser.add_argument("--min-directional-confidence", default="0.0,0.55")
    parser.add_argument("--max-chop-range-width-pct", default="0.08,0.12")
    parser.add_argument("--max-balanced-range-slope-pct", default="0.03,0.05")
    parser.add_argument("--high-vol-atr-pct", default="0.025,0.03")
    parser.add_argument("--high-vol-multiplier", default="1.75,2.0")
    parser.add_argument("--funding-extreme-rate", default="0.000025,0.00005,0.001")
    parser.add_argument("--thin-liquidity-score", default="0.5,0.8")
    parser.add_argument("--min-breakout-distance-pct", default="0.0,0.002")
    parser.add_argument("--min-boundary-touches", default="2")
    parser.add_argument("--min-label-rows", type=int, default=100)
    parser.add_argument("--min-trade-enabled-pct", type=float, default=35.0)
    parser.add_argument("--max-trade-enabled-pct", type=float, default=70.0)
    parser.add_argument("--use-context-modifiers", action="store_true")
    args = parser.parse_args()

    horizons = [int(item.strip()) for item in args.horizons.split(",") if item.strip()]
    model = load_regime_config(args.base_config)
    base = model.model_dump()
    df = add_forward_returns(pd.read_csv(args.input), horizons)

    results = []
    for params in candidate_params(args, base):
        labels = classify_from_features(df, params)
        result = evaluate(df, labels, params, horizons)
        for key, value in params.items():
            if isinstance(value, (int, float, bool)):
                result[key] = value
        results.append(result)

    ranked = pd.DataFrame(results).sort_values("score", ascending=False).reset_index(drop=True)
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    ranked.to_csv(args.output, index=False)
    best = ranked.iloc[0].to_dict()
    write_candidate_config(args.base_config, args.candidate_config_output, best)

    print(f"Wrote sweep results to {args.output}")
    print(f"Wrote best candidate config to {args.candidate_config_output}")
    print("\nTop candidates:")
    columns = [
        "score",
        "policy_mean_24h_pct",
        "policy_positive_24h_pct",
        "trade_enabled_pct",
        "no_trade_pct",
        "uptrend_mean_24h_pct",
        "downtrend_mean_24h_pct",
        "breakout_mean_24h_pct",
        "breakdown_mean_24h_pct",
        "min_trend_slope_pct",
        "min_directional_confidence",
        "high_vol_atr_pct",
        "high_vol_multiplier",
        "funding_extreme_rate",
        "thin_liquidity_score",
    ]
    print(ranked[columns].head(10).to_string(index=False, float_format=lambda value: f"{value:8.4f}"))


if __name__ == "__main__":
    main()
