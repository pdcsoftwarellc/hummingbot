"""
Analyze joined research tables for strategy candidates.

Default usage:
    conda run -n hummingbot python scripts/analyze_joined_research_table.py
"""
import argparse
import os
import re
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Tuple

import numpy as np
import pandas as pd


DEFAULT_INPUT = "data/research/sol_5m_joined_research.csv"
DEFAULT_OUTPUT_DIR = "data/research/analysis"

LONG_SIGNALS = {
    "strong_long_continuation",
    "short_squeeze_risk",
    "weak_breakdown_trap",
}
SHORT_SIGNALS = {
    "strong_short_continuation",
    "long_squeeze_risk",
    "weak_breakout_trap",
}
BASE_COLUMNS = [
    "timestamp",
    "regime_label",
    "modifiers",
    "context_available",
    "l2_available",
    "market_signals",
    "long_signal_score",
    "short_signal_score",
    "risk_off_signal",
    "funding_rate_level",
    "open_interest_rising",
    "premium_level",
    "price_above_session_vwap",
    "price_above_rolling_vwap",
    "volume_expansion",
    "spread_bps",
    "l2_depth_top5_usd",
    "l2_mean_spread_pct",
]
OUTCOME_RE = re.compile(r"^(long|short)_(sl[^_]+_tp[^_]+)_h(\d+)_return$")


def epoch_to_utc(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def parse_float_list(value: str) -> List[float]:
    return [float(item.strip()) for item in value.split(",") if item.strip()]


def bool_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(False, index=frame.index)
    values = frame[column]
    if values.dtype == bool:
        return values.fillna(False)
    return values.fillna(False).astype(str).str.lower().isin({"true", "1", "yes", "y"})


def load_columns(path: str) -> Tuple[pd.DataFrame, List[Tuple[str, str, str, str]]]:
    header = pd.read_csv(path, nrows=0).columns.tolist()
    outcomes = []
    outcome_columns = []
    for column in header:
        match = OUTCOME_RE.match(column)
        if not match:
            continue
        side, stop_take, horizon = match.groups()
        exit_column = column.replace("_return", "_exit")
        if exit_column not in header:
            continue
        outcomes.append((side, stop_take, horizon, column))
        outcome_columns.extend([column, exit_column])
    usecols = [column for column in BASE_COLUMNS if column in header]
    usecols.extend(outcome_columns)
    frame = pd.read_csv(path, usecols=sorted(set(usecols)), low_memory=False)
    return frame, outcomes


def add_bucket_columns(frame: pd.DataFrame) -> pd.DataFrame:
    df = frame.copy()
    df["year"] = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.year
    df["regime_label"] = df["regime_label"].fillna("unknown")
    df["context_available"] = bool_series(df, "context_available")
    df["l2_available"] = bool_series(df, "l2_available")
    df["risk_off_signal"] = bool_series(df, "risk_off_signal")
    df["open_interest_rising"] = bool_series(df, "open_interest_rising")
    df["price_above_session_vwap"] = bool_series(df, "price_above_session_vwap")
    df["price_above_rolling_vwap"] = bool_series(df, "price_above_rolling_vwap")

    funding = pd.to_numeric(df.get("funding_rate_level"), errors="coerce")
    premium = pd.to_numeric(df.get("premium_level"), errors="coerce")
    volume_expansion = pd.to_numeric(df.get("volume_expansion"), errors="coerce")
    l2_depth = pd.to_numeric(df.get("l2_depth_top5_usd"), errors="coerce")
    spread_bps = pd.to_numeric(df.get("spread_bps"), errors="coerce")

    df["funding_bucket"] = np.select(
        [funding <= -0.001, funding < 0, funding > 0.001, funding > 0],
        ["neg_extreme", "negative", "pos_extreme", "positive"],
        default="missing_or_flat",
    )
    df["premium_bucket"] = np.select(
        [premium < 0, premium > 0],
        ["negative", "positive"],
        default="missing_or_flat",
    )
    df["volume_bucket"] = np.select(
        [volume_expansion >= 2.0, volume_expansion >= 1.25, volume_expansion.notna()],
        ["high_expansion", "expanding", "normal"],
        default="missing",
    )
    df["l2_depth_bucket"] = np.select(
        [~df["l2_available"], l2_depth >= 500_000, l2_depth >= 150_000, l2_depth.notna()],
        ["missing", "deep", "normal", "thin"],
        default="missing",
    )
    df["spread_bucket"] = np.select(
        [spread_bps <= 2, spread_bps <= 10, spread_bps.notna()],
        ["tight", "normal", "wide"],
        default="missing",
    )
    df["vwap_alignment"] = np.select(
        [df["price_above_session_vwap"] & df["price_above_rolling_vwap"],
         ~df["price_above_session_vwap"] & ~df["price_above_rolling_vwap"]],
        ["above_both", "below_both"],
        default="mixed",
    )
    return df


def explode_signals(frame: pd.DataFrame, score_floors: Iterable[float]) -> pd.DataFrame:
    rows = frame.copy()
    rows["signal"] = rows["market_signals"].fillna("").astype(str).str.split(",")
    rows = rows.explode("signal")
    rows["signal"] = rows["signal"].fillna("").str.strip()
    rows = rows[rows["signal"] != ""].copy()

    long_rows = rows[rows["signal"].isin(LONG_SIGNALS)].copy()
    long_rows["side"] = "long"
    long_rows["side_score"] = pd.to_numeric(long_rows["long_signal_score"], errors="coerce")

    short_rows = rows[rows["signal"].isin(SHORT_SIGNALS)].copy()
    short_rows["side"] = "short"
    short_rows["side_score"] = pd.to_numeric(short_rows["short_signal_score"], errors="coerce")

    sided = pd.concat([long_rows, short_rows], ignore_index=True)
    scored_frames = []
    for floor in score_floors:
        scored = sided[sided["side_score"] >= floor].copy()
        scored["score_floor"] = floor
        scored_frames.append(scored)
    if not scored_frames:
        return pd.DataFrame(columns=[*sided.columns, "score_floor"])
    return pd.concat(scored_frames, ignore_index=True)


def explode_modifiers(frame: pd.DataFrame) -> pd.DataFrame:
    df = frame.copy()
    df["modifier"] = df["modifiers"].fillna("").astype(str)
    df.loc[df["modifier"].str.strip() == "", "modifier"] = "none"
    df["modifier"] = df["modifier"].str.split(",")
    df = df.explode("modifier")
    df["modifier"] = df["modifier"].fillna("none").str.strip()
    df.loc[df["modifier"] == "", "modifier"] = "none"
    return df


def outcome_metadata(outcome_column: str) -> Dict[str, str]:
    match = OUTCOME_RE.match(outcome_column)
    if not match:
        raise ValueError(f"Unsupported outcome column: {outcome_column}")
    side, stop_take, horizon = match.groups()
    return {
        "side": side,
        "stop_take": stop_take,
        "horizon_bars": horizon,
        "exit_column": outcome_column.replace("_return", "_exit"),
    }


def aggregate_slice(
    frame: pd.DataFrame,
    group_columns: List[str],
    outcome_column: str,
    leverage: float,
    fee_rate: float,
    min_samples: int,
) -> pd.DataFrame:
    meta = outcome_metadata(outcome_column)
    side_frame = frame[frame["side"] == meta["side"]].copy()
    if side_frame.empty:
        return pd.DataFrame()

    returns = pd.to_numeric(side_frame[outcome_column], errors="coerce")
    side_frame = side_frame[returns.notna()].copy()
    returns = pd.to_numeric(side_frame[outcome_column], errors="coerce")
    adjusted_margin_return = returns * leverage - (2 * fee_rate * leverage)
    side_frame["_price_return"] = returns
    side_frame["_margin_return"] = adjusted_margin_return
    side_frame["_win"] = adjusted_margin_return > 0
    side_frame["_gross_profit"] = adjusted_margin_return.clip(lower=0)
    side_frame["_gross_loss"] = -adjusted_margin_return.clip(upper=0)
    exit_column = meta["exit_column"]
    side_frame["_take"] = side_frame[exit_column].eq("take") if exit_column in side_frame.columns else False
    side_frame["_stop"] = side_frame[exit_column].eq("stop") if exit_column in side_frame.columns else False
    side_frame["_timeout"] = side_frame[exit_column].eq("timeout") if exit_column in side_frame.columns else False

    grouped = side_frame.groupby(group_columns, dropna=False)
    result = grouped.agg(
        samples=("_margin_return", "size"),
        avg_price_return=("_price_return", "mean"),
        avg_margin_return=("_margin_return", "mean"),
        median_margin_return=("_margin_return", "median"),
        win_rate=("_win", "mean"),
        gross_profit=("_gross_profit", "sum"),
        gross_loss=("_gross_loss", "sum"),
        take_rate=("_take", "mean"),
        stop_rate=("_stop", "mean"),
        timeout_rate=("_timeout", "mean"),
        avg_side_score=("side_score", "mean"),
    ).reset_index()
    result = result[result["samples"] >= min_samples]
    if result.empty:
        return result

    year_avg = side_frame.groupby([*group_columns, "year"], dropna=False)["_margin_return"].mean()
    year_stats = year_avg.reset_index().groupby(group_columns, dropna=False).agg(
        years=("year", "size"),
        positive_years=("_margin_return", lambda values: int((values > 0).sum())),
    ).reset_index()
    result = result.merge(year_stats, on=group_columns, how="left")
    result["profit_factor"] = result["gross_profit"] / result["gross_loss"].replace(0, np.nan)
    result["profit_factor"] = result["profit_factor"].replace([np.inf, -np.inf], np.nan)
    result["consistency"] = result["positive_years"] / result["years"].replace(0, np.nan)
    result["expectancy_score"] = result["avg_margin_return"] * np.sqrt(result["samples"]) * result["consistency"].fillna(0)
    result["side"] = meta["side"]
    result["stop_take"] = meta["stop_take"]
    result["horizon_bars"] = int(meta["horizon_bars"])
    return result.sort_values(["expectancy_score", "avg_margin_return"], ascending=False)


def run_analysis(
    frame: pd.DataFrame,
    outcomes: List[Tuple[str, str, str, str]],
    score_floors: List[float],
    leverage: float,
    fee_rate: float,
    min_samples: int,
) -> pd.DataFrame:
    prepared = add_bucket_columns(frame)
    signal_rows = explode_signals(prepared, score_floors)
    signal_rows = explode_modifiers(signal_rows)

    group_specs = {
        "signal_regime": ["side", "signal", "score_floor", "regime_label"],
        "signal_regime_context": ["side", "signal", "score_floor", "regime_label", "context_available", "risk_off_signal"],
        "signal_regime_modifier": ["side", "signal", "score_floor", "regime_label", "modifier"],
        "signal_regime_derivatives": ["side", "signal", "score_floor", "regime_label", "open_interest_rising", "funding_bucket", "premium_bucket"],
        "signal_regime_liquidity": ["side", "signal", "score_floor", "regime_label", "l2_available", "l2_depth_bucket", "spread_bucket"],
        "signal_regime_vwap_volume": ["side", "signal", "score_floor", "regime_label", "vwap_alignment", "volume_bucket"],
    }
    result_frames = []
    for _, _, _, outcome_column in outcomes:
        for slice_name, group_columns in group_specs.items():
            result = aggregate_slice(
                frame=signal_rows,
                group_columns=group_columns,
                outcome_column=outcome_column,
                leverage=leverage,
                fee_rate=fee_rate,
                min_samples=min_samples,
            )
            if result.empty:
                continue
            result.insert(0, "slice", slice_name)
            result_frames.append(result)
    if not result_frames:
        return pd.DataFrame()
    return pd.concat(result_frames, ignore_index=True)


def write_outputs(results: pd.DataFrame, output_dir: str, top_n: int):
    os.makedirs(output_dir, exist_ok=True)
    full_path = os.path.join(output_dir, "joined_5m_signal_outcomes.csv")
    top_path = os.path.join(output_dir, "joined_5m_signal_outcomes_top.csv")
    results.to_csv(full_path, index=False)

    top = results.sort_values(["expectancy_score", "avg_margin_return"], ascending=False).head(top_n)
    top.to_csv(top_path, index=False)
    print(f"Wrote {len(results)} rows to {full_path}")
    print(f"Wrote top {len(top)} rows to {top_path}")
    if not top.empty:
        display_columns = [
            "slice",
            "side",
            "signal",
            "score_floor",
            "regime_label",
            "stop_take",
            "horizon_bars",
            "samples",
            "avg_margin_return",
            "median_margin_return",
            "win_rate",
            "profit_factor",
            "stop_rate",
            "take_rate",
            "consistency",
        ]
        display_columns = [column for column in display_columns if column in top.columns]
        print("\nTop slices:")
        print(top[display_columns].head(12).to_string(index=False))


def parse_args():
    parser = argparse.ArgumentParser(description="Analyze joined research table signal outcomes")
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--score-floors", default="0.65,0.75,0.85")
    parser.add_argument("--leverage", type=float, default=20.0)
    parser.add_argument(
        "--fee-rate",
        type=float,
        default=0.00035,
        help="Assumed per-side notional fee rate used for margin-return ranking.",
    )
    parser.add_argument("--min-samples", type=int, default=500)
    parser.add_argument("--top-n", type=int, default=100)
    return parser.parse_args()


def main():
    args = parse_args()
    frame, outcomes = load_columns(args.input)
    if not outcomes:
        raise RuntimeError("No stop/take outcome columns found")
    results = run_analysis(
        frame=frame,
        outcomes=outcomes,
        score_floors=parse_float_list(args.score_floors),
        leverage=args.leverage,
        fee_rate=args.fee_rate,
        min_samples=args.min_samples,
    )
    if results.empty:
        raise RuntimeError("No slices met the minimum sample requirement")

    first_ts = int(frame["timestamp"].min())
    last_ts = int(frame["timestamp"].max())
    print("Joined research outcome scan:")
    print(f"  Input rows: {len(frame)}")
    print(f"  Range:      {epoch_to_utc(first_ts)} -> {epoch_to_utc(last_ts)}")
    print(f"  Outcomes:   {len(outcomes)}")
    print(f"  Leverage:   {args.leverage:g}x")
    print(f"  Fee rate:   {args.fee_rate:g} per side")
    write_outputs(results, args.output_dir, args.top_n)


if __name__ == "__main__":
    main()
