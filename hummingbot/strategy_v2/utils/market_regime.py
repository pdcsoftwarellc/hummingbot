from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional

import pandas as pd


class MarketRegime(str, Enum):
    RANGE_CHOP = "range_chop"
    UPTREND = "uptrend"
    DOWNTREND = "downtrend"
    BREAKOUT = "breakout"
    BREAKDOWN = "breakdown"
    SQUEEZE_RISK = "squeeze_risk"
    HIGH_VOLATILITY_DANGER = "high_volatility_danger"
    NO_TRADE = "no_trade"


class GridBias(str, Enum):
    NEUTRAL = "neutral"
    LONG = "long"
    SHORT = "short"
    NONE = "none"


class RegimeAction(str, Enum):
    NEUTRAL_GRID = "neutral_grid"
    LONG_BIASED_GRID = "long_biased_grid"
    SHORT_BIASED_GRID = "short_biased_grid"
    TREND_FOLLOW_LONG_OR_PAUSE = "trend_follow_long_or_pause"
    TREND_FOLLOW_SHORT_OR_PAUSE = "trend_follow_short_or_pause"
    REDUCE_OR_DIRECTIONAL = "reduce_or_directional"
    WIDEN_REDUCE_OR_DISABLE = "widen_reduce_or_disable"
    BOT_OFF = "bot_off"


@dataclass
class MarketRegimeConfig:
    range_lookback: int = 48
    trend_lookback: int = 24
    atr_length: int = 14
    realized_vol_length: int = 24
    acceptance_bars: int = 3
    boundary_touch_tolerance_pct: float = 0.003
    min_boundary_touches: int = 2
    max_chop_range_width_pct: float = 0.08
    min_trend_slope_pct: float = 0.01
    max_balanced_range_slope_pct: float = 0.03
    high_vol_atr_pct: float = 0.035
    high_vol_multiplier: float = 2.0
    min_liquidity_score: float = 0.2
    squeeze_crowding_threshold: float = 0.75
    squeeze_liquidation_distance_pct: float = 0.015

    @property
    def min_records(self) -> int:
        return max(
            self.range_lookback + self.acceptance_bars + 1,
            self.trend_lookback * 2,
            self.atr_length * 2,
            self.realized_vol_length * 2,
        )


@dataclass
class MarketContext:
    liquidity_score: Optional[float] = None
    crowding_score: Optional[float] = None
    nearest_liquidation_distance_pct: Optional[float] = None
    liquidation_pressure_score: Optional[float] = None


@dataclass
class MarketRegimeReport:
    label: MarketRegime
    action: RegimeAction
    grid_bias: GridBias
    confidence: float
    allow_longs: bool
    allow_shorts: bool
    risk_multiplier: float
    reason: str
    features: Dict[str, float] = field(default_factory=dict)


class MarketRegimeDetector:
    """
    Classifies market structure into reusable Strategy V2 labels.

    The detector intentionally returns both a label and an execution policy. Controllers can use the
    label for analytics and the policy fields for gating entries, grid sides, sizing, and cooldowns.
    """

    def __init__(self, config: Optional[MarketRegimeConfig] = None):
        self.config = config or MarketRegimeConfig()

    def classify(self, candles: pd.DataFrame, context: Optional[MarketContext] = None) -> MarketRegimeReport:
        context = context or MarketContext()
        if candles is None or len(candles) < self.config.min_records:
            return self._report(
                label=MarketRegime.NO_TRADE,
                confidence=1.0,
                reason=f"insufficient candles: {0 if candles is None else len(candles)} < {self.config.min_records}",
            )

        df = candles.copy()
        if "timestamp" in df.columns:
            df = df.sort_values("timestamp")
        else:
            df = df.sort_index()
        numeric_columns = ["open", "high", "low", "close", "volume"]
        missing_columns = [column for column in numeric_columns if column not in df.columns]
        if missing_columns:
            return self._report(
                label=MarketRegime.NO_TRADE,
                confidence=1.0,
                reason=f"missing candle columns: {', '.join(missing_columns)}",
            )
        df[numeric_columns] = df[numeric_columns].apply(pd.to_numeric, errors="coerce")
        df = df.dropna(subset=numeric_columns)
        if len(df) < self.config.min_records:
            return self._report(
                label=MarketRegime.NO_TRADE,
                confidence=1.0,
                reason=f"insufficient clean candles: {len(df)} < {self.config.min_records}",
            )

        features = self._features(df, context)

        if features["liquidity_bad"]:
            return self._report(
                label=MarketRegime.NO_TRADE,
                confidence=1.0,
                reason="liquidity score below threshold",
                features=features,
            )
        if features["high_vol_danger"]:
            return self._report(
                label=MarketRegime.HIGH_VOLATILITY_DANGER,
                confidence=min(1.0, features["vol_ratio"] / self.config.high_vol_multiplier),
                reason="ATR/realized volatility expanded beyond danger threshold",
                features=features,
            )
        if features["squeeze_risk"]:
            return self._report(
                label=MarketRegime.SQUEEZE_RISK,
                confidence=max(features["crowding_score"], features["liquidation_pressure_score"]),
                reason="crowded positioning or nearby liquidations",
                features=features,
            )
        if features["accepted_above_range"]:
            return self._report(
                label=MarketRegime.BREAKOUT,
                confidence=min(1.0, features["breakout_distance_pct"] / max(features["atr_pct"], 0.0001)),
                reason="price accepted above prior range",
                features=features,
            )
        if features["accepted_below_range"]:
            return self._report(
                label=MarketRegime.BREAKDOWN,
                confidence=min(1.0, features["breakdown_distance_pct"] / max(features["atr_pct"], 0.0001)),
                reason="price accepted below prior range",
                features=features,
            )
        if features["higher_highs"] and features["higher_lows"] and features["trend_slope_pct"] > self.config.min_trend_slope_pct:
            return self._report(
                label=MarketRegime.UPTREND,
                confidence=min(1.0, features["trend_slope_pct"] / (self.config.min_trend_slope_pct * 3)),
                reason="higher highs and higher lows with positive slope",
                features=features,
            )
        if features["lower_highs"] and features["lower_lows"] and features["trend_slope_pct"] < -self.config.min_trend_slope_pct:
            return self._report(
                label=MarketRegime.DOWNTREND,
                confidence=min(1.0, abs(features["trend_slope_pct"]) / (self.config.min_trend_slope_pct * 3)),
                reason="lower highs and lower lows with negative slope",
                features=features,
            )
        if features["inside_range"] and features["clear_boundaries"]:
            return self._report(
                label=MarketRegime.RANGE_CHOP,
                confidence=min(1.0, features["boundary_touch_count"] / (self.config.min_boundary_touches * 2)),
                reason="price oscillating inside clear boundaries",
                features=features,
            )

        return self._report(
            label=MarketRegime.NO_TRADE,
            confidence=0.7,
            reason="structure unclear",
            features=features,
        )

    def _features(self, df: pd.DataFrame, context: MarketContext) -> Dict[str, float]:
        cfg = self.config
        close = df["close"]
        high = df["high"]
        low = df["low"]
        previous_close = close.shift(1)
        true_range = pd.concat([
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ], axis=1).max(axis=1)
        atr = true_range.rolling(cfg.atr_length).mean()
        atr_pct = float((atr / close).iloc[-1])
        realized_vol = float(close.pct_change().rolling(cfg.realized_vol_length).std().iloc[-1])
        realized_vol_median = float(close.pct_change().rolling(cfg.realized_vol_length).std().rolling(cfg.realized_vol_length).median().iloc[-1])
        vol_ratio = realized_vol / realized_vol_median if realized_vol_median > 0 else 1.0

        prior_high = float(high.shift(cfg.acceptance_bars).rolling(cfg.range_lookback).max().iloc[-1])
        prior_low = float(low.shift(cfg.acceptance_bars).rolling(cfg.range_lookback).min().iloc[-1])
        last_close = float(close.iloc[-1])
        range_width_pct = (prior_high - prior_low) / last_close if last_close > 0 else 0
        recent_closes = close.tail(cfg.acceptance_bars)
        raw_accepted_above_range = bool((recent_closes > prior_high).all())
        raw_accepted_below_range = bool((recent_closes < prior_low).all())

        recent = df.tail(cfg.trend_lookback)
        previous = df.iloc[-cfg.trend_lookback * 2:-cfg.trend_lookback]
        recent_high = float(recent["high"].max())
        recent_low = float(recent["low"].min())
        previous_high = float(previous["high"].max())
        previous_low = float(previous["low"].min())
        trend_slope_pct = float(close.iloc[-1] / close.iloc[-cfg.trend_lookback] - 1)
        prior_trend_slope_pct = float(
            close.iloc[-cfg.acceptance_bars - 1] / close.iloc[-cfg.acceptance_bars - cfg.trend_lookback] - 1
        )

        prior_window = df.iloc[-cfg.range_lookback - cfg.acceptance_bars:-cfg.acceptance_bars]
        near_upper = (prior_window["high"] >= prior_high * (1 - cfg.boundary_touch_tolerance_pct)).sum()
        near_lower = (prior_window["low"] <= prior_low * (1 + cfg.boundary_touch_tolerance_pct)).sum()
        boundary_touch_count = int(min(near_upper, near_lower))
        inside_range = prior_low <= last_close <= prior_high
        balanced_prior_range = (
            range_width_pct <= cfg.max_chop_range_width_pct and
            boundary_touch_count >= cfg.min_boundary_touches and
            abs(prior_trend_slope_pct) <= cfg.max_balanced_range_slope_pct
        )
        clear_boundaries = range_width_pct <= cfg.max_chop_range_width_pct and boundary_touch_count >= cfg.min_boundary_touches
        accepted_above_range = raw_accepted_above_range and balanced_prior_range
        accepted_below_range = raw_accepted_below_range and balanced_prior_range

        liquidity_score = 1.0 if context.liquidity_score is None else float(context.liquidity_score)
        crowding_score = 0.0 if context.crowding_score is None else float(context.crowding_score)
        liquidation_distance = 1.0 if context.nearest_liquidation_distance_pct is None else float(context.nearest_liquidation_distance_pct)
        liquidation_pressure_score = 0.0 if context.liquidation_pressure_score is None else float(context.liquidation_pressure_score)

        squeeze_risk = (
            crowding_score >= cfg.squeeze_crowding_threshold or
            liquidation_distance <= cfg.squeeze_liquidation_distance_pct or
            liquidation_pressure_score >= cfg.squeeze_crowding_threshold
        )
        high_vol_danger = atr_pct >= cfg.high_vol_atr_pct or vol_ratio >= cfg.high_vol_multiplier

        return {
            "last_close": last_close,
            "prior_range_high": prior_high,
            "prior_range_low": prior_low,
            "range_width_pct": range_width_pct,
            "atr_pct": atr_pct,
            "realized_vol": realized_vol,
            "vol_ratio": vol_ratio,
            "trend_slope_pct": trend_slope_pct,
            "prior_trend_slope_pct": prior_trend_slope_pct,
            "higher_highs": float(recent_high > previous_high),
            "higher_lows": float(recent_low > previous_low),
            "lower_highs": float(recent_high < previous_high),
            "lower_lows": float(recent_low < previous_low),
            "accepted_above_range": float(accepted_above_range),
            "accepted_below_range": float(accepted_below_range),
            "raw_accepted_above_range": float(raw_accepted_above_range),
            "raw_accepted_below_range": float(raw_accepted_below_range),
            "breakout_distance_pct": max(0.0, (last_close - prior_high) / last_close),
            "breakdown_distance_pct": max(0.0, (prior_low - last_close) / last_close),
            "inside_range": float(inside_range),
            "clear_boundaries": float(clear_boundaries),
            "balanced_prior_range": float(balanced_prior_range),
            "boundary_touch_count": float(boundary_touch_count),
            "liquidity_score": liquidity_score,
            "liquidity_bad": float(liquidity_score < cfg.min_liquidity_score),
            "crowding_score": crowding_score,
            "nearest_liquidation_distance_pct": liquidation_distance,
            "liquidation_pressure_score": liquidation_pressure_score,
            "squeeze_risk": float(squeeze_risk),
            "high_vol_danger": float(high_vol_danger),
        }

    @staticmethod
    def _report(
        label: MarketRegime,
        confidence: float,
        reason: str,
        features: Optional[Dict[str, float]] = None,
    ) -> MarketRegimeReport:
        policies = {
            MarketRegime.RANGE_CHOP: (RegimeAction.NEUTRAL_GRID, GridBias.NEUTRAL, True, True, 1.0),
            MarketRegime.UPTREND: (RegimeAction.LONG_BIASED_GRID, GridBias.LONG, True, False, 1.0),
            MarketRegime.DOWNTREND: (RegimeAction.SHORT_BIASED_GRID, GridBias.SHORT, False, True, 1.0),
            MarketRegime.BREAKOUT: (RegimeAction.TREND_FOLLOW_LONG_OR_PAUSE, GridBias.LONG, True, False, 0.7),
            MarketRegime.BREAKDOWN: (RegimeAction.TREND_FOLLOW_SHORT_OR_PAUSE, GridBias.SHORT, False, True, 0.7),
            MarketRegime.SQUEEZE_RISK: (RegimeAction.REDUCE_OR_DIRECTIONAL, GridBias.NONE, False, False, 0.35),
            MarketRegime.HIGH_VOLATILITY_DANGER: (RegimeAction.WIDEN_REDUCE_OR_DISABLE, GridBias.NONE, False, False, 0.25),
            MarketRegime.NO_TRADE: (RegimeAction.BOT_OFF, GridBias.NONE, False, False, 0.0),
        }
        action, grid_bias, allow_longs, allow_shorts, risk_multiplier = policies[label]
        return MarketRegimeReport(
            label=label,
            action=action,
            grid_bias=grid_bias,
            confidence=max(0.0, min(1.0, confidence)),
            allow_longs=allow_longs,
            allow_shorts=allow_shorts,
            risk_multiplier=risk_multiplier,
            reason=reason,
            features=features or {},
        )
