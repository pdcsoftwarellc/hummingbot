"""
Mine joined research tables for reusable strategy edge candidates.

This is intentionally stricter than the broad slice analyzer:

* entries are replayed chronologically with one open trade per candidate;
* filters are added greedily on train data only;
* final candidates are reported with train/test and full-period daily bps.

Default usage:
    conda run -n hummingbot python scripts/mine_research_edges.py
"""
import argparse
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd


DEFAULT_INPUT = "data/research/sol_5m_joined_research.csv"
DEFAULT_OUTPUT = "data/research/analysis/joined_5m_edge_candidates.csv"
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
OUTCOME_RE = re.compile(r"^(long|short)_(sl[^_]+_tp[^_]+)_h(\d+)_return$")


@dataclass(frozen=True)
class FilterAtom:
    name: str
    mask: pd.Series


def epoch_to_utc(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def bool_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(False, index=frame.index)
    values = frame[column]
    if values.dtype == bool:
        return values.fillna(False)
    return values.fillna(False).astype(str).str.lower().isin({"true", "1", "yes", "y"})


def parse_float_list(value: str) -> List[float]:
    return [float(item.strip()) for item in value.split(",") if item.strip()]


def parse_int_list(value: str) -> List[int]:
    return [int(item.strip()) for item in value.split(",") if item.strip()]


def discover_outcomes(header: Sequence[str]) -> List[Tuple[str, str, int, str, str]]:
    outcomes = []
    for column in header:
        match = OUTCOME_RE.match(column)
        if not match:
            continue
        side, stop_take, horizon = match.groups()
        exit_column = column.replace("_return", "_exit")
        if exit_column in header:
            outcomes.append((side, stop_take, int(horizon), column, exit_column))
    return outcomes


def required_columns(header: Sequence[str], horizons: Sequence[int], stop_takes: Sequence[str]) -> List[str]:
    base_columns = {
        "timestamp",
        "regime_label",
        "modifiers",
        "market_signals",
        "long_signal_score",
        "short_signal_score",
        "context_available",
        "l2_available",
        "risk_off_signal",
        "allow_longs",
        "allow_shorts",
        "regime_high_vol_danger",
        "regime_liquidity_thin",
        "ema_fast_above_slow",
        "price_above_rolling_vwap",
        "price_above_session_vwap",
        "roc_6_positive",
        "roc_12_positive",
        "volume_expanding",
        "taker_buy_pressure",
        "cvd_proxy_rising",
        "funding_positive",
        "funding_negative",
        "open_interest_rising",
        "premium_positive",
        "liquidity_thin_feature",
        "recent_breakout_above",
        "recent_breakdown_below",
        "failed_breakout_above",
        "failed_breakdown_below",
        "price_vs_rolling_vwap_pct",
        "price_vs_session_vwap_pct",
        "rsi",
        "roc_6",
        "roc_12",
        "volume_expansion",
        "taker_buy_imbalance",
        "cvd_proxy_change",
        "funding_rate_level",
        "funding_rate_trend",
        "open_interest_change_pct_feature",
        "premium_level",
        "premium_trend",
        "basis_pct",
        "spread_bps",
        "depth_usd_feature",
        "depth_imbalance",
        "liquidity_score_feature",
        "atr_pct_feature",
        "realized_vol_feature",
        "l2_depth_top5_usd",
        "l2_imbalance_top5",
        "l2_mean_spread_pct",
    }
    for side, stop_take, horizon, return_column, exit_column in discover_outcomes(header):
        if horizon in horizons and stop_take in stop_takes:
            base_columns.add(return_column)
            base_columns.add(exit_column)
    return sorted(column for column in base_columns if column in header)


def add_derived_columns(frame: pd.DataFrame) -> pd.DataFrame:
    df = frame.copy()
    df["date"] = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.floor("D")
    df["year"] = df["date"].dt.year
    df["regime_label"] = df["regime_label"].fillna("unknown").astype(str)
    df["modifiers"] = df["modifiers"].fillna("").astype(str)
    for column in [
        "context_available",
        "l2_available",
        "risk_off_signal",
        "allow_longs",
        "allow_shorts",
        "regime_high_vol_danger",
        "regime_liquidity_thin",
        "ema_fast_above_slow",
        "price_above_rolling_vwap",
        "price_above_session_vwap",
        "roc_6_positive",
        "roc_12_positive",
        "volume_expanding",
        "taker_buy_pressure",
        "cvd_proxy_rising",
        "funding_positive",
        "funding_negative",
        "open_interest_rising",
        "premium_positive",
        "liquidity_thin_feature",
        "recent_breakout_above",
        "recent_breakdown_below",
        "failed_breakout_above",
        "failed_breakdown_below",
    ]:
        if column in df.columns:
            df[column] = bool_series(df, column)
    return df


def signal_mask(frame: pd.DataFrame, side: str, signal: str, score_floor: float) -> pd.Series:
    side_ok = signal in LONG_SIGNALS if side == "long" else signal in SHORT_SIGNALS
    if not side_ok:
        return pd.Series(False, index=frame.index)
    score_column = "long_signal_score" if side == "long" else "short_signal_score"
    score = pd.to_numeric(frame[score_column], errors="coerce")
    pattern = rf"(?:^|,){re.escape(signal)}(?:,|$)"
    signal_ok = frame["market_signals"].fillna("").astype(str).str.contains(pattern, regex=True, na=False)
    return signal_ok & (score >= score_floor)


def modifier_mask(frame: pd.DataFrame, modifier: str) -> pd.Series:
    pattern = rf"(?:^|,){re.escape(modifier)}(?:,|$)"
    return frame["modifiers"].str.contains(pattern, regex=True, na=False)


def categorical_atoms(frame: pd.DataFrame, min_rows: int) -> List[FilterAtom]:
    atoms: List[FilterAtom] = []
    for label in sorted(frame["regime_label"].dropna().unique()):
        mask = frame["regime_label"].eq(label)
        if int(mask.sum()) >= min_rows:
            atoms.append(FilterAtom(f"regime_label={label}", mask))

    modifiers = sorted(
        {
            item.strip()
            for value in frame["modifiers"].dropna().astype(str)
            for item in value.split(",")
            if item.strip()
        }
    )
    for modifier in modifiers:
        mask = modifier_mask(frame, modifier)
        if int(mask.sum()) >= min_rows:
            atoms.append(FilterAtom(f"modifier_has={modifier}", mask))

    bool_columns = [
        "context_available",
        "l2_available",
        "risk_off_signal",
        "allow_longs",
        "allow_shorts",
        "regime_high_vol_danger",
        "regime_liquidity_thin",
        "ema_fast_above_slow",
        "price_above_rolling_vwap",
        "price_above_session_vwap",
        "roc_6_positive",
        "roc_12_positive",
        "volume_expanding",
        "taker_buy_pressure",
        "cvd_proxy_rising",
        "funding_positive",
        "funding_negative",
        "open_interest_rising",
        "premium_positive",
        "liquidity_thin_feature",
        "recent_breakout_above",
        "recent_breakdown_below",
        "failed_breakout_above",
        "failed_breakdown_below",
    ]
    for column in bool_columns:
        if column not in frame.columns:
            continue
        values = bool_series(frame, column)
        for expected in [True, False]:
            mask = values.eq(expected)
            if int(mask.sum()) >= min_rows:
                atoms.append(FilterAtom(f"{column}={str(expected).lower()}", mask))
    return atoms


def numeric_atoms(frame: pd.DataFrame, min_rows: int) -> List[FilterAtom]:
    atoms: List[FilterAtom] = []
    numeric_columns = [
        "price_vs_rolling_vwap_pct",
        "price_vs_session_vwap_pct",
        "rsi",
        "roc_6",
        "roc_12",
        "volume_expansion",
        "taker_buy_imbalance",
        "cvd_proxy_change",
        "funding_rate_level",
        "funding_rate_trend",
        "open_interest_change_pct_feature",
        "premium_level",
        "premium_trend",
        "basis_pct",
        "spread_bps",
        "depth_usd_feature",
        "depth_imbalance",
        "liquidity_score_feature",
        "atr_pct_feature",
        "realized_vol_feature",
        "l2_depth_top5_usd",
        "l2_imbalance_top5",
        "l2_mean_spread_pct",
    ]
    for column in numeric_columns:
        if column not in frame.columns:
            continue
        values = pd.to_numeric(frame[column], errors="coerce")
        valid = values.dropna()
        if len(valid) < min_rows:
            continue
        for quantile in [0.2, 0.4, 0.6, 0.8]:
            threshold = float(valid.quantile(quantile))
            if not np.isfinite(threshold):
                continue
            for op, mask in [(">=", values >= threshold), ("<=", values <= threshold)]:
                if int(mask.sum()) >= min_rows:
                    atoms.append(FilterAtom(f"{column}{op}{threshold:.8g}", mask.fillna(False)))
    return atoms


def candidate_atoms(frame: pd.DataFrame, min_rows: int) -> List[FilterAtom]:
    atoms = categorical_atoms(frame, min_rows)
    atoms.extend(numeric_atoms(frame, min_rows))
    return atoms


def select_non_overlapping(mask: pd.Series, returns: pd.Series, horizon_bars: int) -> np.ndarray:
    candidate_indices = np.flatnonzero(mask.to_numpy() & returns.notna().to_numpy())
    selected = []
    next_allowed = -1
    for index in candidate_indices:
        if index < next_allowed:
            continue
        selected.append(index)
        next_allowed = index + horizon_bars
    return np.array(selected, dtype=int)


def max_drawdown(daily_returns: pd.Series) -> float:
    if daily_returns.empty:
        return 0.0
    cumulative = daily_returns.cumsum()
    return float((cumulative - cumulative.cummax()).min())


def evaluate_mask(
    frame: pd.DataFrame,
    mask: pd.Series,
    side: str,
    stop_take: str,
    horizon: int,
    leverage: float,
    fee_rate: float,
    split_timestamp: int,
    calendar_days: int,
) -> Optional[Dict]:
    return_column = f"{side}_{stop_take}_h{horizon}_return"
    exit_column = f"{side}_{stop_take}_h{horizon}_exit"
    if return_column not in frame.columns:
        return None

    returns = pd.to_numeric(frame[return_column], errors="coerce")
    selected = select_non_overlapping(mask, returns, horizon)
    if len(selected) == 0:
        return None

    trades = frame.iloc[selected].copy()
    price_returns = returns.iloc[selected].to_numpy(dtype="float64")
    margin_returns = price_returns * leverage - (2 * fee_rate * leverage)
    trades["margin_return"] = margin_returns

    train = trades["timestamp"] < split_timestamp
    test = ~train
    daily = trades.groupby("date")["margin_return"].sum().sort_index()
    yearly = trades.groupby("year")["margin_return"].sum()
    exits = trades[exit_column].fillna("unknown") if exit_column in trades.columns else pd.Series("unknown", index=trades.index)

    train_sum = float(trades.loc[train, "margin_return"].sum())
    test_sum = float(trades.loc[test, "margin_return"].sum())
    train_days = max(1, int((pd.to_datetime(split_timestamp, unit="s", utc=True).floor("D") - frame["date"].min()).days))
    test_days = max(1, calendar_days - train_days)

    gross_profit = float(np.clip(margin_returns, 0, None).sum())
    gross_loss = float(-np.clip(margin_returns, None, 0).sum())
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else np.nan

    return {
        "trades": int(len(trades)),
        "train_trades": int(train.sum()),
        "test_trades": int(test.sum()),
        "avg_trade_margin_return": float(np.mean(margin_returns)),
        "median_trade_margin_return": float(np.median(margin_returns)),
        "win_rate": float(np.mean(margin_returns > 0)),
        "profit_factor": profit_factor,
        "stop_rate": float((exits == "stop").mean()),
        "take_rate": float((exits == "take").mean()),
        "timeout_rate": float((exits == "timeout").mean()),
        "total_simple_return": float(np.sum(margin_returns)),
        "avg_daily_return_calendar": float(np.sum(margin_returns) / calendar_days),
        "avg_daily_bps_calendar": float(np.sum(margin_returns) / calendar_days * 10_000),
        "train_daily_bps": train_sum / train_days * 10_000,
        "test_daily_bps": test_sum / test_days * 10_000,
        "active_days": int(daily.size),
        "positive_active_day_rate": float((daily > 0).mean()) if daily.size else np.nan,
        "max_daily_loss": float(daily.min()) if daily.size else np.nan,
        "max_daily_gain": float(daily.max()) if daily.size else np.nan,
        "max_drawdown_simple": max_drawdown(daily),
        "positive_years": int((yearly > 0).sum()),
        "years": int(yearly.size),
        "first_trade": epoch_to_utc(int(trades["timestamp"].min())),
        "last_trade": epoch_to_utc(int(trades["timestamp"].max())),
    }


def train_score(metrics: Dict, min_train_trades: int, min_test_trades: int, require_positive_test: bool) -> float:
    if metrics["train_trades"] < min_train_trades or metrics["test_trades"] < min_test_trades:
        return -np.inf
    if metrics["train_daily_bps"] <= 0:
        return -np.inf
    if require_positive_test and metrics["test_daily_bps"] <= 0:
        return -np.inf
    consistency = metrics["positive_years"] / max(metrics["years"], 1)
    drawdown_penalty = abs(min(metrics["max_drawdown_simple"], 0.0))
    if not require_positive_test and metrics["test_daily_bps"] <= 0:
        drawdown_penalty += abs(metrics["test_daily_bps"]) / 10.0
    return metrics["avg_daily_bps_calendar"] * consistency / (1.0 + drawdown_penalty)


def mine_one(
    frame: pd.DataFrame,
    atoms: Sequence[FilterAtom],
    base_mask: pd.Series,
    side: str,
    signal: str,
    score_floor: float,
    stop_take: str,
    horizon: int,
    leverage: float,
    fee_rate: float,
    split_timestamp: int,
    calendar_days: int,
    max_filters: int,
    min_trades: int,
    min_train_trades: int,
    min_test_trades: int,
    min_improvement_bps: float,
    require_positive_test: bool,
) -> Optional[Dict]:
    current_mask = base_mask.copy()
    current_filters: List[str] = []
    current_metrics = evaluate_mask(
        frame, current_mask, side, stop_take, horizon, leverage, fee_rate, split_timestamp, calendar_days
    )
    if current_metrics is None:
        return None

    current_score = train_score(current_metrics, min_train_trades, min_test_trades, require_positive_test)
    used = set()
    for _ in range(max_filters):
        best_atom: Optional[FilterAtom] = None
        best_metrics: Optional[Dict] = None
        best_score = current_score
        for atom in atoms:
            if atom.name in used:
                continue
            next_mask = current_mask & atom.mask
            if int(next_mask.sum()) < min_trades:
                continue
            metrics = evaluate_mask(
                frame, next_mask, side, stop_take, horizon, leverage, fee_rate, split_timestamp, calendar_days
            )
            if metrics is None or metrics["trades"] < min_trades:
                continue
            score = train_score(metrics, min_train_trades, min_test_trades, require_positive_test)
            if score > best_score + min_improvement_bps:
                best_atom = atom
                best_metrics = metrics
                best_score = score

        if best_atom is None or best_metrics is None:
            break
        current_mask &= best_atom.mask
        current_filters.append(best_atom.name)
        used.add(best_atom.name)
        current_metrics = best_metrics
        current_score = best_score

    if not np.isfinite(current_score) or current_metrics["trades"] < min_trades:
        return None

    return {
        "side": side,
        "signal": signal,
        "score_floor": score_floor,
        "stop_take": stop_take,
        "horizon_bars": horizon,
        "leverage": leverage,
        "filters": " AND ".join(current_filters) if current_filters else "none",
        "filter_count": len(current_filters),
        "score": current_score,
        **current_metrics,
    }


def load_frame(path: str, horizons: Sequence[int], stop_takes: Sequence[str]) -> pd.DataFrame:
    header = pd.read_csv(path, nrows=0).columns.tolist()
    usecols = required_columns(header, horizons, stop_takes)
    return add_derived_columns(pd.read_csv(path, usecols=usecols, low_memory=False))


def parse_args():
    parser = argparse.ArgumentParser(description="Mine joined research tables for edge candidates")
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--leverages", default="1,2,3,5")
    parser.add_argument("--score-floors", default="0,0.6666667,1")
    parser.add_argument("--horizons", default="12,24,48")
    parser.add_argument("--stop-takes", default="sl0p5_tp1p5,sl1_tp3,sl1p5_tp4p5")
    parser.add_argument("--fee-rate", type=float, default=0.00035)
    parser.add_argument("--target-daily-bps", type=float, default=25.0)
    parser.add_argument("--min-trades", type=int, default=80)
    parser.add_argument("--min-train-trades", type=int, default=50)
    parser.add_argument("--min-test-trades", type=int, default=20)
    parser.add_argument("--max-filters", type=int, default=3)
    parser.add_argument("--max-atoms", type=int, default=120)
    parser.add_argument("--train-fraction", type=float, default=0.70)
    parser.add_argument("--min-improvement-bps", type=float, default=0.05)
    parser.add_argument("--allow-negative-test", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    leverages = parse_float_list(args.leverages)
    score_floors = parse_float_list(args.score_floors)
    horizons = parse_int_list(args.horizons)
    stop_takes = [item.strip() for item in args.stop_takes.split(",") if item.strip()]

    frame = load_frame(args.input, horizons, stop_takes)
    first_day = frame["date"].min()
    last_day = frame["date"].max()
    calendar_days = int((last_day - first_day).days) + 1
    split_index = int(len(frame) * args.train_fraction)
    split_timestamp = int(frame["timestamp"].iloc[split_index])

    atoms = candidate_atoms(frame, args.min_trades)
    atoms = sorted(atoms, key=lambda atom: atom.name)[: args.max_atoms]

    rows = []
    for side, signals in [("long", sorted(LONG_SIGNALS)), ("short", sorted(SHORT_SIGNALS))]:
        for signal in signals:
            for score_floor in score_floors:
                base_mask = signal_mask(frame, side, signal, score_floor)
                if int(base_mask.sum()) < args.min_trades:
                    continue
                for stop_take in stop_takes:
                    for horizon in horizons:
                        for leverage in leverages:
                            row = mine_one(
                                frame=frame,
                                atoms=atoms,
                                base_mask=base_mask,
                                side=side,
                                signal=signal,
                                score_floor=score_floor,
                                stop_take=stop_take,
                                horizon=horizon,
                                leverage=leverage,
                                fee_rate=args.fee_rate,
                                split_timestamp=split_timestamp,
                                calendar_days=calendar_days,
                                max_filters=args.max_filters,
                                min_trades=args.min_trades,
                                min_train_trades=args.min_train_trades,
                                min_test_trades=args.min_test_trades,
                                min_improvement_bps=args.min_improvement_bps,
                                require_positive_test=not args.allow_negative_test,
                            )
                            if row is not None:
                                row["target_capture"] = row["avg_daily_bps_calendar"] / args.target_daily_bps
                                rows.append(row)

    if not rows:
        raise RuntimeError("No edge candidates passed train/test constraints")

    results = pd.DataFrame(rows).sort_values(
        ["avg_daily_bps_calendar", "test_daily_bps", "score"],
        ascending=False,
    )
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    results.to_csv(args.output, index=False)
    print("Edge mining complete:")
    print(f"  Rows:        {len(results)}")
    print(f"  Atoms:       {len(atoms)}")
    print(f"  Split time:  {epoch_to_utc(split_timestamp)}")
    print(f"  CSV:         {args.output}")
    display_columns = [
        "leverage",
        "side",
        "signal",
        "stop_take",
        "horizon_bars",
        "trades",
        "avg_daily_bps_calendar",
        "target_capture",
        "train_daily_bps",
        "test_daily_bps",
        "win_rate",
        "profit_factor",
        "max_drawdown_simple",
        "filters",
    ]
    print(results[display_columns].head(20).to_string(index=False))


if __name__ == "__main__":
    main()
