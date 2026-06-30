import os
import tempfile
import unittest

import pandas as pd

from scripts.collect_hyperliquid_context import FIELDNAMES
from scripts.merge_hyperliquid_context import merge_context_frames, read_context, write_context


class HyperliquidContextMergeTests(unittest.TestCase):
    def row(self, timestamp: int, coin: str = "SOL", funding_rate: float = 0.0):
        row = {field: pd.NA for field in FIELDNAMES}
        row.update({
            "timestamp": timestamp,
            "iso_time": pd.to_datetime(timestamp, unit="s", utc=True).isoformat(),
            "coin": coin,
            "funding_rate": funding_rate,
            "open_interest": 1000,
            "mid_price": 100,
        })
        return row

    def test_live_rows_win_exact_timestamp_duplicates(self):
        s3_frame = pd.DataFrame([
            self.row(100, funding_rate=0.001),
            self.row(200, funding_rate=0.002),
        ])
        s3_frame["_source"] = "s3"
        live_frame = pd.DataFrame([
            self.row(200, funding_rate=0.009),
            self.row(300, funding_rate=0.003),
        ])
        live_frame["_source"] = "live"

        merged, duplicate_rows = merge_context_frames(s3_frame, live_frame, prefer="live")

        self.assertEqual([100, 200, 300], merged["timestamp"].tolist())
        self.assertEqual(2, duplicate_rows)
        self.assertEqual(0.009, merged.loc[merged["timestamp"] == 200, "funding_rate"].iloc[0])

    def test_read_context_filters_coin_and_preserves_schema(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "context.csv")
            write_context(path, pd.DataFrame([
                self.row(100, coin="SOL", funding_rate=0.001),
                self.row(200, coin="BTC", funding_rate=0.002),
            ]))

            frame = read_context(path, source="s3", coin="SOL")

        self.assertEqual(["SOL"], frame["coin"].tolist())
        self.assertEqual(FIELDNAMES + ["_source"], frame.columns.tolist())


if __name__ == "__main__":
    unittest.main()
