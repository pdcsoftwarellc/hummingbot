import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional


class MarketSignal(str, Enum):
    STRONG_LONG_CONTINUATION = "strong_long_continuation"
    STRONG_SHORT_CONTINUATION = "strong_short_continuation"
    SHORT_SQUEEZE_RISK = "short_squeeze_risk"
    LONG_SQUEEZE_RISK = "long_squeeze_risk"
    WEAK_BREAKOUT_TRAP = "weak_breakout_trap"
    WEAK_BREAKDOWN_TRAP = "weak_breakdown_trap"
    RISK_OFF = "risk_off"


@dataclass
class MarketSignalConfig:
    min_continuation_confirmations: int = 6
    min_squeeze_confirmations: int = 4
    min_signal_score: float = 0.65
    min_volume_expansion: float = 1.1
    min_taker_imbalance: float = 0.02
    funding_extreme_rate: float = 0.001
    min_liquidity_score: float = 0.5
    max_spread_bps: float = 10.0
    high_atr_pct: float = 0.035
    high_realized_vol: float = 0.04


@dataclass
class MarketSignalReport:
    signals: List[MarketSignal] = field(default_factory=list)
    scores: Dict[MarketSignal, float] = field(default_factory=dict)
    reasons: Dict[MarketSignal, str] = field(default_factory=dict)
    long_score: float = 0.0
    short_score: float = 0.0
    risk_off: bool = False

    def has_signal(self, signal: MarketSignal) -> bool:
        return signal in self.signals


def _as_float(row: Mapping[str, Any], key: str) -> Optional[float]:
    value = row.get(key)
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def _as_bool(row: Mapping[str, Any], key: str) -> bool:
    value = row.get(key)
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _is_false(row: Mapping[str, Any], key: str) -> bool:
    if key not in row or row.get(key) is None:
        return False
    return not _as_bool(row, key)


def _lt(row: Mapping[str, Any], key: str, threshold: float) -> bool:
    value = _as_float(row, key)
    return value is not None and value < threshold


def _gt(row: Mapping[str, Any], key: str, threshold: float) -> bool:
    value = _as_float(row, key)
    return value is not None and value > threshold


def _non_extreme_funding(value: Optional[float], cfg: MarketSignalConfig) -> bool:
    return value is not None and abs(value) < cfg.funding_extreme_rate


class MarketSignalDetector:
    def __init__(self, config: Optional[MarketSignalConfig] = None):
        self.config = config or MarketSignalConfig()

    def evaluate(self, row: Mapping[str, Any]) -> MarketSignalReport:
        report = MarketSignalReport()
        self._add_risk_off(row, report)
        self._add_continuation(row, report)
        self._add_squeeze_risk(row, report)
        self._add_traps(row, report)
        return report

    def _add_signal(
        self,
        report: MarketSignalReport,
        signal: MarketSignal,
        score: float,
        reasons: List[str],
    ):
        if signal not in report.signals:
            report.signals.append(signal)
        report.scores[signal] = score
        report.reasons[signal] = ", ".join(reasons)
        if signal in {MarketSignal.STRONG_LONG_CONTINUATION, MarketSignal.SHORT_SQUEEZE_RISK, MarketSignal.WEAK_BREAKDOWN_TRAP}:
            report.long_score = max(report.long_score, score)
        if signal in {MarketSignal.STRONG_SHORT_CONTINUATION, MarketSignal.LONG_SQUEEZE_RISK, MarketSignal.WEAK_BREAKOUT_TRAP}:
            report.short_score = max(report.short_score, score)

    def _add_risk_off(self, row: Mapping[str, Any], report: MarketSignalReport):
        reasons = []
        liquidity = _as_float(row, "liquidity_score_feature")
        spread_bps = _as_float(row, "spread_bps")
        atr_pct = _as_float(row, "atr_pct_feature")
        realized_vol = _as_float(row, "realized_vol_feature")
        if liquidity is not None and liquidity < self.config.min_liquidity_score:
            reasons.append("liquidity below threshold")
        if spread_bps is not None and spread_bps > self.config.max_spread_bps:
            reasons.append("spread above threshold")
        if atr_pct is not None and atr_pct > self.config.high_atr_pct:
            reasons.append("ATR above threshold")
        if realized_vol is not None and realized_vol > self.config.high_realized_vol:
            reasons.append("realized vol above threshold")
        if not reasons:
            return
        score = min(1.0, len(reasons) / 2)
        report.risk_off = True
        self._add_signal(report, MarketSignal.RISK_OFF, score, reasons)

    def _add_continuation(self, row: Mapping[str, Any], report: MarketSignalReport):
        funding = _as_float(row, "funding_rate_level")
        long_checks = [
            ("price above rolling VWAP", _as_bool(row, "price_above_rolling_vwap")),
            ("price above session VWAP", _as_bool(row, "price_above_session_vwap")),
            ("EMA fast above slow", _as_bool(row, "ema_fast_above_slow") or _gt(row, "ema_bias", 0)),
            ("ROC positive", _as_bool(row, "roc_6_positive") or _gt(row, "roc_6", 0)),
            ("CVD proxy rising", _as_bool(row, "cvd_proxy_rising")),
            ("taker buy pressure", _gt(row, "taker_buy_imbalance", self.config.min_taker_imbalance)),
            ("OI rising", _as_bool(row, "open_interest_rising")),
            ("volume expanding", _gt(row, "volume_expansion", self.config.min_volume_expansion)),
            ("funding positive but not extreme", funding is not None and funding > 0 and _non_extreme_funding(funding, self.config)),
        ]
        self._maybe_add_confirmation_signal(
            report=report,
            signal=MarketSignal.STRONG_LONG_CONTINUATION,
            checks=long_checks,
            min_confirmations=self.config.min_continuation_confirmations,
        )

        short_checks = [
            ("price below rolling VWAP", _is_false(row, "price_above_rolling_vwap") or _lt(row, "price_vs_rolling_vwap_pct", 0)),
            ("price below session VWAP", _is_false(row, "price_above_session_vwap") or _lt(row, "price_vs_session_vwap_pct", 0)),
            ("EMA fast below slow", _lt(row, "ema_bias", 0)),
            ("ROC negative", _lt(row, "roc_6", 0)),
            ("CVD proxy falling", _is_false(row, "cvd_proxy_rising") or _lt(row, "cvd_proxy_change", 0)),
            ("taker sell pressure", _lt(row, "taker_buy_imbalance", -self.config.min_taker_imbalance)),
            ("OI rising", _as_bool(row, "open_interest_rising")),
            ("volume expanding", _gt(row, "volume_expansion", self.config.min_volume_expansion)),
            ("funding negative but not extreme", funding is not None and funding < 0 and _non_extreme_funding(funding, self.config)),
        ]
        self._maybe_add_confirmation_signal(
            report=report,
            signal=MarketSignal.STRONG_SHORT_CONTINUATION,
            checks=short_checks,
            min_confirmations=self.config.min_continuation_confirmations,
        )

    def _add_squeeze_risk(self, row: Mapping[str, Any], report: MarketSignalReport):
        funding = _as_float(row, "funding_rate_level")
        short_squeeze_checks = [
            ("funding negative", funding is not None and funding < 0),
            ("funding negative extreme", funding is not None and funding <= -self.config.funding_extreme_rate),
            ("OI rising", _as_bool(row, "open_interest_rising")),
            ("CVD proxy rising", _as_bool(row, "cvd_proxy_rising")),
            ("taker buy pressure", _gt(row, "taker_buy_imbalance", self.config.min_taker_imbalance)),
            ("price above VWAP", _as_bool(row, "price_above_session_vwap") or _as_bool(row, "price_above_rolling_vwap")),
        ]
        self._maybe_add_confirmation_signal(
            report=report,
            signal=MarketSignal.SHORT_SQUEEZE_RISK,
            checks=short_squeeze_checks,
            min_confirmations=self.config.min_squeeze_confirmations,
        )

        long_squeeze_checks = [
            ("funding positive", funding is not None and funding > 0),
            ("funding positive extreme", funding is not None and funding >= self.config.funding_extreme_rate),
            ("OI rising", _as_bool(row, "open_interest_rising")),
            ("CVD proxy falling", _is_false(row, "cvd_proxy_rising") or _lt(row, "cvd_proxy_change", 0)),
            ("taker sell pressure", _lt(row, "taker_buy_imbalance", -self.config.min_taker_imbalance)),
            ("price below VWAP", _is_false(row, "price_above_session_vwap") or _is_false(row, "price_above_rolling_vwap")),
        ]
        self._maybe_add_confirmation_signal(
            report=report,
            signal=MarketSignal.LONG_SQUEEZE_RISK,
            checks=long_squeeze_checks,
            min_confirmations=self.config.min_squeeze_confirmations,
        )

    def _add_traps(self, row: Mapping[str, Any], report: MarketSignalReport):
        if _as_bool(row, "failed_breakout_above") or _lt(row, "trap_direction", 0):
            checks = [
                ("failed breakout above range", True),
                ("weak volume expansion", not _gt(row, "volume_expansion", self.config.min_volume_expansion)),
                ("OI not rising", _is_false(row, "open_interest_rising")),
                ("CVD not confirming", _is_false(row, "cvd_proxy_rising") or _lt(row, "cvd_proxy_change", 0)),
                ("funding hot", _gt(row, "funding_rate_level", self.config.funding_extreme_rate)),
            ]
            self._maybe_add_confirmation_signal(
                report=report,
                signal=MarketSignal.WEAK_BREAKOUT_TRAP,
                checks=checks,
                min_confirmations=2,
            )
        if _as_bool(row, "failed_breakdown_below") or _gt(row, "trap_direction", 0):
            checks = [
                ("failed breakdown below range", True),
                ("weak volume expansion", not _gt(row, "volume_expansion", self.config.min_volume_expansion)),
                ("OI not rising", _is_false(row, "open_interest_rising")),
                ("CVD not confirming sell pressure", _as_bool(row, "cvd_proxy_rising")),
                ("funding washed out", _lt(row, "funding_rate_level", -self.config.funding_extreme_rate)),
            ]
            self._maybe_add_confirmation_signal(
                report=report,
                signal=MarketSignal.WEAK_BREAKDOWN_TRAP,
                checks=checks,
                min_confirmations=2,
            )

    def _maybe_add_confirmation_signal(
        self,
        report: MarketSignalReport,
        signal: MarketSignal,
        checks: List[tuple],
        min_confirmations: int,
    ):
        matched = [reason for reason, ok in checks if ok]
        score = len(matched) / len(checks) if checks else 0.0
        if len(matched) >= min_confirmations and score >= self.config.min_signal_score:
            self._add_signal(report, signal, score, matched)
