import unittest

import pandas as pd

from hummingbot.strategy_v2.utils.market_regime import (
    MarketRegimeConfig,
    MarketRegimeDetector,
    MarketRegimeModifier,
    MarketRiskState,
)
from hummingbot.strategy_v2.utils.market_regime_context import (
    MarketContextBuilder,
    MarketContextInput,
    SOL_1H_CONTEXT_CONFIG,
)


def make_candles(closes):
    rows = []
    for index, close in enumerate(closes):
        rows.append({
            "timestamp": index,
            "open": close,
            "high": close * 1.002,
            "low": close * 0.998,
            "close": close,
            "volume": 1000,
        })
    return pd.DataFrame(rows)


class TestMarketContextBuilder(unittest.TestCase):
    def setUp(self):
        self.builder = MarketContextBuilder.sol_1h()

    def test_build_passes_funding_rate(self):
        context = self.builder.build(MarketContextInput(funding_rate=0.0012))

        self.assertEqual(0.0012, context.funding_rate)

    def test_build_scores_liquidity_from_depth_and_spread(self):
        context = self.builder.build(MarketContextInput(
            depth_usd=SOL_1H_CONTEXT_CONFIG.full_depth_usd,
            spread_pct=SOL_1H_CONTEXT_CONFIG.healthy_spread_pct,
        ))

        self.assertEqual(1.0, context.liquidity_score)

    def test_build_uses_worst_liquidity_component(self):
        context = self.builder.build(MarketContextInput(
            depth_usd=SOL_1H_CONTEXT_CONFIG.full_depth_usd,
            spread_pct=SOL_1H_CONTEXT_CONFIG.thin_spread_pct,
        ))

        self.assertEqual(0.0, context.liquidity_score)

    def test_tight_spread_moderate_depth_is_soft_liquidity_risk(self):
        context = self.builder.build(MarketContextInput(
            depth_usd=322_000,
            spread_pct=0.000061,
        ))

        self.assertGreaterEqual(context.liquidity_score, 0.2)
        self.assertLess(context.liquidity_score, 0.5)

    def test_depth_below_hard_floor_is_bad_liquidity(self):
        context = self.builder.build(MarketContextInput(
            depth_usd=SOL_1H_CONTEXT_CONFIG.hard_min_depth_usd * 0.5,
            spread_pct=SOL_1H_CONTEXT_CONFIG.healthy_spread_pct,
        ))

        self.assertEqual(0.0, context.liquidity_score)

    def test_build_scores_crowding_from_open_interest_change(self):
        context = self.builder.build(MarketContextInput(
            open_interest_change_pct=SOL_1H_CONTEXT_CONFIG.crowded_open_interest_change_pct,
        ))

        self.assertEqual(1.0, context.crowding_score)

    def test_build_scores_crowding_from_long_short_ratio(self):
        context = self.builder.build(MarketContextInput(
            long_short_ratio=SOL_1H_CONTEXT_CONFIG.crowded_long_short_ratio,
        ))

        self.assertEqual(1.0, context.crowding_score)

    def test_build_computes_nearest_liquidation_distance(self):
        context = self.builder.build(MarketContextInput(
            close_price=100,
            nearest_liquidation_price=98,
        ))

        self.assertAlmostEqual(0.02, context.nearest_liquidation_distance_pct)

    def test_build_scores_liquidation_pressure_and_flush(self):
        context = self.builder.build(MarketContextInput(
            long_liquidation_notional_usd=12_000_000,
            short_liquidation_notional_usd=8_000_000,
        ))

        self.assertEqual(1.0, context.liquidation_pressure_score)
        self.assertEqual(1.0, context.liquidation_flush_score)
        self.assertEqual(1, context.liquidation_flush_direction)

    def test_build_from_mapping_uses_supported_aliases(self):
        context = self.builder.build_from_mapping({
            "funding": "0.0009",
            "market_depth_usd": "5000000",
            "bid_ask_spread_pct": "0.0004",
            "oi_change_pct": "0.10",
            "close": "100",
            "nearest_liq_price": "101",
            "long_liquidations_usd": "1000000",
            "short_liquidations_usd": "3000000",
        })

        self.assertEqual(0.0009, context.funding_rate)
        self.assertEqual(1.0, context.liquidity_score)
        self.assertEqual(1.0, context.crowding_score)
        self.assertAlmostEqual(0.01, context.nearest_liquidation_distance_pct)
        self.assertEqual(-1, context.liquidation_flush_direction)

    def test_built_context_drives_detector_modifiers(self):
        detector = MarketRegimeDetector(MarketRegimeConfig(
            range_lookback=12,
            trend_lookback=6,
            atr_length=5,
            realized_vol_length=5,
            acceptance_bars=2,
            high_vol_atr_pct=0.15,
            high_vol_multiplier=10,
            funding_extreme_rate=0.001,
        ))
        context = self.builder.build_from_mapping({
            "funding_rate": "0.002",
        })

        report = detector.classify(make_candles([100 + i for i in range(40)]), context)

        self.assertIn(MarketRegimeModifier.FUNDING_EXTREME, report.modifiers)
        self.assertEqual(0.002, report.features["funding_rate"])
        self.assertLess(report.long_risk_multiplier, 1)

    def test_moderate_depth_does_not_block_detector(self):
        detector = MarketRegimeDetector(MarketRegimeConfig(
            range_lookback=12,
            trend_lookback=6,
            atr_length=5,
            realized_vol_length=5,
            acceptance_bars=2,
            high_vol_atr_pct=0.15,
            high_vol_multiplier=10,
        ))
        context = self.builder.build_from_mapping({
            "depth_usd": "322000",
            "spread_pct": "0.000061",
        })

        report = detector.classify(make_candles([100 + i for i in range(40)]), context)

        self.assertEqual("uptrend", report.price_regime.value)
        self.assertEqual("uptrend", report.label.value)
        self.assertEqual(MarketRiskState.SOFT_RISK, report.risk_state)
        self.assertIn(MarketRegimeModifier.LIQUIDITY_THIN, report.modifiers)


if __name__ == "__main__":
    unittest.main()
