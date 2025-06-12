"""
Utility helpers for working with IST timestamps.

* to_ist_naive(ts):
    - If `ts` is tz‑aware in **UTC**, convert to IST and strip tzinfo.
    - If `ts` is tz‑aware **already in IST**, just strip tzinfo.
    - If `ts` is naive, assume it's already IST and return as‑is.

* localize_df(df, col="date"):
    - Applies `to_ist_naive` to the specified column in‑place and returns df.
"""

from datetime import datetime, timezone, timedelta
import pandas as pd

# --------------------------------------------------------------------------- #
IST = timezone(timedelta(hours=5, minutes=30))
# --------------------------------------------------------------------------- #

def to_ist_naive(ts: datetime) -> datetime:
    """
    Return a *naive* datetime representing IST, without double‑shifting.
    """
    if ts.tzinfo is None:
        # naive => assume already IST
        return ts
    # tz‑aware → check offset
    if ts.tzinfo.utcoffset(ts) == IST.utcoffset(None):
        # already IST‑aware → strip tzinfo
        return ts.replace(tzinfo=None)
    # otherwise assume source is UTC; convert to IST then strip
    return ts.astimezone(IST).replace(tzinfo=None)


def localize_df(df: pd.DataFrame, col: str = "date") -> pd.DataFrame:
    """
    Convert column `col` to IST‑naive datetimes.
    """
    df[col] = pd.to_datetime(df[col]).apply(to_ist_naive)
    return df