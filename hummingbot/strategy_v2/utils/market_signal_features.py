from dataclasses import dataclass, field
from typing import Iterable, List, Optional

import pandas as pd


DEFAULT_ROC_PERIODS = (6, 12)


@dataclass
class MarketSignalFeatureConfig:
    ema_fast: int = 20
    ema_slow: int = 50
    rolling_vwap_window: int = 24
    rsi_length: int = 14
    roc_periods: Iterable[int] = field(default_factory=lambda: DEFAULT_ROC_PERIODS)
    volume_window: int = 24
    funding_trend_window: int = 24
    oi_change_window: int = 24
    premium_trend_window: int = 24
    trap_lookback: int = 6
    atr_length: int = 14
    realized_vol_length: int = 24


SIGNAL_FEATURE_COLUMNS = [
    "ema_fast",
    "ema_slow",
    "ema_bias",
    "ema_fast_above_slow",
    "rolling_vwap",
    "session_vwap",
    "price_vs_rolling_vwap_pct",
    "price_vs_session_vwap_pct",
    "price_above_rolling_vwap",
    "price_above_session_vwap",
    "rsi",
    "volume_sma",
    "volume_expansion",
    "volume_expanding",
    "taker_buy_imbalance",
    "taker_buy_pressure",
    "cvd_proxy",
    "cvd_proxy_change",
    "cvd_proxy_rising",
    "funding_rate_level",
    "funding_rate_ma",
    "funding_rate_trend",
    "open_interest_level",
    "open_interest_change_pct_feature",
    "premium_level",
    "premium_ma",
    "premium_trend",
    "basis_pct",
    "funding_positive",
    "funding_negative",
    "open_interest_rising",
    "premium_positive",
    "spread_pct_feature",
    "spread_bps",
    "depth_usd_feature",
    "bid_depth_usd_feature",
    "ask_depth_usd_feature",
    "depth_imbalance",
    "liquidity_score_feature",
    "liquidity_thin_feature",
    "atr_pct_feature",
    "realized_vol_feature",
    "recent_breakout_above",
    "recent_breakdown_below",
    "failed_breakout_above",
    "failed_breakdown_below",
    "trap_direction",
]


def _numeric(frame: pd.DataFrame, column: str, default: Optional[float] = None) -> pd.Series:
    if column in frame.columns:
        return pd.to_numeric(frame[column], errors="coerce")
    return pd.Series(default, index=frame.index, dtype="float64")


def _first_numeric(frame: pd.DataFrame, columns: List[str], default: Optional[float] = None) -> pd.Series:
    result = pd.Series(default, index=frame.index, dtype="float64")
    for column in columns:
        if column not in frame.columns:
            continue
        values = pd.to_numeric(frame[column], errors="coerce")
        result = result.where(result.notna(), values)
    return result


def _safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    denominator = denominator.where(denominator != 0)
    return numerator / denominator


def _true_range(df: pd.DataFrame) -> pd.Series:
    high = _numeric(df, "high")
    low = _numeric(df, "low")
    close = _numeric(df, "close")
    previous_close = close.shift(1)
    return pd.concat([
        high - low,
        (high - previous_close).abs(),
        (low - previous_close).abs(),
    ], axis=1).max(axis=1)


def _rsi(close: pd.Series, length: int) -> pd.Series:
    delta = close.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    avg_gain = gains.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    avg_loss = losses.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    rs = _safe_divide(avg_gain, avg_loss)
    rsi = 100 - (100 / (1 + rs))
    return rsi.where(avg_loss != 0, 100)


def _session_vwap(df: pd.DataFrame, typical_price: pd.Series, volume: pd.Series) -> pd.Series:
    if "timestamp" not in df.columns:
        session = pd.Series(0, index=df.index)
    else:
        session = pd.to_datetime(df["timestamp"], unit="s", utc=True, errors="coerce").dt.floor("D")
    cumulative_pv = (typical_price * volume).groupby(session).cumsum()
    cumulative_volume = volume.groupby(session).cumsum()
    return _safe_divide(cumulative_pv, cumulative_volume)


def _taker_buy_imbalance(df: pd.DataFrame, volume: pd.Series) -> pd.Series:
    taker_buy_base = _first_numeric(df, ["taker_buy_base_volume", "taker_buy_volume"])
    if taker_buy_base.notna().any():
        taker_sell_base = volume - taker_buy_base
        return _safe_divide(taker_buy_base - taker_sell_base, volume)

    taker_buy_quote = _first_numeric(df, ["taker_buy_quote_volume"])
    quote_volume = _first_numeric(df, ["quote_asset_volume"])
    taker_sell_quote = quote_volume - taker_buy_quote
    return _safe_divide(taker_buy_quote - taker_sell_quote, quote_volume)


def _merge_existing_or_compute_atr(df: pd.DataFrame, cfg: MarketSignalFeatureConfig) -> pd.Series:
    existing = _first_numeric(df, ["atr_pct"])
    if existing.notna().any():
        return existing
    close = _numeric(df, "close")
    atr = _true_range(df).rolling(cfg.atr_length, min_periods=cfg.atr_length).mean()
    return _safe_divide(atr, close)


def _merge_existing_or_compute_realized_vol(df: pd.DataFrame, cfg: MarketSignalFeatureConfig) -> pd.Series:
    existing = _first_numeric(df, ["realized_vol"])
    if existing.notna().any():
        return existing
    close = _numeric(df, "close")
    return close.pct_change().rolling(cfg.realized_vol_length, min_periods=cfg.realized_vol_length).std()


def enrich_market_signal_features(
    candles: pd.DataFrame,
    config: Optional[MarketSignalFeatureConfig] = None,
) -> pd.DataFrame:
    cfg = config or MarketSignalFeatureConfig()
    df = candles.copy()
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce")
        df = df.sort_values("timestamp").reset_index(drop=True)

    open_ = _numeric(df, "open")
    high = _numeric(df, "high")
    low = _numeric(df, "low")
    close = _numeric(df, "close")
    volume = _numeric(df, "volume").fillna(0)
    typical_price = (high + low + close) / 3

    df["ema_fast"] = close.ewm(span=cfg.ema_fast, adjust=False, min_periods=cfg.ema_fast).mean()
    df["ema_slow"] = close.ewm(span=cfg.ema_slow, adjust=False, min_periods=cfg.ema_slow).mean()
    df["ema_bias"] = 0
    df.loc[df["ema_fast"] > df["ema_slow"], "ema_bias"] = 1
    df.loc[df["ema_fast"] < df["ema_slow"], "ema_bias"] = -1
    df["ema_fast_above_slow"] = df["ema_fast"] > df["ema_slow"]

    rolling_pv = (typical_price * volume).rolling(cfg.rolling_vwap_window, min_periods=cfg.rolling_vwap_window).sum()
    rolling_volume = volume.rolling(cfg.rolling_vwap_window, min_periods=cfg.rolling_vwap_window).sum()
    df["rolling_vwap"] = _safe_divide(rolling_pv, rolling_volume)
    df["session_vwap"] = _session_vwap(df, typical_price, volume)
    df["price_vs_rolling_vwap_pct"] = _safe_divide(close - df["rolling_vwap"], df["rolling_vwap"])
    df["price_vs_session_vwap_pct"] = _safe_divide(close - df["session_vwap"], df["session_vwap"])
    df["price_above_rolling_vwap"] = close > df["rolling_vwap"]
    df["price_above_session_vwap"] = close > df["session_vwap"]

    df["rsi"] = _rsi(close, cfg.rsi_length)
    for period in cfg.roc_periods:
        df[f"roc_{period}"] = close.pct_change(period)
        df[f"roc_{period}_positive"] = df[f"roc_{period}"] > 0

    df["volume_sma"] = volume.rolling(cfg.volume_window, min_periods=cfg.volume_window).mean()
    df["volume_expansion"] = _safe_divide(volume, df["volume_sma"])
    df["volume_expanding"] = df["volume_expansion"] > 1
    df["taker_buy_imbalance"] = _taker_buy_imbalance(df, volume)
    df["taker_buy_pressure"] = df["taker_buy_imbalance"] > 0
    taker_delta = df["taker_buy_imbalance"].fillna(0) * volume
    df["cvd_proxy"] = taker_delta.cumsum()
    df["cvd_proxy_change"] = df["cvd_proxy"].diff(cfg.volume_window)
    df["cvd_proxy_rising"] = df["cvd_proxy_change"] > 0

    funding = _first_numeric(df, ["funding_rate", "funding"])
    df["funding_rate_level"] = funding
    df["funding_rate_ma"] = funding.rolling(cfg.funding_trend_window, min_periods=1).mean()
    df["funding_rate_trend"] = funding - df["funding_rate_ma"]
    df["funding_positive"] = funding > 0
    df["funding_negative"] = funding < 0

    open_interest = _first_numeric(df, ["open_interest", "oi"])
    df["open_interest_level"] = open_interest
    existing_oi_change = _first_numeric(df, ["open_interest_change_pct", "oi_change_pct"])
    computed_oi_change = open_interest.pct_change(cfg.oi_change_window)
    df["open_interest_change_pct_feature"] = existing_oi_change.where(existing_oi_change.notna(), computed_oi_change)
    df["open_interest_rising"] = df["open_interest_change_pct_feature"] > 0

    premium = _first_numeric(df, ["premium", "premium_pct"])
    df["premium_level"] = premium
    df["premium_ma"] = premium.rolling(cfg.premium_trend_window, min_periods=1).mean()
    df["premium_trend"] = premium - df["premium_ma"]
    df["premium_positive"] = premium > 0

    mark_price = _first_numeric(df, ["mark_price", "markPx"])
    oracle_price = _first_numeric(df, ["oracle_price", "oraclePx", "index_price"])
    df["basis_pct"] = premium.where(premium.notna(), _safe_divide(mark_price - oracle_price, oracle_price))

    df["spread_pct_feature"] = _first_numeric(df, ["spread_pct", "bid_ask_spread_pct"])
    df["spread_bps"] = df["spread_pct_feature"] * 10_000
    df["depth_usd_feature"] = _first_numeric(df, ["depth_usd", "order_book_depth_usd", "market_depth_usd"])
    df["bid_depth_usd_feature"] = _first_numeric(df, ["bid_depth_usd"])
    df["ask_depth_usd_feature"] = _first_numeric(df, ["ask_depth_usd"])
    total_side_depth = df["bid_depth_usd_feature"] + df["ask_depth_usd_feature"]
    df["depth_imbalance"] = _safe_divide(df["bid_depth_usd_feature"] - df["ask_depth_usd_feature"], total_side_depth)
    df["liquidity_score_feature"] = _first_numeric(df, ["liquidity_score"])
    df["liquidity_thin_feature"] = df["liquidity_score_feature"] < 0.5
    df["atr_pct_feature"] = _merge_existing_or_compute_atr(df, cfg)
    df["realized_vol_feature"] = _merge_existing_or_compute_realized_vol(df, cfg)

    prior_high = _first_numeric(df, ["prior_range_high"])
    prior_low = _first_numeric(df, ["prior_range_low"])
    raw_breakout = _first_numeric(df, ["raw_accepted_above_range", "accepted_above_range"], 0).fillna(0).astype(bool)
    raw_breakdown = _first_numeric(df, ["raw_accepted_below_range", "accepted_below_range"], 0).fillna(0).astype(bool)
    if not raw_breakout.any() and prior_high.notna().any():
        raw_breakout = close > prior_high
    if not raw_breakdown.any() and prior_low.notna().any():
        raw_breakdown = close < prior_low

    recent_breakout = raw_breakout.rolling(cfg.trap_lookback, min_periods=1).max().astype(bool)
    recent_breakdown = raw_breakdown.rolling(cfg.trap_lookback, min_periods=1).max().astype(bool)
    back_inside_from_above = prior_high.notna() & (close < prior_high)
    back_inside_from_below = prior_low.notna() & (close > prior_low)
    rejected_above = _first_numeric(df, ["rejected_above_range"], 0).fillna(0).astype(bool)
    rejected_below = _first_numeric(df, ["rejected_below_range"], 0).fillna(0).astype(bool)

    df["recent_breakout_above"] = recent_breakout
    df["recent_breakdown_below"] = recent_breakdown
    df["failed_breakout_above"] = recent_breakout & (back_inside_from_above | rejected_above)
    df["failed_breakdown_below"] = recent_breakdown & (back_inside_from_below | rejected_below)
    df["trap_direction"] = 0
    df.loc[df["failed_breakout_above"], "trap_direction"] = -1
    df.loc[df["failed_breakdown_below"], "trap_direction"] = 1

    return df
