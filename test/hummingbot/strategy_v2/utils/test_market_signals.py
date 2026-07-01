import unittest

from hummingbot.strategy_v2.utils.market_signals import (
    MarketSignal,
    MarketSignalConfig,
    MarketSignalDetector,
)


class MarketSignalDetectorTests(unittest.TestCase):
    def setUp(self):
        self.detector = MarketSignalDetector(MarketSignalConfig(
            funding_extreme_rate=0.001,
            min_signal_score=0.65,
        ))

    def test_detects_strong_long_continuation(self):
        report = self.detector.evaluate({
            "price_above_rolling_vwap": True,
            "price_above_session_vwap": True,
            "ema_fast_above_slow": True,
            "roc_6_positive": True,
            "cvd_proxy_rising": True,
            "taker_buy_imbalance": 0.08,
            "open_interest_rising": True,
            "volume_expansion": 1.4,
            "funding_rate_level": 0.0002,
        })

        self.assertTrue(report.has_signal(MarketSignal.STRONG_LONG_CONTINUATION))
        self.assertGreater(report.long_score, 0.9)
        self.assertEqual(0, report.short_score)

    def test_detects_strong_short_continuation(self):
        report = self.detector.evaluate({
            "price_above_rolling_vwap": False,
            "price_above_session_vwap": False,
            "ema_bias": -1,
            "roc_6": -0.02,
            "cvd_proxy_rising": False,
            "cvd_proxy_change": -1000,
            "taker_buy_imbalance": -0.08,
            "open_interest_rising": True,
            "volume_expansion": 1.4,
            "funding_rate_level": -0.0002,
        })

        self.assertTrue(report.has_signal(MarketSignal.STRONG_SHORT_CONTINUATION))
        self.assertGreater(report.short_score, 0.9)
        self.assertEqual(0, report.long_score)

    def test_missing_bearish_booleans_do_not_create_short_signal(self):
        report = self.detector.evaluate({
            "ema_bias": -1,
            "roc_6": -0.02,
            "taker_buy_imbalance": -0.08,
            "volume_expansion": 1.4,
            "funding_rate_level": -0.0002,
        })

        self.assertFalse(report.has_signal(MarketSignal.STRONG_SHORT_CONTINUATION))

    def test_detects_short_squeeze_risk(self):
        report = self.detector.evaluate({
            "funding_rate_level": -0.0015,
            "open_interest_rising": True,
            "cvd_proxy_rising": True,
            "taker_buy_imbalance": 0.05,
            "price_above_session_vwap": True,
        })

        self.assertTrue(report.has_signal(MarketSignal.SHORT_SQUEEZE_RISK))
        self.assertGreater(report.long_score, 0)

    def test_detects_long_squeeze_risk(self):
        report = self.detector.evaluate({
            "funding_rate_level": 0.0015,
            "open_interest_rising": True,
            "cvd_proxy_rising": False,
            "cvd_proxy_change": -1000,
            "taker_buy_imbalance": -0.05,
            "price_above_session_vwap": False,
        })

        self.assertTrue(report.has_signal(MarketSignal.LONG_SQUEEZE_RISK))
        self.assertGreater(report.short_score, 0)

    def test_detects_weak_breakout_trap(self):
        report = self.detector.evaluate({
            "failed_breakout_above": True,
            "volume_expansion": 0.8,
            "open_interest_rising": False,
            "cvd_proxy_rising": False,
            "cvd_proxy_change": -100,
            "funding_rate_level": 0.002,
        })

        self.assertTrue(report.has_signal(MarketSignal.WEAK_BREAKOUT_TRAP))
        self.assertGreater(report.short_score, 0)

    def test_detects_risk_off(self):
        report = self.detector.evaluate({
            "liquidity_score_feature": 0.2,
            "spread_bps": 25,
            "atr_pct_feature": 0.05,
        })

        self.assertTrue(report.risk_off)
        self.assertTrue(report.has_signal(MarketSignal.RISK_OFF))


if __name__ == "__main__":
    unittest.main()
