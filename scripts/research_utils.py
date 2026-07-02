"""
Shared helpers for local strategy-research scripts.

These scripts are intentionally runnable as standalone files from the repository
root, so this module avoids package-specific imports.
"""
import re
from datetime import datetime, timezone
from typing import List

import pandas as pd


LONG_SIGNALS = {
    "strong_long_continuation",
    "short_squeeze_risk",
    "weak_breakdown_trap",
}
SHORT_SIGNALS = {
    "strong_short_continuation",
    "long_squeeze_risk",
    "weak_breakout_trap",
}
OUTCOME_RE = re.compile(r"^(long|short)_(sl[^_]+_tp[^_]+)_h(\d+)_return$")


def epoch_to_utc(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def parse_float_list(value: str) -> List[float]:
    return [float(item.strip()) for item in value.split(",") if item.strip()]


def parse_int_list(value: str) -> List[int]:
    return [int(item.strip()) for item in value.split(",") if item.strip()]


def bool_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(False, index=frame.index)
    values = frame[column]
    if values.dtype == bool:
        return values.fillna(False)
    return values.fillna(False).astype(str).str.lower().isin({"true", "1", "yes", "y"})


def normalize_timestamp_column(frame: pd.DataFrame, source: str) -> pd.DataFrame:
    frame = frame.copy()
    if "timestamp" not in frame.columns:
        raise ValueError(f"{source} must include a timestamp column")
    raw_timestamp = frame["timestamp"].astype(str).str.strip()
    numeric_timestamp = pd.to_numeric(frame["timestamp"], errors="coerce")
    if numeric_timestamp.notna().all() and raw_timestamp.str.fullmatch(r"\d+(\.\d+)?").all():
        frame["timestamp"] = numeric_timestamp.astype(float).astype(int)
        return frame

    parsed_timestamp = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    if parsed_timestamp.isna().any():
        bad_count = int(parsed_timestamp.isna().sum())
        raise ValueError(f"{source} has {bad_count} invalid timestamp values")
    epoch_start = pd.Timestamp("1970-01-01", tz="UTC")
    frame["timestamp"] = (parsed_timestamp - epoch_start).dt.total_seconds().astype(int)
    return frame


def load_csv_frame(path: str, source: str, low_memory: bool = False) -> pd.DataFrame:
    frame = normalize_timestamp_column(pd.read_csv(path, low_memory=low_memory), source)
    return frame.sort_values("timestamp").drop_duplicates(subset=["timestamp"]).reset_index(drop=True)
