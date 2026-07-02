"""
Backtest L2-aware SOL swing hypotheses on the joined research table.

The goal is to test side-specific long/short swing logic, not to mirror the
older 1h regime snipe controller. Entries are generated from the 5m joined
table, then replayed through OHLC bars with fees, funding, and L2 slippage.
"""

import argparse
import json
import math
import os
from dataclasses import asdict, dataclass
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd


DEFAULT_INPUT = "data/research/sol_5m_joined_research.csv"
DEFAULT_OUTPUT_DIR = "data/backtests/sol_swing_baseline"


@dataclass
class SwingConfig:
    initial_capital: float = 10_000.0
    collateral_pct: float = 0.05
    leverage: float = 10.0
    long_size_multiplier: float = 1.0
    short_size_multiplier: float = 1.0
    trap_size_multiplier: float = 1.0
    min_confidence: float = 0.55
    confidence_scaled_leverage: bool = True
    min_leverage: float = 2.0
    full_leverage_confidence: float = 0.9
    stop_loss: float = 0.006
    take_profit: float = 0.04
    horizon_bars: int = 432
    cooldown_bars: int = 12
    disable_trend_shorts: bool = False
    disable_trap_longs: bool = True
    disable_trap_shorts: bool = False
    require_short_momentum_confirmation: bool = False
    max_trend_short_roc12: Optional[float] = None
    min_trend_short_session_vwap_gap: Optional[float] = None
    require_short_cvd_not_rising: bool = False
    max_long_oi_change: Optional[float] = None
    max_long_session_vwap_pct: Optional[float] = 0.008
    require_long_cvd_rising: bool = True
    require_long_oi_not_rising: bool = False
    min_long_roc12: Optional[float] = None
    enable_breakout_longs: bool = True
    breakout_long_min_score: float = 0.75
    breakout_long_max_session_vwap_pct: float = 0.025
    breakout_long_min_volume_expansion: float = 1.0
    breakout_long_max_volume_expansion: float = 2.5
    breakout_long_min_regime_vol_ratio: float = 1.0
    breakout_long_min_roc12: float = 0.0
    breakout_long_max_funding: float = 0.000025
    breakout_long_min_premium: float = -0.0007
    breakout_long_max_premium: float = 0.0004
    breakout_long_require_cvd_rising: bool = True
    breakout_long_exclude_downtrends: bool = True
    breakout_stop_loss: Optional[float] = 0.01
    breakout_take_profit: Optional[float] = 0.05
    min_trend_short_funding: float = 0.000035
    min_trend_short_premium: float = 0.0005
    use_atr_stop: bool = False
    atr_stop_multiplier: float = 0.45
    min_atr_stop: float = 0.004
    max_atr_stop: float = 0.009
    atr_reward_r: float = 6.0
    partial_take_profit: bool = False
    partial_take_profit_r: float = 2.0
    partial_exit_fraction: float = 0.5
    breakeven_trigger_r: float = 0.0
    breakeven_buffer_bps: float = 0.0
    stale_exit_bars: int = 0
    stale_min_favorable_r: float = 0.0
    taker_fee_rate: float = 0.00045
    fallback_slippage_bps: float = 2.0
    max_spread_bps: float = 2.0
    min_depth_top5_usd: float = 150_000.0
    max_entry_slippage_bps: float = 2.5
    funding_bar_fraction: float = 5.0 / 60.0


@dataclass
class Trade:
    book: str
    side: str
    signal_index: int
    entry_index: int
    exit_index: int
    entry_timestamp: int
    exit_timestamp: int
    entry_price: float
    exit_price: float
    close_type: str
    notional: float
    leverage: float
    gross_pnl: float
    fees: float
    slippage_cost: float
    funding_pnl: float
    net_pnl: float
    equity_after: float
    return_on_margin_pct: float
    holding_bars: int
    entry_regime: str
    entry_modifiers: str
    funding_rate: float
    premium: float
    l2_spread_bps: float
    l2_depth_top5_usd: float


def parse_bool(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series.fillna(False)
    return series.astype(str).str.lower().isin(["true", "1", "yes"])


def contains_token(series: pd.Series, token: str) -> pd.Series:
    values = series.fillna("").astype(str)
    return values.str.contains(rf"(?:^|,){token}(?:,|$)", regex=True, na=False)


def load_table(path: str) -> pd.DataFrame:
    usecols = [
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "regime_label",
        "regime_vol_ratio",
        "regime_atr_pct",
        "confidence",
        "modifiers",
        "market_signals",
        "long_signal_score",
        "context_available",
        "l2_available",
        "funding_rate",
        "premium",
        "open_interest_change_pct",
        "regime_vol_ratio",
        "regime_atr_pct",
        "l2_spread_bps",
        "l2_depth_top5_usd",
        "l2_buy_10k_slippage_bps",
        "l2_sell_10k_slippage_bps",
        "ema_fast_above_slow",
        "price_above_rolling_vwap",
        "price_above_session_vwap",
        "price_vs_rolling_vwap_pct",
        "price_vs_session_vwap_pct",
        "rsi",
        "roc_6",
        "roc_12",
        "volume_expansion",
        "taker_buy_pressure",
        "cvd_proxy_rising",
        "open_interest_rising",
        "recent_breakout_above",
        "recent_breakdown_below",
        "failed_breakout_above",
        "failed_breakdown_below",
        "risk_off_signal",
    ]
    header = set(pd.read_csv(path, nrows=0).columns)
    available = [column for column in usecols if column in header]
    df = pd.read_csv(path, usecols=available, low_memory=False)
    for column in ["timestamp", "open", "high", "low", "close", "confidence"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    numeric_defaults = [
        "funding_rate",
        "premium",
        "open_interest_change_pct",
        "regime_vol_ratio",
        "regime_atr_pct",
        "l2_spread_bps",
        "l2_depth_top5_usd",
        "l2_buy_10k_slippage_bps",
        "l2_sell_10k_slippage_bps",
        "price_vs_rolling_vwap_pct",
        "price_vs_session_vwap_pct",
        "rsi",
        "roc_6",
        "roc_12",
        "volume_expansion",
        "long_signal_score",
    ]
    for column in numeric_defaults:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
        else:
            df[column] = 0.0
    if "market_signals" not in df.columns:
        df["market_signals"] = ""
    for column in [
        "context_available",
        "l2_available",
        "ema_fast_above_slow",
        "price_above_rolling_vwap",
        "price_above_session_vwap",
        "taker_buy_pressure",
        "cvd_proxy_rising",
        "open_interest_rising",
        "recent_breakout_above",
        "recent_breakdown_below",
        "failed_breakout_above",
        "failed_breakdown_below",
        "risk_off_signal",
    ]:
        if column in df.columns:
            df[column] = parse_bool(df[column])
        else:
            df[column] = False
    return (
        df.dropna(subset=["timestamp", "open", "high", "low", "close"])
        .sort_values("timestamp")
        .drop_duplicates(subset=["timestamp"])
        .reset_index(drop=True)
    )


def build_entry_masks(df: pd.DataFrame, config: SwingConfig) -> Dict[str, pd.Series]:
    prior_vwap_min = df["price_vs_rolling_vwap_pct"].rolling(6, min_periods=1).min().shift(1)
    prior_vwap_max = df["price_vs_rolling_vwap_pct"].rolling(6, min_periods=1).max().shift(1)
    modifiers = df.get("modifiers", pd.Series("", index=df.index))
    market_signals = df.get("market_signals", pd.Series("", index=df.index))
    funding_extreme = contains_token(modifiers, "funding_extreme")
    liquidity_thin = contains_token(modifiers, "liquidity_thin")
    strong_long_continuation = contains_token(market_signals, "strong_long_continuation")

    common = (
        df["context_available"]
        & df["l2_available"]
        & (df["confidence"] >= config.min_confidence)
        & (df["l2_spread_bps"] <= config.max_spread_bps)
        & (df["l2_depth_top5_usd"] >= config.min_depth_top5_usd)
        & (~liquidity_thin)
    )
    long_execution = common & (df["l2_buy_10k_slippage_bps"] <= config.max_entry_slippage_bps)
    short_execution = common & (df["l2_sell_10k_slippage_bps"] <= config.max_entry_slippage_bps)

    long_not_crowded = (
        (df["funding_rate"] >= 0.0)
        & (df["funding_rate"] <= 0.000025)
        & (df["premium"] >= -0.0007)
        & (df["premium"] <= 0.0)
        & (~funding_extreme)
    )
    short_not_crowded = (
        (df["funding_rate"] >= 0.0)
        & (df["premium"] >= 0.0005)
    )

    trend_pullback_long = (
        long_execution
        & df["regime_label"].isin(["uptrend", "breakout"])
        & long_not_crowded
        & df["ema_fast_above_slow"]
        & df["price_above_rolling_vwap"]
        & (prior_vwap_min <= -0.0015)
        & (df["roc_6"] > 0)
        & (df["volume_expansion"].between(1.2, 1.8))
        & (df["rsi"].between(55, 72))
        & (df["regime_vol_ratio"] >= 1.0)
        & (~df["risk_off_signal"])
    )
    if config.max_long_oi_change is not None:
        trend_pullback_long &= df["open_interest_change_pct"] <= config.max_long_oi_change
    if config.max_long_session_vwap_pct is not None:
        trend_pullback_long &= df["price_vs_session_vwap_pct"] <= config.max_long_session_vwap_pct
    if config.require_long_cvd_rising:
        trend_pullback_long &= df["cvd_proxy_rising"]
    if config.require_long_oi_not_rising:
        trend_pullback_long &= ~df["open_interest_rising"]
    if config.min_long_roc12 is not None:
        trend_pullback_long &= df["roc_12"] >= config.min_long_roc12

    breakout_long_not_crowded = (
        (df["funding_rate"] >= 0.0)
        & (df["funding_rate"] <= config.breakout_long_max_funding)
        & (df["premium"] >= config.breakout_long_min_premium)
        & (df["premium"] <= config.breakout_long_max_premium)
        & (~funding_extreme)
    )
    trend_breakout_continuation_long = (
        long_execution
        & (df["regime_label"].isin(["uptrend", "breakout"]) | strong_long_continuation)
        & ((df["long_signal_score"] >= config.breakout_long_min_score) | strong_long_continuation)
        & breakout_long_not_crowded
        & df["price_above_rolling_vwap"]
        & (df["roc_6"] > 0)
        & (df["roc_12"] >= config.breakout_long_min_roc12)
        & (df["volume_expansion"].between(
            config.breakout_long_min_volume_expansion,
            config.breakout_long_max_volume_expansion,
        ))
        & (df["rsi"].between(55, 78))
        & (df["regime_vol_ratio"] >= config.breakout_long_min_regime_vol_ratio)
        & (df["price_vs_session_vwap_pct"] <= config.breakout_long_max_session_vwap_pct)
        & (~df["risk_off_signal"])
    )
    if config.breakout_long_require_cvd_rising:
        trend_breakout_continuation_long &= df["cvd_proxy_rising"]
    if config.breakout_long_exclude_downtrends:
        trend_breakout_continuation_long &= ~df["regime_label"].isin(["downtrend", "breakdown"])

    trend_pullback_short = (
        short_execution
        & df["regime_label"].isin(["downtrend", "breakdown"])
        & short_not_crowded
        & (df["funding_rate"] >= config.min_trend_short_funding)
        & (df["premium"] >= config.min_trend_short_premium)
        & (~df["ema_fast_above_slow"])
        & (~df["price_above_rolling_vwap"])
        & (prior_vwap_max >= 0.0015)
        & (df["roc_6"] < 0)
        & (df["volume_expansion"].between(0.9, 1.8))
        & (df["rsi"].between(35, 58))
        & (df["price_vs_rolling_vwap_pct"] >= -0.005)
        & (~df["risk_off_signal"])
    )
    if config.require_short_momentum_confirmation:
        trend_pullback_short &= df["roc_12"] < 0
    if config.max_trend_short_roc12 is not None:
        trend_pullback_short &= df["roc_12"] <= config.max_trend_short_roc12
    if config.min_trend_short_session_vwap_gap is not None:
        trend_pullback_short &= df["price_vs_session_vwap_pct"] <= -config.min_trend_short_session_vwap_gap
    if config.require_short_cvd_not_rising:
        trend_pullback_short &= ~df["cvd_proxy_rising"]

    failed_breakdown_long = (
        long_execution
        & df["risk_off_signal"]
        & long_not_crowded
        & (df["price_above_rolling_vwap"] | df["price_above_session_vwap"])
        & (df["roc_6"] > 0)
        & (df["volume_expansion"] >= 1.05)
        & (df["rsi"] >= 38)
    )

    failed_breakout_short = (
        short_execution
        & df["risk_off_signal"]
        & short_not_crowded
        & ((~df["price_above_rolling_vwap"]) | (~df["price_above_session_vwap"]))
        & (df["roc_6"] < 0)
        & (df["volume_expansion"] >= 1.05)
        & (df["rsi"] <= 62)
    )

    if config.disable_trend_shorts:
        trend_pullback_short = pd.Series(False, index=df.index)
    if not config.enable_breakout_longs:
        trend_breakout_continuation_long = pd.Series(False, index=df.index)
    if config.disable_trap_longs:
        failed_breakdown_long = pd.Series(False, index=df.index)
    if config.disable_trap_shorts:
        failed_breakout_short = pd.Series(False, index=df.index)

    return {
        "trend_pullback_long": trend_pullback_long.fillna(False),
        "trend_breakout_continuation_long": trend_breakout_continuation_long.fillna(False),
        "trend_pullback_short": trend_pullback_short.fillna(False),
        "failed_breakdown_long": failed_breakdown_long.fillna(False),
        "failed_breakout_short": failed_breakout_short.fillna(False),
    }


def slippage_bps(row: pd.Series, side: int, is_entry: bool, config: SwingConfig) -> float:
    action_side = side if is_entry else -side
    column = "l2_buy_10k_slippage_bps" if action_side > 0 else "l2_sell_10k_slippage_bps"
    value = row.get(column, np.nan)
    return float(value) if pd.notna(value) else config.fallback_slippage_bps


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def stop_take_pct(signal_row: pd.Series, config: SwingConfig, book: str) -> Tuple[float, float]:
    if (
        book == "trend_breakout_continuation_long"
        and config.breakout_stop_loss is not None
        and config.breakout_take_profit is not None
    ):
        return config.breakout_stop_loss, config.breakout_take_profit
    if not config.use_atr_stop:
        return config.stop_loss, config.take_profit
    atr_pct = signal_row.get("regime_atr_pct", np.nan)
    if pd.isna(atr_pct) or float(atr_pct) <= 0:
        return config.stop_loss, config.take_profit
    stop_pct = clamp(float(atr_pct) * config.atr_stop_multiplier, config.min_atr_stop, config.max_atr_stop)
    return stop_pct, stop_pct * config.atr_reward_r


def effective_leverage(signal_row: pd.Series, config: SwingConfig) -> float:
    if not config.confidence_scaled_leverage:
        return max(config.leverage, 0.0)
    max_leverage = max(config.leverage, 0.0)
    min_leverage = clamp(config.min_leverage, 0.0, max_leverage)
    if max_leverage <= min_leverage:
        return max_leverage
    confidence = signal_row.get("confidence", np.nan)
    if pd.isna(confidence):
        return min_leverage
    confidence_span = max(config.full_leverage_confidence - config.min_confidence, 1e-9)
    confidence_weight = clamp((float(confidence) - config.min_confidence) / confidence_span, 0.0, 1.0)
    return min_leverage + (max_leverage - min_leverage) * confidence_weight


def simulate_one_trade(
    df: pd.DataFrame,
    signal_index: int,
    book: str,
    side: int,
    equity: float,
    config: SwingConfig,
) -> Optional[Trade]:
    entry_index = signal_index + 1
    if entry_index >= len(df):
        return None
    signal_row = df.iloc[signal_index]
    entry_row = df.iloc[entry_index]
    entry_price = float(entry_row["open"])
    if entry_price <= 0:
        return None

    stop_pct, take_pct = stop_take_pct(signal_row, config, book)
    stop_price = entry_price * (1 - stop_pct if side > 0 else 1 + stop_pct)
    initial_stop_price = stop_price
    take_price = entry_price * (1 + take_pct if side > 0 else 1 - take_pct)
    partial_take_price = entry_price * (
        1 + stop_pct * config.partial_take_profit_r
        if side > 0
        else 1 - stop_pct * config.partial_take_profit_r
    )
    max_exit_index = min(len(df) - 1, entry_index + config.horizon_bars)
    exit_index = max_exit_index
    exit_price = float(df.iloc[max_exit_index]["close"])
    close_type = "data_end" if max_exit_index < entry_index + config.horizon_bars else "time_limit"
    breakeven_armed = False
    max_favorable = 0.0
    partial_exit_index: Optional[int] = None
    partial_exit_price: Optional[float] = None

    for index in range(entry_index, max_exit_index + 1):
        row = df.iloc[index]
        high = float(row["high"])
        low = float(row["low"])
        if side > 0:
            favorable = max(0.0, high / entry_price - 1.0)
            stop_hit = low <= stop_price
            take_hit = high >= take_price
        else:
            favorable = max(0.0, entry_price / low - 1.0)
            stop_hit = high >= stop_price
            take_hit = low <= take_price
        max_favorable = max(max_favorable, favorable)
        if stop_hit:
            exit_index = index
            exit_price = stop_price
            close_type = "breakeven_stop" if breakeven_armed and stop_price != initial_stop_price else "stop_loss"
            if partial_exit_index is not None:
                close_type = f"partial_take_profit_{close_type}"
            break
        if take_hit:
            exit_index = index
            exit_price = take_price
            close_type = "take_profit" if partial_exit_index is None else "partial_take_profit_runner"
            break
        if (
            config.partial_take_profit
            and partial_exit_index is None
            and config.partial_exit_fraction > 0
            and favorable >= stop_pct * config.partial_take_profit_r
        ):
            partial_exit_index = index
            partial_exit_price = partial_take_price
        if (
            config.stale_exit_bars > 0
            and index - entry_index + 1 >= config.stale_exit_bars
            and max_favorable < stop_pct * config.stale_min_favorable_r
        ):
            exit_index = index
            exit_price = float(row["close"])
            close_type = "stale_exit" if partial_exit_index is None else "partial_take_profit_stale_exit"
            break
        if (
            config.breakeven_trigger_r > 0
            and not breakeven_armed
            and max_favorable >= stop_pct * config.breakeven_trigger_r
        ):
            if side > 0:
                stop_price = max(stop_price, entry_price * (1 + config.breakeven_buffer_bps / 10_000.0))
            else:
                stop_price = min(stop_price, entry_price * (1 - config.breakeven_buffer_bps / 10_000.0))
            breakeven_armed = True

    size_multiplier = config.long_size_multiplier if side > 0 else config.short_size_multiplier
    if book.startswith("failed_"):
        size_multiplier *= config.trap_size_multiplier
    trade_leverage = effective_leverage(signal_row, config)
    notional = max(0.0, equity * config.collateral_pct * trade_leverage * size_multiplier)
    amount = notional / entry_price
    partial_fraction = (
        clamp(config.partial_exit_fraction, 0.0, 1.0)
        if partial_exit_index is not None and partial_exit_price is not None
        else 0.0
    )
    partial_amount = amount * partial_fraction
    remaining_amount = amount - partial_amount
    gross_pnl = side * (exit_price - entry_price) * remaining_amount
    exit_notional = remaining_amount * exit_price
    if partial_amount:
        gross_pnl += side * (partial_exit_price - entry_price) * partial_amount
        exit_notional += partial_amount * partial_exit_price
    fees = (notional + exit_notional) * config.taker_fee_rate
    entry_slip = slippage_bps(entry_row, side, True, config)
    exit_slip = slippage_bps(df.iloc[exit_index], side, False, config)
    slippage_cost = entry_slip / 10_000.0 * notional + exit_slip / 10_000.0 * (remaining_amount * exit_price)
    if partial_amount and partial_exit_index is not None:
        partial_slip = slippage_bps(df.iloc[partial_exit_index], side, False, config)
        slippage_cost += partial_slip / 10_000.0 * (partial_amount * partial_exit_price)

    funding_pnl = 0.0
    for index in range(entry_index, exit_index + 1):
        row = df.iloc[index]
        funding_rate = row.get("funding_rate", 0.0)
        if pd.isna(funding_rate):
            funding_rate = 0.0
        mark = float(row["close"])
        active_amount = remaining_amount if partial_exit_index is not None and index > partial_exit_index else amount
        mark_notional = active_amount * mark
        funding_pnl += -side * mark_notional * float(funding_rate) * config.funding_bar_fraction

    net_pnl = gross_pnl + funding_pnl - fees - slippage_cost
    equity_after = equity + net_pnl
    margin = notional / trade_leverage if trade_leverage else notional
    return Trade(
        book=book,
        side="long" if side > 0 else "short",
        signal_index=signal_index,
        entry_index=entry_index,
        exit_index=exit_index,
        entry_timestamp=int(entry_row["timestamp"]),
        exit_timestamp=int(df.iloc[exit_index]["timestamp"]),
        entry_price=entry_price,
        exit_price=exit_price,
        close_type=close_type,
        notional=notional,
        leverage=trade_leverage,
        gross_pnl=gross_pnl,
        fees=fees,
        slippage_cost=slippage_cost,
        funding_pnl=funding_pnl,
        net_pnl=net_pnl,
        equity_after=equity_after,
        return_on_margin_pct=net_pnl / margin * 100.0 if margin else 0.0,
        holding_bars=exit_index - entry_index + 1,
        entry_regime=str(signal_row.get("regime_label", "")),
        entry_modifiers="" if pd.isna(signal_row.get("modifiers")) else str(signal_row.get("modifiers")),
        funding_rate=float(signal_row.get("funding_rate", 0.0) or 0.0),
        premium=float(signal_row.get("premium", 0.0) or 0.0),
        l2_spread_bps=float(signal_row.get("l2_spread_bps", np.nan)),
        l2_depth_top5_usd=float(signal_row.get("l2_depth_top5_usd", np.nan)),
    )


def run_portfolio(df: pd.DataFrame, masks: Dict[str, pd.Series], books: Iterable[str], config: SwingConfig) -> List[Trade]:
    book_list = list(books)
    side_by_book = {
        "trend_pullback_long": 1,
        "trend_breakout_continuation_long": 1,
        "trend_pullback_short": -1,
        "failed_breakdown_long": 1,
        "failed_breakout_short": -1,
    }
    priority = {
        "failed_breakdown_long": 0,
        "failed_breakout_short": 1,
        "trend_pullback_long": 2,
        "trend_pullback_short": 3,
        "trend_breakout_continuation_long": 4,
    }
    trades: List[Trade] = []
    equity = config.initial_capital
    index = 0
    while index < len(df) - 1 and equity > 0:
        active = [book for book in book_list if bool(masks[book].iloc[index])]
        if not active:
            index += 1
            continue
        active.sort(key=lambda book: priority.get(book, 99))
        book = active[0]
        trade = simulate_one_trade(df, index, book, side_by_book[book], equity, config)
        if trade is None:
            index += 1
            continue
        trades.append(trade)
        equity = trade.equity_after
        index = max(trade.exit_index + config.cooldown_bars, index + 1)
    return trades


def max_drawdown(equity: pd.Series, starting_capital: float) -> Tuple[float, float]:
    if equity.empty:
        return 0.0, 0.0
    curve = pd.concat([pd.Series([starting_capital]), equity.reset_index(drop=True)], ignore_index=True)
    peak = curve.cummax()
    drawdown = curve - peak
    pct = drawdown / peak.replace(0, np.nan)
    return float(-drawdown.min()), float(-pct.min() * 100.0)


def summarize_window(trades: pd.DataFrame, starting_capital: float, start: pd.Timestamp, end: pd.Timestamp) -> Dict[str, object]:
    empty_stats = {
        "starting_capital": starting_capital,
        "final_equity": starting_capital,
        "total_return_pct": 0.0,
        "cagr_pct": np.nan,
        "win_rate_pct": np.nan,
        "biggest_win": np.nan,
        "biggest_loss": np.nan,
        "avg_trade_pnl": np.nan,
        "avg_holding_seconds": np.nan,
        "max_drawdown_pct": 0.0,
        "sharpe": np.nan,
        "trades": 0,
        "avg_leverage": np.nan,
        "max_leverage": np.nan,
        "timeframe": f"{start.strftime('%Y-%m-%d')} -> {end.strftime('%Y-%m-%d')}",
    }
    if trades.empty:
        return empty_stats
    window = trades[(trades["exit_dt"] >= start) & (trades["exit_dt"] <= end)].copy()
    if window.empty:
        return empty_stats
    equity = starting_capital + window["net_pnl"].cumsum()
    final_equity = float(equity.iloc[-1])
    total_return = final_equity / starting_capital - 1.0
    years = max((end - start).total_seconds() / (365.25 * 24 * 3600), 1 / 365.25)
    cagr = (final_equity / starting_capital) ** (1 / years) - 1.0 if final_equity > 0 else np.nan
    dd, dd_pct = max_drawdown(equity, starting_capital)
    daily = (
        pd.DataFrame({"date": window["exit_dt"].dt.floor("D"), "equity": equity})
        .groupby("date")
        .tail(1)
        .set_index("date")["equity"]
        .sort_index()
    )
    daily = daily.reindex(pd.date_range(start.floor("D"), end.floor("D"), freq="D", tz="UTC")).ffill()
    daily = daily.fillna(starting_capital)
    returns = daily.pct_change().dropna()
    sharpe = returns.mean() / returns.std() * math.sqrt(365) if len(returns) > 1 and returns.std() else np.nan
    return {
        "starting_capital": starting_capital,
        "final_equity": final_equity,
        "total_return_pct": total_return * 100.0,
        "cagr_pct": cagr * 100.0,
        "win_rate_pct": float((window["net_pnl"] > 0).mean() * 100.0),
        "biggest_win": float(window["net_pnl"].max()),
        "biggest_loss": float(window["net_pnl"].min()),
        "avg_trade_pnl": float(window["net_pnl"].mean()),
        "avg_holding_seconds": float((window["exit_timestamp"] - window["entry_timestamp"]).mean()),
        "max_drawdown_pct": dd_pct,
        "sharpe": float(sharpe) if pd.notna(sharpe) else np.nan,
        "trades": int(len(window)),
        "avg_leverage": float(window["leverage"].mean()) if "leverage" in window else np.nan,
        "max_leverage": float(window["leverage"].max()) if "leverage" in window else np.nan,
        "timeframe": f"{start.strftime('%Y-%m-%d')} -> {end.strftime('%Y-%m-%d')}",
    }


def grouped_trade_stats(frame: pd.DataFrame, groups: List[str]) -> List[Dict[str, object]]:
    if frame.empty:
        return []
    return (
        frame.groupby(groups, dropna=False)["net_pnl"]
        .agg(
            trades="size",
            pnl="sum",
            avg_pnl="mean",
            win_rate_pct=lambda values: float((values > 0).mean() * 100.0),
        )
        .reset_index()
        .to_dict(orient="records")
    )


def summarize(
    trades: List[Trade],
    config: SwingConfig,
    label: str,
    data_start: pd.Timestamp,
    data_end: pd.Timestamp,
) -> Dict[str, object]:
    frame = pd.DataFrame([asdict(trade) for trade in trades])
    ytd_start = pd.Timestamp(year=data_end.year, month=1, day=1, tz="UTC")
    one_year_start = data_end - pd.Timedelta(days=365)
    windows = {
        "YTD": (max(data_start, ytd_start), data_end),
        "1 Year": (max(data_start, one_year_start), data_end),
        "Full": (data_start, data_end),
    }
    if frame.empty:
        return {
            "label": label,
            "first_trade": None,
            "last_trade": None,
            "trades": 0,
            "books": [],
            "sides": [],
            "windows": {
                window_name: summarize_window(frame, config.initial_capital, start, end)
                for window_name, (start, end) in windows.items()
            },
        }
    frame["entry_dt"] = pd.to_datetime(frame["entry_timestamp"], unit="s", utc=True)
    frame["exit_dt"] = pd.to_datetime(frame["exit_timestamp"], unit="s", utc=True)
    frame["entry_year"] = frame["entry_dt"].dt.year
    first = frame["entry_dt"].min()
    last = frame["exit_dt"].max()
    output = {
        "label": label,
        "first_trade": first.isoformat(),
        "last_trade": last.isoformat(),
        "trades": int(len(frame)),
        "books": frame.groupby("book")["net_pnl"].agg(["count", "sum", "mean"]).reset_index().to_dict(orient="records"),
        "sides": frame.groupby("side")["net_pnl"].agg(["count", "sum", "mean"]).reset_index().to_dict(orient="records"),
        "segments": {
            "year": grouped_trade_stats(frame, ["entry_year"]),
            "year_book": grouped_trade_stats(frame, ["entry_year", "book"]),
            "regime_book": grouped_trade_stats(frame, ["entry_regime", "book"]),
        },
        "windows": {},
    }
    for window_name, (start, end) in windows.items():
        stats = summarize_window(frame, config.initial_capital, start, end)
        output["windows"][window_name] = stats
    return output


def format_value(value: object, money: bool = False, pct: bool = False) -> str:
    if value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
        return "N/A"
    if money:
        return f"${float(value):,.2f}"
    if pct:
        return f"{float(value):.2f}%"
    return str(value)


def print_summary(summary: Dict[str, object]):
    print(f"\n== {summary['label']} ==")
    print(f"Trades: {summary.get('trades', 0)}")
    for window, stats in summary.get("windows", {}).items():
        print(f"\n[{window}]")
        print(f"Starting Capital: {format_value(stats['starting_capital'], money=True)}")
        print(f"Backtest Timeframe: {stats['timeframe']}")
        print(f"Final Equity: {format_value(stats['final_equity'], money=True)}")
        print(f"Total Return: {format_value(stats['total_return_pct'], pct=True)}")
        print("SPY Benchmark: N/A")
        print(f"CAGR: {format_value(stats['cagr_pct'], pct=True)}")
        print(f"Win Rate: {format_value(stats['win_rate_pct'], pct=True)}")
        print(f"Biggest Win per trade: {format_value(stats['biggest_win'], money=True)}")
        print(f"Biggest Loss per trade: {format_value(stats['biggest_loss'], money=True)}")
        print(f"Average P&L per trade: {format_value(stats['avg_trade_pnl'], money=True)}")
        holding = stats["avg_holding_seconds"]
        if pd.notna(holding):
            hours = int(holding // 3600)
            minutes = int((holding % 3600) // 60)
            print(f"Avg Holding Time: {hours}h {minutes}m")
        else:
            print("Avg Holding Time: N/A")
        print(f"Max Drawdown: {format_value(stats['max_drawdown_pct'], pct=True)}")
        print(f"Sharpe Ratio: {format_value(stats['sharpe'])}")
        print(f"Avg/Max Leverage: {format_value(stats.get('avg_leverage'))}x/{format_value(stats.get('max_leverage'))}x")
        print(f"Trades: {stats['trades']}")


def parse_args():
    parser = argparse.ArgumentParser(description="Backtest SOL swing hypotheses")
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--initial-capital", type=float, default=10_000.0)
    parser.add_argument("--collateral-pct", type=float, default=0.05)
    parser.add_argument("--leverage", type=float, default=10.0)
    parser.add_argument("--min-confidence", type=float, default=0.55)
    parser.add_argument("--disable-confidence-scaled-leverage", dest="confidence_scaled_leverage", action="store_false", default=True)
    parser.add_argument("--min-leverage", type=float, default=2.0)
    parser.add_argument("--full-leverage-confidence", type=float, default=0.9)
    parser.add_argument("--long-size-multiplier", type=float, default=1.0)
    parser.add_argument("--short-size-multiplier", type=float, default=1.0)
    parser.add_argument("--trap-size-multiplier", type=float, default=1.0)
    parser.add_argument("--stop-loss", type=float, default=0.006)
    parser.add_argument("--take-profit", type=float, default=0.04)
    parser.add_argument("--horizon-bars", type=int, default=432)
    parser.add_argument("--cooldown-bars", type=int, default=12)
    parser.add_argument("--disable-trend-shorts", action="store_true")
    parser.add_argument("--disable-trap-longs", dest="disable_trap_longs", action="store_true", default=True)
    parser.add_argument("--enable-trap-longs", dest="disable_trap_longs", action="store_false")
    parser.add_argument("--disable-trap-shorts", action="store_true")
    parser.add_argument("--require-short-momentum-confirmation", action="store_true")
    parser.add_argument("--max-trend-short-roc12", type=float, default=None)
    parser.add_argument("--min-trend-short-session-vwap-gap", type=float, default=None)
    parser.add_argument("--require-short-cvd-not-rising", action="store_true")
    parser.add_argument("--max-long-oi-change", type=float, default=None)
    parser.add_argument("--max-long-session-vwap-pct", type=float, default=0.008)
    parser.add_argument("--require-long-cvd-rising", dest="require_long_cvd_rising", action="store_true", default=True)
    parser.add_argument("--allow-long-cvd-not-rising", dest="require_long_cvd_rising", action="store_false")
    parser.add_argument("--require-long-oi-not-rising", action="store_true")
    parser.add_argument("--min-long-roc12", type=float, default=None)
    parser.add_argument("--disable-breakout-longs", dest="enable_breakout_longs", action="store_false", default=True)
    parser.add_argument("--breakout-long-min-score", type=float, default=0.75)
    parser.add_argument("--breakout-long-max-session-vwap-pct", type=float, default=0.025)
    parser.add_argument("--breakout-long-min-volume-expansion", type=float, default=1.0)
    parser.add_argument("--breakout-long-max-volume-expansion", type=float, default=2.5)
    parser.add_argument("--breakout-long-min-regime-vol-ratio", type=float, default=1.0)
    parser.add_argument("--breakout-long-min-roc12", type=float, default=0.0)
    parser.add_argument("--breakout-long-max-funding", type=float, default=0.000025)
    parser.add_argument("--breakout-long-min-premium", type=float, default=-0.0007)
    parser.add_argument("--breakout-long-max-premium", type=float, default=0.0004)
    parser.add_argument("--allow-breakout-long-cvd-not-rising", dest="breakout_long_require_cvd_rising", action="store_false", default=True)
    parser.add_argument("--allow-breakout-long-downtrends", dest="breakout_long_exclude_downtrends", action="store_false", default=True)
    parser.add_argument("--breakout-stop-loss", type=float, default=0.01)
    parser.add_argument("--breakout-take-profit", type=float, default=0.05)
    parser.add_argument("--min-trend-short-funding", type=float, default=0.000035)
    parser.add_argument("--min-trend-short-premium", type=float, default=0.0005)
    parser.add_argument("--use-atr-stop", action="store_true")
    parser.add_argument("--atr-stop-multiplier", type=float, default=0.45)
    parser.add_argument("--min-atr-stop", type=float, default=0.004)
    parser.add_argument("--max-atr-stop", type=float, default=0.009)
    parser.add_argument("--atr-reward-r", type=float, default=6.0)
    parser.add_argument("--partial-take-profit", action="store_true")
    parser.add_argument("--partial-take-profit-r", type=float, default=2.0)
    parser.add_argument("--partial-exit-fraction", type=float, default=0.5)
    parser.add_argument("--breakeven-trigger-r", type=float, default=0.0)
    parser.add_argument("--breakeven-buffer-bps", type=float, default=0.0)
    parser.add_argument("--stale-exit-bars", type=int, default=0)
    parser.add_argument("--stale-min-favorable-r", type=float, default=0.0)
    parser.add_argument("--taker-fee-rate", type=float, default=0.00045)
    return parser.parse_args()


def main():
    args = parse_args()
    config = SwingConfig(
        initial_capital=args.initial_capital,
        collateral_pct=args.collateral_pct,
        leverage=args.leverage,
        min_confidence=args.min_confidence,
        confidence_scaled_leverage=args.confidence_scaled_leverage,
        min_leverage=args.min_leverage,
        full_leverage_confidence=args.full_leverage_confidence,
        long_size_multiplier=args.long_size_multiplier,
        short_size_multiplier=args.short_size_multiplier,
        trap_size_multiplier=args.trap_size_multiplier,
        stop_loss=args.stop_loss,
        take_profit=args.take_profit,
        horizon_bars=args.horizon_bars,
        cooldown_bars=args.cooldown_bars,
        disable_trend_shorts=args.disable_trend_shorts,
        disable_trap_longs=args.disable_trap_longs,
        disable_trap_shorts=args.disable_trap_shorts,
        require_short_momentum_confirmation=args.require_short_momentum_confirmation,
        max_trend_short_roc12=args.max_trend_short_roc12,
        min_trend_short_session_vwap_gap=args.min_trend_short_session_vwap_gap,
        require_short_cvd_not_rising=args.require_short_cvd_not_rising,
        max_long_oi_change=args.max_long_oi_change,
        max_long_session_vwap_pct=args.max_long_session_vwap_pct,
        require_long_cvd_rising=args.require_long_cvd_rising,
        require_long_oi_not_rising=args.require_long_oi_not_rising,
        min_long_roc12=args.min_long_roc12,
        enable_breakout_longs=args.enable_breakout_longs,
        breakout_long_min_score=args.breakout_long_min_score,
        breakout_long_max_session_vwap_pct=args.breakout_long_max_session_vwap_pct,
        breakout_long_min_volume_expansion=args.breakout_long_min_volume_expansion,
        breakout_long_max_volume_expansion=args.breakout_long_max_volume_expansion,
        breakout_long_min_regime_vol_ratio=args.breakout_long_min_regime_vol_ratio,
        breakout_long_min_roc12=args.breakout_long_min_roc12,
        breakout_long_max_funding=args.breakout_long_max_funding,
        breakout_long_min_premium=args.breakout_long_min_premium,
        breakout_long_max_premium=args.breakout_long_max_premium,
        breakout_long_require_cvd_rising=args.breakout_long_require_cvd_rising,
        breakout_long_exclude_downtrends=args.breakout_long_exclude_downtrends,
        breakout_stop_loss=args.breakout_stop_loss,
        breakout_take_profit=args.breakout_take_profit,
        min_trend_short_funding=args.min_trend_short_funding,
        min_trend_short_premium=args.min_trend_short_premium,
        use_atr_stop=args.use_atr_stop,
        atr_stop_multiplier=args.atr_stop_multiplier,
        min_atr_stop=args.min_atr_stop,
        max_atr_stop=args.max_atr_stop,
        atr_reward_r=args.atr_reward_r,
        partial_take_profit=args.partial_take_profit,
        partial_take_profit_r=args.partial_take_profit_r,
        partial_exit_fraction=args.partial_exit_fraction,
        breakeven_trigger_r=args.breakeven_trigger_r,
        breakeven_buffer_bps=args.breakeven_buffer_bps,
        stale_exit_bars=args.stale_exit_bars,
        stale_min_favorable_r=args.stale_min_favorable_r,
        taker_fee_rate=args.taker_fee_rate,
    )
    df = load_table(args.input)
    data_times = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    data_start = data_times.min()
    data_end = data_times.max()
    masks = build_entry_masks(df, config)
    variants = {
        "trend_longs": ["trend_pullback_long"],
        "breakout_longs": ["trend_breakout_continuation_long"],
        "trend_shorts": ["trend_pullback_short"],
        "trap_longs": ["failed_breakdown_long"],
        "trap_shorts": ["failed_breakout_short"],
        "long_book": ["trend_pullback_long", "failed_breakdown_long"],
        "long_book_with_breakout": ["trend_pullback_long", "trend_breakout_continuation_long", "failed_breakdown_long"],
        "short_book": ["trend_pullback_short", "failed_breakout_short"],
        "combined": [
            "trend_pullback_long",
            "trend_pullback_short",
            "failed_breakdown_long",
            "failed_breakout_short",
        ],
        "combined_with_breakout": list(masks.keys()),
    }
    os.makedirs(args.output_dir, exist_ok=True)
    summaries = []
    mask_counts = {name: int(mask.sum()) for name, mask in masks.items()}
    for label, books in variants.items():
        trades = run_portfolio(df, masks, books, config)
        trade_frame = pd.DataFrame([asdict(trade) for trade in trades])
        trade_frame.to_csv(os.path.join(args.output_dir, f"{label}_trades.csv"), index=False)
        summary = summarize(trades, config, label, data_start, data_end)
        summaries.append(summary)
        if not args.quiet:
            print_summary(summary)
    with open(os.path.join(args.output_dir, "summary.json"), "w") as file:
        json.dump({"config": asdict(config), "mask_counts": mask_counts, "summaries": summaries}, file, indent=2)
    if not args.quiet:
        print(f"\nMask counts: {mask_counts}")
        print(f"Wrote artifacts to {args.output_dir}")


if __name__ == "__main__":
    main()
