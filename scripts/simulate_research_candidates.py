"""
Replay ranked research slices as non-overlapping candidate strategies.

The joined-table analyzer ranks overlapping signal slices. This script takes
those slice definitions and simulates them chronologically with one open trade
per candidate, using the outcome horizon as the cooldown window.

Usage:
    conda run -n hummingbot python scripts/simulate_research_candidates.py
"""
import argparse
import os
import re
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd


DEFAULT_TABLE = "data/research/sol_5m_joined_research.csv"
DEFAULT_SLICES = "data/research/analysis/joined_5m_signal_outcomes_top.csv"
DEFAULT_OUTPUT = "data/research/analysis/joined_5m_candidate_simulations.csv"
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
SLICE_GROUPS = {
    "signal_regime": ["side", "signal", "score_floor", "regime_label"],
    "signal_regime_context": ["side", "signal", "score_floor", "regime_label", "context_available", "risk_off_signal"],
    "signal_regime_modifier": ["side", "signal", "score_floor", "regime_label", "modifier"],
    "signal_regime_derivatives": ["side", "signal", "score_floor", "regime_label", "open_interest_rising", "funding_bucket", "premium_bucket"],
    "signal_regime_liquidity": ["side", "signal", "score_floor", "regime_label", "l2_available", "l2_depth_bucket", "spread_bucket"],
    "signal_regime_vwap_volume": ["side", "signal", "score_floor", "regime_label", "vwap_alignment", "volume_bucket"],
}
OUTCOME_RE = re.compile(r"^(long|short)_(sl[^_]+_tp[^_]+)_h(\d+)_return$")


def epoch_to_utc(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def bool_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(False, index=frame.index)
    values = frame[column]
    if values.dtype == bool:
        return values.fillna(False)
    return values.fillna(False).astype(str).str.lower().isin({"true", "1", "yes", "y"})


def add_bucket_columns(frame: pd.DataFrame) -> pd.DataFrame:
    df = frame.copy()
    df["date"] = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.floor("D")
    df["year"] = df["date"].dt.year
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
        [
            df["price_above_session_vwap"] & df["price_above_rolling_vwap"],
            ~df["price_above_session_vwap"] & ~df["price_above_rolling_vwap"],
        ],
        ["above_both", "below_both"],
        default="mixed",
    )
    df["modifier"] = df["modifiers"].fillna("").astype(str)
    return df


def required_table_columns(slices: pd.DataFrame) -> List[str]:
    columns = {
        "timestamp",
        "high",
        "low",
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
        "l2_best_bid",
        "l2_best_ask",
        "l2_depth_top5_usd",
        "l2_buy_10k_slippage_bps",
        "l2_sell_10k_slippage_bps",
        "l2_buy_100k_slippage_bps",
        "l2_sell_100k_slippage_bps",
    }
    for _, row in slices.iterrows():
        side = row["side"]
        stop_take = row["stop_take"]
        horizon = int(row["horizon_bars"])
        columns.add(f"{side}_{stop_take}_h{horizon}_return")
        columns.add(f"{side}_{stop_take}_h{horizon}_exit")
    return sorted(columns)


def load_table(path: str, slices: pd.DataFrame) -> pd.DataFrame:
    header = set(pd.read_csv(path, nrows=0).columns)
    usecols = [column for column in required_table_columns(slices) if column in header]
    return add_bucket_columns(pd.read_csv(path, usecols=usecols, low_memory=False))


def signal_mask(frame: pd.DataFrame, side: str, signal: str, score_floor: float) -> pd.Series:
    signals = frame["market_signals"].fillna("").astype(str)
    score_column = "long_signal_score" if side == "long" else "short_signal_score"
    score = pd.to_numeric(frame[score_column], errors="coerce")
    side_ok = signal in LONG_SIGNALS if side == "long" else signal in SHORT_SIGNALS
    if not side_ok:
        return pd.Series(False, index=frame.index)
    pattern = rf"(?:^|,){re.escape(signal)}(?:,|$)"
    return signals.str.contains(pattern, regex=True, na=False) & (score >= score_floor)


def apply_candidate_filters(frame: pd.DataFrame, candidate: pd.Series) -> pd.Series:
    mask = signal_mask(frame, candidate["side"], candidate["signal"], float(candidate["score_floor"]))
    for column in SLICE_GROUPS.get(candidate["slice"], []):
        if column in {"side", "signal", "score_floor"}:
            continue
        if column not in frame.columns or column not in candidate.index or pd.isna(candidate[column]):
            continue
        value = candidate[column]
        if column == "modifier":
            if value == "none":
                mask &= frame["modifier"].str.strip().eq("")
            else:
                pattern = rf"(?:^|,){re.escape(str(value))}(?:,|$)"
                mask &= frame["modifier"].str.contains(pattern, regex=True, na=False)
        elif frame[column].dtype == bool:
            mask &= frame[column] == bool(value)
        elif isinstance(value, (bool, np.bool_)):
            mask &= bool_series(frame, column) == bool(value)
        else:
            mask &= frame[column].astype(str) == str(value)
    return mask


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


def max_drawdown(cumulative: pd.Series) -> float:
    if cumulative.empty:
        return 0.0
    peak = cumulative.cummax()
    drawdown = cumulative - peak
    return float(drawdown.min())


def slippage_column(side: str, notional_usd: float) -> str:
    action = "buy" if side == "long" else "sell"
    size = "10k" if notional_usd <= 10_000 else "100k"
    return f"l2_{action}_{size}_slippage_bps"


def taker_slippage_bps(
    frame: pd.DataFrame,
    selected: np.ndarray,
    side: str,
    notional_usd: float,
    fallback_slippage_bps: float,
    buffer_bps: float,
) -> np.ndarray:
    column = slippage_column(side, notional_usd)
    if column in frame.columns:
        values = pd.to_numeric(frame[column], errors="coerce").iloc[selected].to_numpy(dtype="float64")
        values = np.where(np.isfinite(values), values, fallback_slippage_bps)
    else:
        values = np.full(len(selected), fallback_slippage_bps, dtype="float64")
    return values + buffer_bps


def passive_entry_fill_mask(frame: pd.DataFrame, side: str, passive_offset_bps: float) -> pd.Series:
    if "l2_best_bid" not in frame.columns or "l2_best_ask" not in frame.columns:
        return pd.Series(False, index=frame.index)
    best_bid = pd.to_numeric(frame["l2_best_bid"], errors="coerce")
    best_ask = pd.to_numeric(frame["l2_best_ask"], errors="coerce")
    next_high = pd.to_numeric(frame["high"], errors="coerce").shift(-1)
    next_low = pd.to_numeric(frame["low"], errors="coerce").shift(-1)
    if side == "long":
        bid_price = best_bid * (1 - passive_offset_bps / 10_000)
        return bid_price.notna() & next_low.notna() & (next_low <= bid_price)
    ask_price = best_ask * (1 + passive_offset_bps / 10_000)
    return ask_price.notna() & next_high.notna() & (next_high >= ask_price)


def execution_cost_rate(
    frame: pd.DataFrame,
    selected: np.ndarray,
    side: str,
    execution_mode: str,
    taker_fee_rate: float,
    maker_fee_rate: float,
    notional_usd: float,
    fallback_slippage_bps: float,
    slippage_buffer_bps: float,
) -> np.ndarray:
    exit_side = "short" if side == "long" else "long"
    if execution_mode == "taker":
        entry_fee = taker_fee_rate
        entry_slippage_bps = taker_slippage_bps(
            frame, selected, side, notional_usd, fallback_slippage_bps, slippage_buffer_bps
        )
    elif execution_mode == "maker-entry-taker-exit":
        entry_fee = maker_fee_rate
        entry_slippage_bps = np.zeros(len(selected), dtype="float64")
    else:
        raise ValueError(f"unsupported execution mode: {execution_mode}")

    exit_fee = taker_fee_rate
    exit_slippage_bps = taker_slippage_bps(
        frame, selected, exit_side, notional_usd, fallback_slippage_bps, slippage_buffer_bps
    )
    return entry_fee + exit_fee + (entry_slippage_bps + exit_slippage_bps) / 10_000


def simulate_candidate(
    frame: pd.DataFrame,
    candidate: pd.Series,
    leverage: float,
    calendar_days: int,
    execution_mode: str,
    taker_fee_rate: float,
    maker_fee_rate: float,
    notional_usd: float,
    fallback_slippage_bps: float,
    slippage_buffer_bps: float,
    passive_offset_bps: float,
) -> Optional[Dict]:
    side = candidate["side"]
    stop_take = candidate["stop_take"]
    horizon = int(candidate["horizon_bars"])
    return_column = f"{side}_{stop_take}_h{horizon}_return"
    exit_column = f"{side}_{stop_take}_h{horizon}_exit"
    if return_column not in frame.columns:
        return None

    returns = pd.to_numeric(frame[return_column], errors="coerce")
    mask = apply_candidate_filters(frame, candidate)
    eligible_entries = int(mask.sum())
    passive_fill_rate = np.nan
    if execution_mode == "maker-entry-taker-exit":
        fill_mask = passive_entry_fill_mask(frame, side, passive_offset_bps)
        passive_fills = int((mask & fill_mask).sum())
        passive_fill_rate = passive_fills / eligible_entries if eligible_entries else np.nan
        mask &= fill_mask
    selected = select_non_overlapping(mask, returns, horizon)
    if len(selected) == 0:
        return None

    trades = frame.iloc[selected].copy()
    price_returns = returns.iloc[selected].to_numpy(dtype="float64")
    costs = execution_cost_rate(
        frame=frame,
        selected=selected,
        side=side,
        execution_mode=execution_mode,
        taker_fee_rate=taker_fee_rate,
        maker_fee_rate=maker_fee_rate,
        notional_usd=notional_usd,
        fallback_slippage_bps=fallback_slippage_bps,
        slippage_buffer_bps=slippage_buffer_bps,
    )
    margin_returns = price_returns * leverage - (costs * leverage)
    trades["margin_return"] = margin_returns
    daily = trades.groupby("date")["margin_return"].sum().sort_index()
    yearly = trades.groupby("year")["margin_return"].sum()
    cumulative = daily.cumsum()
    exits = trades[exit_column].fillna("unknown") if exit_column in trades.columns else pd.Series("unknown", index=trades.index)

    return {
        "slice": candidate["slice"],
        "side": side,
        "signal": candidate["signal"],
        "score_floor": candidate["score_floor"],
        "regime_label": candidate.get("regime_label"),
        "modifier": candidate.get("modifier"),
        "context_available": candidate.get("context_available"),
        "risk_off_signal": candidate.get("risk_off_signal"),
        "open_interest_rising": candidate.get("open_interest_rising"),
        "funding_bucket": candidate.get("funding_bucket"),
        "premium_bucket": candidate.get("premium_bucket"),
        "l2_available": candidate.get("l2_available"),
        "l2_depth_bucket": candidate.get("l2_depth_bucket"),
        "spread_bucket": candidate.get("spread_bucket"),
        "vwap_alignment": candidate.get("vwap_alignment"),
        "volume_bucket": candidate.get("volume_bucket"),
        "stop_take": stop_take,
        "horizon_bars": horizon,
        "leverage": leverage,
        "execution_mode": execution_mode,
        "notional_usd": notional_usd,
        "eligible_entries": eligible_entries,
        "passive_fill_rate": passive_fill_rate,
        "avg_execution_cost_bps": float(np.mean(costs) * 10_000),
        "trades": len(trades),
        "active_days": int(daily.size),
        "calendar_days": calendar_days,
        "trades_per_calendar_day": len(trades) / calendar_days,
        "trades_per_active_day": len(trades) / daily.size if daily.size else np.nan,
        "avg_trade_margin_return": float(np.mean(margin_returns)),
        "median_trade_margin_return": float(np.median(margin_returns)),
        "win_rate": float(np.mean(margin_returns > 0)),
        "stop_rate": float((exits == "stop").mean()),
        "take_rate": float((exits == "take").mean()),
        "timeout_rate": float((exits == "timeout").mean()),
        "total_simple_return": float(np.sum(margin_returns)),
        "avg_daily_return_calendar": float(np.sum(margin_returns) / calendar_days),
        "avg_daily_return_active": float(daily.mean()) if daily.size else np.nan,
        "median_daily_return_active": float(daily.median()) if daily.size else np.nan,
        "positive_active_day_rate": float((daily > 0).mean()) if daily.size else np.nan,
        "max_daily_loss": float(daily.min()) if daily.size else np.nan,
        "max_daily_gain": float(daily.max()) if daily.size else np.nan,
        "max_drawdown_simple": max_drawdown(cumulative),
        "positive_years": int((yearly > 0).sum()),
        "years": int(yearly.size),
        "first_trade": epoch_to_utc(int(trades["timestamp"].min())),
        "last_trade": epoch_to_utc(int(trades["timestamp"].max())),
    }


def parse_float_list(value: str) -> List[float]:
    return [float(item.strip()) for item in value.split(",") if item.strip()]


def parse_args():
    parser = argparse.ArgumentParser(description="Simulate ranked research candidates")
    parser.add_argument("--table", default=DEFAULT_TABLE)
    parser.add_argument("--slices", default=DEFAULT_SLICES)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--top-n", type=int, default=100)
    parser.add_argument("--leverages", default="1,2,3,5")
    parser.add_argument("--execution-mode", choices=["taker", "maker-entry-taker-exit"], default="taker")
    parser.add_argument("--fee-rate", type=float, default=None, help="Deprecated alias for --taker-fee-rate")
    parser.add_argument("--taker-fee-rate", type=float, default=0.00035)
    parser.add_argument("--maker-fee-rate", type=float, default=0.0)
    parser.add_argument("--notional-usd", type=float, default=10_000)
    parser.add_argument("--fallback-slippage-bps", type=float, default=1.0)
    parser.add_argument("--slippage-buffer-bps", type=float, default=0.0)
    parser.add_argument("--passive-offset-bps", type=float, default=0.0)
    parser.add_argument("--min-trades", type=int, default=50)
    parser.add_argument("--target-daily-return", type=float, default=0.0025)
    return parser.parse_args()


def main():
    args = parse_args()
    taker_fee_rate = args.taker_fee_rate if args.fee_rate is None else args.fee_rate
    slices = pd.read_csv(args.slices).head(args.top_n)
    frame = load_table(args.table, slices)
    first_day = pd.to_datetime(frame["timestamp"].min(), unit="s", utc=True).floor("D")
    last_day = pd.to_datetime(frame["timestamp"].max(), unit="s", utc=True).floor("D")
    calendar_days = int((last_day - first_day).days) + 1

    rows = []
    for leverage in parse_float_list(args.leverages):
        for _, candidate in slices.iterrows():
            row = simulate_candidate(
                frame=frame,
                candidate=candidate,
                leverage=leverage,
                calendar_days=calendar_days,
                execution_mode=args.execution_mode,
                taker_fee_rate=taker_fee_rate,
                maker_fee_rate=args.maker_fee_rate,
                notional_usd=args.notional_usd,
                fallback_slippage_bps=args.fallback_slippage_bps,
                slippage_buffer_bps=args.slippage_buffer_bps,
                passive_offset_bps=args.passive_offset_bps,
            )
            if row is not None and row["trades"] >= args.min_trades:
                rows.append(row)

    if not rows:
        raise RuntimeError("No candidates produced enough non-overlapping trades")

    results = pd.DataFrame(rows).sort_values(
        ["avg_daily_return_calendar", "avg_trade_margin_return", "positive_active_day_rate"],
        ascending=False,
    )
    results["avg_daily_bps_calendar"] = results["avg_daily_return_calendar"] * 10_000
    results["target_daily_capture"] = results["avg_daily_return_calendar"] / args.target_daily_return
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    results.to_csv(args.output, index=False)
    print("Candidate simulation complete:")
    print(f"  Input candidates: {len(slices)}")
    print(f"  Output rows:      {len(results)}")
    print(f"  CSV:              {args.output}")
    display_columns = [
        "leverage",
        "side",
        "signal",
        "regime_label",
        "stop_take",
        "horizon_bars",
        "execution_mode",
        "avg_execution_cost_bps",
        "passive_fill_rate",
        "trades",
        "avg_daily_bps_calendar",
        "target_daily_capture",
        "avg_trade_margin_return",
        "median_trade_margin_return",
        "win_rate",
        "max_drawdown_simple",
        "positive_years",
        "years",
    ]
    print(results[display_columns].head(15).to_string(index=False))


if __name__ == "__main__":
    main()
