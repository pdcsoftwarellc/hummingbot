import unittest

import pandas as pd

from hummingbot.strategy_v2.utils.market_regime import (
    MarketContext,
    MarketRegime,
    MarketRegimeConfig,
    MarketRegimeDetector,
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


if __name__ == "__main__":
    unittest.main()
