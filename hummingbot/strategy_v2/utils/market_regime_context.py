import math
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from hummingbot.strategy_v2.utils.market_regime import MarketContext


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def _as_int(value: Any) -> Optional[int]:
    result = _as_float(value)
    return None if result is None else int(result)


def _first_float(row: Mapping[str, Any], aliases: tuple) -> Optional[float]:
    for alias in aliases:
        if alias not in row:
            continue
        value = _as_float(row[alias])
        if value is not None:
            return value
    return None


def _clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _score_between(value: Optional[float], low: float, high: float) -> Optional[float]:
    if value is None:
        return None
    if high <= low:
        return None
    return _clip((value - low) / (high - low))


@dataclass
class MarketContextInput:
    """
    Raw market-context inputs used to build the normalized MarketContext consumed by the detector.

    liquidation_flush_direction convention:
    1 means long/rebound opportunity after long liquidation dominance.
    -1 means short/fade opportunity after short liquidation dominance.
    """

    funding_rate: Optional[float] = None
    liquidity_score: Optional[float] = None
    spread_pct: Optional[float] = None
    depth_usd: Optional[float] = None
    bid_depth_usd: Optional[float] = None
    ask_depth_usd: Optional[float] = None
    open_interest_change_pct: Optional[float] = None
    long_short_ratio: Optional[float] = None
    crowding_score: Optional[float] = None
    close_price: Optional[float] = None
    nearest_liquidation_price: Optional[float] = None
    nearest_liquidation_distance_pct: Optional[float] = None
    liquidation_notional_usd: Optional[float] = None
    long_liquidation_notional_usd: Optional[float] = None
    short_liquidation_notional_usd: Optional[float] = None
    liquidation_pressure_score: Optional[float] = None
    liquidation_flush_score: Optional[float] = None
    liquidation_flush_direction: Optional[int] = None

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "MarketContextInput":
        aliases: Dict[str, tuple] = {
            "funding_rate": ("funding_rate", "funding", "predicted_funding_rate"),
            "liquidity_score": ("liquidity_score",),
            "spread_pct": ("spread_pct", "bid_ask_spread_pct"),
            "depth_usd": ("depth_usd", "order_book_depth_usd", "market_depth_usd"),
            "bid_depth_usd": ("bid_depth_usd",),
            "ask_depth_usd": ("ask_depth_usd",),
            "open_interest_change_pct": ("open_interest_change_pct", "oi_change_pct", "open_interest_change_24h_pct"),
            "long_short_ratio": ("long_short_ratio", "account_long_short_ratio"),
            "crowding_score": ("crowding_score",),
            "close_price": ("close_price", "close", "mark_price", "mid_price"),
            "nearest_liquidation_price": ("nearest_liquidation_price", "nearest_liq_price"),
            "nearest_liquidation_distance_pct": ("nearest_liquidation_distance_pct", "nearest_liq_distance_pct"),
            "liquidation_notional_usd": ("liquidation_notional_usd", "liquidations_usd"),
            "long_liquidation_notional_usd": ("long_liquidation_notional_usd", "long_liquidations_usd"),
            "short_liquidation_notional_usd": ("short_liquidation_notional_usd", "short_liquidations_usd"),
            "liquidation_pressure_score": ("liquidation_pressure_score",),
            "liquidation_flush_score": ("liquidation_flush_score",),
            "liquidation_flush_direction": ("liquidation_flush_direction",),
        }
        values = {}
        for field_name, field_aliases in aliases.items():
            values[field_name] = _first_float(row, field_aliases)
        values["liquidation_flush_direction"] = _as_int(values["liquidation_flush_direction"])
        return cls(**values)


@dataclass
class MarketContextBuilderConfig:
    min_depth_usd: float = 250_000
    full_depth_usd: float = 2_000_000
    healthy_spread_pct: float = 0.0005
    thin_spread_pct: float = 0.003
    crowded_open_interest_change_pct: float = 0.12
    crowded_long_short_ratio: float = 1.8
    crowded_short_long_ratio: float = 0.55
    liquidation_pressure_notional_usd: float = 10_000_000
    liquidation_flush_notional_usd: float = 25_000_000


SOL_1H_CONTEXT_CONFIG = MarketContextBuilderConfig(
    min_depth_usd=500_000,
    full_depth_usd=5_000_000,
    healthy_spread_pct=0.0004,
    thin_spread_pct=0.0025,
    crowded_open_interest_change_pct=0.10,
    crowded_long_short_ratio=1.7,
    crowded_short_long_ratio=0.60,
    liquidation_pressure_notional_usd=8_000_000,
    liquidation_flush_notional_usd=20_000_000,
)


class MarketContextBuilder:
    def __init__(self, config: Optional[MarketContextBuilderConfig] = None):
        self.config = config or MarketContextBuilderConfig()

    @classmethod
    def sol_1h(cls) -> "MarketContextBuilder":
        return cls(SOL_1H_CONTEXT_CONFIG)

    def build(self, inputs: MarketContextInput) -> MarketContext:
        return MarketContext(
            liquidity_score=self._liquidity_score(inputs),
            crowding_score=self._crowding_score(inputs),
            nearest_liquidation_distance_pct=self._nearest_liquidation_distance_pct(inputs),
            liquidation_pressure_score=self._liquidation_pressure_score(inputs),
            funding_rate=inputs.funding_rate,
            liquidation_flush_score=self._liquidation_flush_score(inputs),
            liquidation_flush_direction=self._liquidation_flush_direction(inputs),
        )

    def build_from_mapping(self, row: Mapping[str, Any]) -> MarketContext:
        return self.build(MarketContextInput.from_mapping(row))

    def _liquidity_score(self, inputs: MarketContextInput) -> Optional[float]:
        if inputs.liquidity_score is not None:
            return _clip(inputs.liquidity_score)

        depth_usd = inputs.depth_usd
        if depth_usd is None and inputs.bid_depth_usd is not None and inputs.ask_depth_usd is not None:
            depth_usd = min(inputs.bid_depth_usd, inputs.ask_depth_usd) * 2
        depth_score = _score_between(depth_usd, self.config.min_depth_usd, self.config.full_depth_usd)

        spread_score = None
        if inputs.spread_pct is not None:
            spread_penalty = _score_between(
                inputs.spread_pct,
                self.config.healthy_spread_pct,
                self.config.thin_spread_pct,
            )
            if spread_penalty is not None:
                spread_score = 1 - spread_penalty

        scores = [score for score in [depth_score, spread_score] if score is not None]
        return min(scores) if scores else None

    def _crowding_score(self, inputs: MarketContextInput) -> Optional[float]:
        if inputs.crowding_score is not None:
            return _clip(inputs.crowding_score)

        scores = []
        if inputs.open_interest_change_pct is not None:
            scores.append(_clip(abs(inputs.open_interest_change_pct) / self.config.crowded_open_interest_change_pct))
        if inputs.long_short_ratio is not None and inputs.long_short_ratio > 0:
            if inputs.long_short_ratio >= 1:
                denominator = self.config.crowded_long_short_ratio - 1
                if denominator > 0:
                    scores.append(_clip((inputs.long_short_ratio - 1) / denominator))
            else:
                denominator = 1 - self.config.crowded_short_long_ratio
                if denominator > 0:
                    scores.append(_clip((1 - inputs.long_short_ratio) / denominator))
        return max(scores) if scores else None

    def _nearest_liquidation_distance_pct(self, inputs: MarketContextInput) -> Optional[float]:
        if inputs.nearest_liquidation_distance_pct is not None:
            return max(0.0, inputs.nearest_liquidation_distance_pct)
        if (
            inputs.nearest_liquidation_price is None or
            inputs.close_price is None or
            inputs.close_price <= 0
        ):
            return None
        return abs(inputs.nearest_liquidation_price - inputs.close_price) / inputs.close_price

    def _liquidation_pressure_score(self, inputs: MarketContextInput) -> Optional[float]:
        if inputs.liquidation_pressure_score is not None:
            return _clip(inputs.liquidation_pressure_score)
        notional = self._total_liquidation_notional(inputs)
        if notional is None:
            return None
        return _clip(notional / self.config.liquidation_pressure_notional_usd)

    def _liquidation_flush_score(self, inputs: MarketContextInput) -> Optional[float]:
        if inputs.liquidation_flush_score is not None:
            return _clip(inputs.liquidation_flush_score)
        notional = self._total_liquidation_notional(inputs)
        if notional is None:
            return None
        return _clip(notional / self.config.liquidation_flush_notional_usd)

    @staticmethod
    def _total_liquidation_notional(inputs: MarketContextInput) -> Optional[float]:
        if inputs.liquidation_notional_usd is not None:
            return inputs.liquidation_notional_usd
        notionals = [
            value for value in [
                inputs.long_liquidation_notional_usd,
                inputs.short_liquidation_notional_usd,
            ] if value is not None
        ]
        return sum(notionals) if notionals else None

    @staticmethod
    def _liquidation_flush_direction(inputs: MarketContextInput) -> Optional[int]:
        if inputs.liquidation_flush_direction in {-1, 0, 1}:
            return inputs.liquidation_flush_direction
        if inputs.long_liquidation_notional_usd is None or inputs.short_liquidation_notional_usd is None:
            return None
        if inputs.long_liquidation_notional_usd > inputs.short_liquidation_notional_usd:
            return 1
        if inputs.short_liquidation_notional_usd > inputs.long_liquidation_notional_usd:
            return -1
        return 0
