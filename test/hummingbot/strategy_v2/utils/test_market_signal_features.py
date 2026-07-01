import unittest

import pandas as pd

from hummingbot.strategy_v2.utils.market_signal_features import (
    MarketSignalFeatureConfig,
    enrich_market_signal_features,
)


def sample_frame(rows=60):
    data = []
    for index in range(rows):
        close = 100 + index
        volume = 1000 + index * 10
        data.append({
            "timestamp": 1_700_000_000 + index * 3600,
            "open": close - 0.5,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": volume,
            "quote_asset_volume": volume * close,
            "taker_buy_base_volume": volume * 0.6,
            "funding_rate": 0.0001 + index * 0.000001,
            "premium": 0.0002 + index * 0.000001,
            "open_interest": 1_000_000 + index * 1000,
            "spread_pct": 0.0005,
            "depth_usd": 2_000_000,
            "bid_depth_usd": 1_200_000,
            "ask_depth_usd": 800_000,
            "liquidity_score": 0.8,
            "prior_range_high": 120,
            "prior_range_low": 90,
            "raw_accepted_above_range": index == 25,
            "rejected_above_range": index == 28,
        })
    return pd.DataFrame(data)


class MarketSignalFeaturesTests(unittest.TestCase):
    def test_enriches_trend_vwap_momentum_and_volume_features(self):
        enriched = enrich_market_signal_features(sample_frame(), MarketSignalFeatureConfig())
        last = enriched.iloc[-1]

        self.assertGreater(last["ema_fast"], last["ema_slow"])
        self.assertEqual(1, last["ema_bias"])
        self.assertTrue(bool(last["ema_fast_above_slow"]))
        self.assertGreater(last["price_vs_rolling_vwap_pct"], 0)
        self.assertGreater(last["price_vs_session_vwap_pct"], 0)
        self.assertTrue(bool(last["price_above_rolling_vwap"]))
        self.assertTrue(bool(last["price_above_session_vwap"]))
        self.assertGreater(last["roc_6"], 0)
        self.assertGreater(last["roc_12"], 0)
        self.assertTrue(bool(last["roc_6_positive"]))
        self.assertGreater(last["rsi"], 50)
        self.assertGreater(last["volume_expansion"], 1)
        self.assertTrue(bool(last["volume_expanding"]))

    def test_enriches_taker_imbalance_and_cvd_proxy(self):
        enriched = enrich_market_signal_features(sample_frame(), MarketSignalFeatureConfig())
        last = enriched.iloc[-1]

        self.assertAlmostEqual(0.2, last["taker_buy_imbalance"])
        self.assertTrue(bool(last["taker_buy_pressure"]))
        self.assertGreater(last["cvd_proxy"], 0)
        self.assertTrue(bool(last["cvd_proxy_rising"]))

    def test_enriches_derivatives_and_risk_features(self):
        enriched = enrich_market_signal_features(sample_frame(), MarketSignalFeatureConfig())
        last = enriched.iloc[-1]

        self.assertEqual(last["funding_rate"], last["funding_rate_level"])
        self.assertGreater(last["funding_rate_trend"], 0)
        self.assertTrue(bool(last["funding_positive"]))
        self.assertGreater(last["open_interest_change_pct_feature"], 0)
        self.assertTrue(bool(last["open_interest_rising"]))
        self.assertGreater(last["premium_trend"], 0)
        self.assertTrue(bool(last["premium_positive"]))
        self.assertEqual(0.0005, last["spread_pct_feature"])
        self.assertEqual(5, last["spread_bps"])
        self.assertEqual(2_000_000, last["depth_usd_feature"])
        self.assertEqual(1_200_000, last["bid_depth_usd_feature"])
        self.assertEqual(800_000, last["ask_depth_usd_feature"])
        self.assertAlmostEqual(0.2, last["depth_imbalance"])
        self.assertEqual(0.8, last["liquidity_score_feature"])
        self.assertFalse(bool(last["liquidity_thin_feature"]))
        self.assertGreater(last["atr_pct_feature"], 0)
        self.assertGreater(last["realized_vol_feature"], 0)

    def test_detects_failed_breakout_without_lookahead(self):
        frame = sample_frame()
        frame.loc[28, "close"] = 119
        enriched = enrich_market_signal_features(frame, MarketSignalFeatureConfig(trap_lookback=6))

        self.assertTrue(bool(enriched.loc[28, "failed_breakout_above"]))
        self.assertEqual(-1, enriched.loc[28, "trap_direction"])


if __name__ == "__main__":
    unittest.main()
