import unittest

import pandas as pd

from hummingbot.strategy_v2.utils.market_regime import (
    MarketContext,
    MarketRegime,
    MarketRegimeConfig,
    MarketRegimeDetector,
    MarketRegimeModifier,
)


def make_candles(closes):
    rows = []
    for i, close in enumerate(closes):
        rows.append({
            "timestamp": i,
            "open": close,
            "high": close * 1.002,
            "low": close * 0.998,
            "close": close,
            "volume": 1000,
        })
    return pd.DataFrame(rows)


class TestMarketRegimeDetector(unittest.TestCase):
    def setUp(self):
        self.config = MarketRegimeConfig(
            range_lookback=12,
            trend_lookback=6,
            atr_length=5,
            realized_vol_length=5,
            acceptance_bars=2,
            min_boundary_touches=1,
            max_chop_range_width_pct=0.12,
            min_trend_slope_pct=0.005,
            max_balanced_range_slope_pct=0.03,
            high_vol_atr_pct=0.15,
            high_vol_multiplier=10,
        )
        self.detector = MarketRegimeDetector(self.config)

    def test_no_trade_when_insufficient_data(self):
        report = self.detector.classify(make_candles([100, 101, 102]))
        self.assertEqual(MarketRegime.NO_TRADE, report.label)
        self.assertFalse(report.allow_longs)
        self.assertFalse(report.allow_shorts)

    def test_uptrend_label(self):
        closes = [100 + i for i in range(40)]
        report = self.detector.classify(make_candles(closes))
        self.assertEqual(MarketRegime.UPTREND, report.label)
        self.assertTrue(report.allow_longs)
        self.assertFalse(report.allow_shorts)

    def test_downtrend_label(self):
        closes = [140 - i for i in range(40)]
        report = self.detector.classify(make_candles(closes))
        self.assertEqual(MarketRegime.DOWNTREND, report.label)
        self.assertFalse(report.allow_longs)
        self.assertTrue(report.allow_shorts)

    def test_breakout_label(self):
        closes = [100, 101, 99, 100, 101, 99] * 5 + [104, 105]
        report = self.detector.classify(make_candles(closes))
        self.assertEqual(MarketRegime.BREAKOUT, report.label)
        self.assertTrue(report.allow_longs)
        self.assertFalse(report.allow_shorts)

    def test_breakdown_label(self):
        closes = [100, 101, 99, 100, 101, 99] * 5 + [96, 95]
        report = self.detector.classify(make_candles(closes))
        self.assertEqual(MarketRegime.BREAKDOWN, report.label)
        self.assertFalse(report.allow_longs)
        self.assertTrue(report.allow_shorts)

    def test_range_chop_label(self):
        closes = [100, 102, 98, 101, 99, 102, 98, 100] * 5
        report = self.detector.classify(make_candles(closes))
        self.assertEqual(MarketRegime.RANGE_CHOP, report.label)
        self.assertTrue(report.allow_longs)
        self.assertTrue(report.allow_shorts)

    def test_squeeze_risk_overrides_directional_regime(self):
        closes = [100 + i for i in range(40)]
        report = self.detector.classify(
            make_candles(closes),
            MarketContext(crowding_score=0.9),
        )
        self.assertEqual(MarketRegime.SQUEEZE_RISK, report.label)
        self.assertLess(report.risk_multiplier, 1)

    def test_high_volatility_danger_overrides_breakout(self):
        config = MarketRegimeConfig(
            range_lookback=12,
            trend_lookback=6,
            atr_length=5,
            realized_vol_length=5,
            acceptance_bars=2,
            high_vol_atr_pct=0.01,
        )
        detector = MarketRegimeDetector(config)
        closes = [100, 101, 99, 100, 101, 99] * 5 + [110, 112]
        report = detector.classify(make_candles(closes))
        self.assertEqual(MarketRegime.HIGH_VOLATILITY_DANGER, report.label)

    def test_pullback_in_uptrend_modifier(self):
        closes = [100 + i for i in range(30)] + [130, 132, 134, 136, 132, 131]
        report = self.detector.classify(make_candles(closes))
        self.assertEqual(MarketRegime.UPTREND, report.label)
        self.assertIn(MarketRegimeModifier.PULLBACK_IN_UPTREND, report.modifiers)

    def test_pullback_in_downtrend_modifier(self):
        closes = [150 - i for i in range(30)] + [120, 118, 116, 114, 118, 119]
        report = self.detector.classify(make_candles(closes))
        self.assertEqual(MarketRegime.DOWNTREND, report.label)
        self.assertIn(MarketRegimeModifier.PULLBACK_IN_DOWNTREND, report.modifiers)

    def test_failed_breakout_modifier_reduces_risk(self):
        closes = [100, 101, 99, 100, 101, 99] * 5 + [104, 100]
        report = self.detector.classify(make_candles(closes))
        self.assertEqual(MarketRegime.RANGE_CHOP, report.label)
        self.assertIn(MarketRegimeModifier.FAILED_BREAKOUT, report.modifiers)
        self.assertLess(report.risk_multiplier, 1)

    def test_trend_exhaustion_modifier_reduces_risk(self):
        closes = [100 + i * 5 for i in range(40)]
        report = self.detector.classify(make_candles(closes))
        self.assertEqual(MarketRegime.UPTREND, report.label)
        self.assertIn(MarketRegimeModifier.TREND_EXHAUSTION, report.modifiers)
        self.assertLess(report.risk_multiplier, 1)

    def test_funding_extreme_modifier_reduces_crowded_side(self):
        closes = [100 + i for i in range(40)]
        report = self.detector.classify(
            make_candles(closes),
            MarketContext(funding_rate=0.002),
        )
        self.assertEqual(MarketRegime.UPTREND, report.label)
        self.assertIn(MarketRegimeModifier.FUNDING_EXTREME, report.modifiers)
        self.assertLess(report.long_risk_multiplier, 1)

    def test_liquidity_thin_modifier_reduces_risk_without_disabling(self):
        closes = [100, 102, 98, 101, 99, 102, 98, 100] * 5
        report = self.detector.classify(
            make_candles(closes),
            MarketContext(liquidity_score=0.35),
        )
        self.assertEqual(MarketRegime.RANGE_CHOP, report.label)
        self.assertIn(MarketRegimeModifier.LIQUIDITY_THIN, report.modifiers)
        self.assertTrue(report.allow_longs)
        self.assertTrue(report.allow_shorts)
        self.assertLess(report.risk_multiplier, 1)

    def test_post_liquidation_flush_modifier(self):
        closes = [100 + i for i in range(40)]
        report = self.detector.classify(
            make_candles(closes),
            MarketContext(liquidation_flush_score=0.8, liquidation_flush_direction=1),
        )
        self.assertEqual(MarketRegime.UPTREND, report.label)
        self.assertIn(MarketRegimeModifier.POST_LIQUIDATION_FLUSH, report.modifiers)
        self.assertEqual(1, report.features["liquidation_flush_direction"])


if __name__ == "__main__":
    unittest.main()
