import unittest
from types import SimpleNamespace

from hummingbot.strategy_v2.utils.market_execution_policy import MarketExecutionPolicy
from hummingbot.strategy_v2.utils.market_regime import (
    GridBias,
    MarketRegime,
    MarketRegimeModifier,
    MarketRegimeReport,
    MarketRiskState,
    RegimeAction,
)


def make_report(
    label=MarketRegime.UPTREND,
    risk_state=MarketRiskState.NORMAL,
    modifiers=None,
    blocked_by=None,
    allow_longs=True,
    allow_shorts=False,
    long_risk_multiplier=1.0,
    short_risk_multiplier=0.0,
):
    return MarketRegimeReport(
        label=label,
        action=RegimeAction.LONG_BIASED_GRID,
        grid_bias=GridBias.LONG,
        confidence=1.0,
        allow_longs=allow_longs,
        allow_shorts=allow_shorts,
        risk_multiplier=max(long_risk_multiplier, short_risk_multiplier),
        reason="test",
        modifiers=modifiers or [MarketRegimeModifier.PULLBACK_IN_UPTREND],
        long_risk_multiplier=long_risk_multiplier,
        short_risk_multiplier=short_risk_multiplier,
        price_regime=MarketRegime.UPTREND,
        risk_state=risk_state,
        blocked_by=blocked_by or [],
    )


class TestMarketExecutionPolicy(unittest.TestCase):
    def test_from_report_allows_soft_liquidity_risk(self):
        report = make_report(
            risk_state=MarketRiskState.SOFT_RISK,
            modifiers=[
                MarketRegimeModifier.PULLBACK_IN_UPTREND,
                MarketRegimeModifier.LIQUIDITY_THIN,
            ],
            long_risk_multiplier=0.5,
        )

        policy = MarketExecutionPolicy.from_report(report)

        self.assertFalse(policy.is_hard_blocked)
        self.assertTrue(policy.can_open_long)
        self.assertEqual(0.5, policy.side_risk_multiplier("long"))
        self.assertEqual(1, policy.directional_signal(min_confidence=0.55))

    def test_from_report_blocks_hard_liquidity_block(self):
        report = make_report(
            label=MarketRegime.NO_TRADE,
            risk_state=MarketRiskState.LIQUIDITY_BLOCKED,
            blocked_by=["liquidity_bad"],
            long_risk_multiplier=0.0,
        )

        policy = MarketExecutionPolicy.from_report(report)

        self.assertTrue(policy.is_hard_blocked)
        self.assertFalse(policy.can_open_long)
        self.assertEqual(0, policy.directional_signal(min_confidence=0.55))

    def test_from_mapping_matches_csv_backtest_rows(self):
        row = {
            "price_regime": "uptrend",
            "regime_label": "uptrend",
            "risk_state": "soft_risk",
            "confidence": "1.0",
            "allow_longs": "True",
            "allow_shorts": "False",
            "long_risk_multiplier": "0.5",
            "short_risk_multiplier": "0.0",
            "modifiers": "pullback_in_uptrend,liquidity_thin",
            "blocked_by": "",
        }

        policy = MarketExecutionPolicy.from_mapping(row)

        self.assertEqual(MarketRegime.UPTREND, policy.price_regime)
        self.assertEqual(MarketRiskState.SOFT_RISK, policy.risk_state)
        self.assertFalse(policy.is_hard_blocked)
        self.assertTrue(policy.can_open_long)
        self.assertEqual(1, policy.directional_signal(min_confidence=0.55))

    def test_strategy_can_add_extra_hard_modifier(self):
        report = make_report(
            modifiers=[
                MarketRegimeModifier.PULLBACK_IN_UPTREND,
                MarketRegimeModifier.TREND_EXHAUSTION,
            ],
        )

        policy = MarketExecutionPolicy.from_report(report)

        self.assertFalse(policy.is_hard_blocked)
        self.assertTrue(policy.is_blocked({MarketRegimeModifier.TREND_EXHAUSTION}))
        self.assertEqual(
            0,
            policy.directional_signal(
                min_confidence=0.55,
                extra_hard_modifiers={MarketRegimeModifier.TREND_EXHAUSTION},
            ),
        )

    def test_strategy_can_add_extra_hard_regime(self):
        report = make_report(
            label=MarketRegime.RANGE_CHOP,
            modifiers=[],
            allow_longs=True,
            allow_shorts=True,
            long_risk_multiplier=1.0,
            short_risk_multiplier=1.0,
        )

        policy = MarketExecutionPolicy.from_report(report)

        self.assertFalse(policy.is_hard_blocked)
        self.assertTrue(policy.is_blocked(extra_hard_regimes={MarketRegime.RANGE_CHOP}))

    def test_from_report_accepts_legacy_report_shape(self):
        report = SimpleNamespace(
            label=MarketRegime.UPTREND,
            confidence=0.8,
            allow_longs=True,
            allow_shorts=False,
            long_risk_multiplier=1.0,
            short_risk_multiplier=0.0,
            modifiers=[MarketRegimeModifier.PULLBACK_IN_UPTREND],
        )

        policy = MarketExecutionPolicy.from_report(report)

        self.assertEqual(MarketRegime.UPTREND, policy.price_regime)
        self.assertEqual(MarketRiskState.NORMAL, policy.risk_state)
        self.assertEqual(1, policy.directional_signal(min_confidence=0.55))


if __name__ == "__main__":
    unittest.main()
