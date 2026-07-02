from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from hummingbot.strategy_v2.utils.market_regime import (
    MarketRegime,
    MarketRegimeModifier,
    MarketRegimeReport,
    MarketRiskState,
)


HARD_BLOCK_RISK_STATES = {
    MarketRiskState.LIQUIDITY_BLOCKED,
    MarketRiskState.SQUEEZE_RISK,
    MarketRiskState.HIGH_VOLATILITY_DANGER,
}
HARD_BLOCK_REGIMES = {
    MarketRegime.SQUEEZE_RISK,
    MarketRegime.HIGH_VOLATILITY_DANGER,
    MarketRegime.NO_TRADE,
}


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _parse_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_regime(value: Any) -> MarketRegime:
    if isinstance(value, MarketRegime):
        return value
    try:
        return MarketRegime(str(value))
    except ValueError:
        return MarketRegime.NO_TRADE


def _parse_risk_state(value: Any) -> MarketRiskState:
    if isinstance(value, MarketRiskState):
        return value
    try:
        return MarketRiskState(str(value))
    except ValueError:
        return MarketRiskState.NORMAL


def _parse_modifiers(value: Any) -> list[MarketRegimeModifier]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        modifiers = []
        for item in value:
            if isinstance(item, MarketRegimeModifier):
                modifiers.append(item)
            else:
                modifiers.extend(_parse_modifiers(item))
        return modifiers
    if isinstance(value, float) and str(value) == "nan":
        return []
    modifiers = []
    for raw_modifier in str(value).split(","):
        raw_modifier = raw_modifier.strip()
        if not raw_modifier:
            continue
        try:
            modifiers.append(MarketRegimeModifier(raw_modifier))
        except ValueError:
            continue
    return modifiers


def _parse_blockers(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(blocker).strip() for blocker in value if str(blocker).strip()]
    return [blocker.strip() for blocker in str(value).split(",") if blocker.strip()]


@dataclass(frozen=True)
class MarketExecutionPolicy:
    """
    Shared execution permission envelope derived from a market-regime report.

    Strategies may still specialize entries and exits, but hard blocks, side
    permissions, and risk multipliers should flow through this policy so soft
    risk states like liquidity_thin are not reinterpreted inconsistently.
    """

    price_regime: MarketRegime
    regime_label: MarketRegime
    risk_state: MarketRiskState
    confidence: float
    allow_longs: bool
    allow_shorts: bool
    long_risk_multiplier: float
    short_risk_multiplier: float
    modifiers: list[MarketRegimeModifier] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)

    @classmethod
    def from_report(cls, report: MarketRegimeReport) -> "MarketExecutionPolicy":
        regime_label = getattr(report, "label", MarketRegime.NO_TRADE)
        return cls(
            price_regime=_parse_regime(getattr(report, "price_regime", regime_label)),
            regime_label=_parse_regime(regime_label),
            risk_state=_parse_risk_state(getattr(report, "risk_state", MarketRiskState.NORMAL)),
            confidence=_parse_float(getattr(report, "confidence", 0.0)),
            allow_longs=_parse_bool(getattr(report, "allow_longs", False)),
            allow_shorts=_parse_bool(getattr(report, "allow_shorts", False)),
            long_risk_multiplier=_parse_float(getattr(report, "long_risk_multiplier", 0.0)),
            short_risk_multiplier=_parse_float(getattr(report, "short_risk_multiplier", 0.0)),
            modifiers=_parse_modifiers(getattr(report, "modifiers", [])),
            blocked_by=_parse_blockers(getattr(report, "blocked_by", [])),
        )

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "MarketExecutionPolicy":
        regime_label = _parse_regime(row.get("regime_label", row.get("label", MarketRegime.NO_TRADE.value)))
        return cls(
            price_regime=_parse_regime(row.get("price_regime", regime_label.value)),
            regime_label=regime_label,
            risk_state=_parse_risk_state(row.get("risk_state", MarketRiskState.NORMAL.value)),
            confidence=_parse_float(row.get("confidence", 0.0)),
            allow_longs=_parse_bool(row.get("allow_longs", False)),
            allow_shorts=_parse_bool(row.get("allow_shorts", False)),
            long_risk_multiplier=_parse_float(row.get("long_risk_multiplier", 0.0)),
            short_risk_multiplier=_parse_float(row.get("short_risk_multiplier", 0.0)),
            modifiers=_parse_modifiers(row.get("modifiers")),
            blocked_by=_parse_blockers(row.get("blocked_by")),
        )

    @property
    def is_hard_blocked(self) -> bool:
        return self.risk_state in HARD_BLOCK_RISK_STATES or self.regime_label in HARD_BLOCK_REGIMES

    def is_blocked(
        self,
        extra_hard_modifiers: Optional[set[MarketRegimeModifier]] = None,
        extra_hard_regimes: Optional[set[MarketRegime]] = None,
    ) -> bool:
        if self.is_hard_blocked:
            return True
        if extra_hard_regimes and self.regime_label in extra_hard_regimes:
            return True
        if not extra_hard_modifiers:
            return False
        return bool(set(self.modifiers).intersection(extra_hard_modifiers))

    @property
    def can_open_long(self) -> bool:
        return not self.is_hard_blocked and self.allow_longs and self.long_risk_multiplier > 0

    @property
    def can_open_short(self) -> bool:
        return not self.is_hard_blocked and self.allow_shorts and self.short_risk_multiplier > 0

    def side_risk_multiplier(self, side: int | str) -> float:
        if side in {1, "long", "buy"}:
            return self.long_risk_multiplier
        if side in {-1, "short", "sell"}:
            return self.short_risk_multiplier
        return 0.0

    def allows_side(self, side: int | str) -> bool:
        if side in {1, "long", "buy"}:
            return self.can_open_long
        if side in {-1, "short", "sell"}:
            return self.can_open_short
        return False

    def has_modifier(self, modifier: MarketRegimeModifier) -> bool:
        return modifier in self.modifiers

    def directional_signal(
        self,
        min_confidence: Optional[float] = None,
        extra_hard_modifiers: Optional[set[MarketRegimeModifier]] = None,
        extra_hard_regimes: Optional[set[MarketRegime]] = None,
    ) -> int:
        if min_confidence is not None and self.confidence < min_confidence:
            return 0
        if self.is_blocked(extra_hard_modifiers, extra_hard_regimes):
            return 0
        if self.regime_label == MarketRegime.BREAKOUT or (
            self.regime_label == MarketRegime.UPTREND and
            self.has_modifier(MarketRegimeModifier.PULLBACK_IN_UPTREND)
        ):
            return 1 if self.can_open_long else 0
        if self.regime_label == MarketRegime.BREAKDOWN or (
            self.regime_label == MarketRegime.DOWNTREND and
            self.has_modifier(MarketRegimeModifier.PULLBACK_IN_DOWNTREND)
        ):
            return -1 if self.can_open_short else 0
        return 0
