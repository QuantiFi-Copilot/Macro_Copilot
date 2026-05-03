"""raw_dataframe_to_artifact_series — fetch-shaped DataFrame → Series.

Per build plan v5 the canonical Q1 data path is:

    fetch_single_tenor(...)                   # rates_fetch.py
        → clean_single_series(...)             # shared.analytics.levels
        → raw_dataframe_to_artifact_series(...) # this adapter
        → Series

The adapter does NOT infer finance metadata.  The caller supplies:

  - ``series_key`` — stable identifier the operator layer uses to look
    the series up by name in a ``SeriesSet`` (e.g. ``"ust_2y"``).
  - ``units`` — a ``TimeSeriesUnits`` member.  No string fallbacks, no
    inference from column names.
  - ``source_kind`` / ``source_params`` — what produced the DataFrame,
    so the lineage chain can name the upstream call.
  - ``missingness_policy`` — typed structured policy
    (``CleanSingleSeriesV1`` / ``RawNoCleaning``).  Free-form strings
    are NOT accepted (build plan v5 / R3).

Inputs in v1:

  - The DataFrame must have a ``DatetimeIndex`` already (i.e. the
    caller has run ``clean_single_series`` or equivalent), or a
    ``trade_date`` column that the adapter sets as the index.
  - The value column defaults to ``"field_value"`` (matches the rest of
    the repo's fetch convention) but is overridable.
"""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional, Tuple

import pandas as pd

from shared.artifacts.lineage import (
    AdapterStep,
    CleanStep,
    FetchStep,
    Lineage,
    LineageStep,
)
from shared.artifacts.missingness import MissingnessPolicy
from shared.artifacts.types import Series
from shared.artifacts.units import TimeSeriesUnits


_ADAPTER_NAME = "raw_dataframe_to_artifact_series"
_ADAPTER_VERSION = "1.0.0"


SourceKind = Literal[
    "fetch_single_tenor",
    "fetch_tenor_group",
    "fetch_cross_market_pair",
]


def raw_dataframe_to_artifact_series(
    df: pd.DataFrame,
    *,
    series_key: str,
    units: TimeSeriesUnits,
    source_kind: SourceKind,
    source_params: Dict[str, Any],
    missingness_policy: MissingnessPolicy,
    value_col: str = "field_value",
    date_col: str = "trade_date",
    frequency: Optional[Literal["B", "D", "W", "M", "Q", "Y"]] = None,
    upstream_lineage: Optional[Tuple[LineageStep, ...]] = None,
) -> Series:
    """Convert a fetch-shaped DataFrame into a typed ``Series`` artifact.

    Parameters
    ----------
    df :
        Either a long-format frame with a ``date_col`` column, OR a
        frame already indexed on a ``DatetimeIndex`` (i.e. post
        ``clean_single_series``).  The adapter handles both shapes.
    series_key :
        Stable identifier — used as the key under which the resulting
        ``Series`` will live inside any downstream ``SeriesSet``.
    units :
        Closed-enum unit assignment.  See ``shared.schemas.time_series``.
    source_kind / source_params :
        Identity of the upstream producer (e.g.
        ``source_kind='fetch_single_tenor'``,
        ``source_params={'curve_family':'UST','tenor':'10Y',...}``).
        Recorded as a ``FetchStep`` at the head of the lineage when
        ``upstream_lineage`` is None.
    missingness_policy :
        Typed structured policy describing how the input was cleaned
        (``CleanSingleSeriesV1`` / ``RawNoCleaning``).  Free-form
        strings are rejected by the type itself.
    value_col :
        Column name for numeric values when ``df`` is long-format.
    date_col :
        Column name for trade dates when ``df`` is long-format.
    frequency :
        Optional explicit frequency tag ('B', 'D', 'W', ...).  When
        omitted the adapter does NOT infer — the resulting ``Series``
        will simply have ``frequency=None``.  This keeps the adapter
        finance-blind; an explicit caller can supply 'B' for business-
        day series, but inference would risk silent drift.
    upstream_lineage :
        Optional pre-built lineage steps to prepend before the
        adapter step.  Used in tests and in pipelines that want to
        record explicit ``FetchStep`` + ``CleanStep`` entries.  When
        ``None``, the adapter synthesises a single-step lineage from
        ``source_kind`` + ``source_params``.

    Returns
    -------
    Series
        With ``payload`` = sorted ``pd.Series`` indexed by date,
        ``units`` / ``missingness_policy`` / ``frequency`` from the
        caller, and ``lineage`` chain ending with this adapter step.
    """
    # ------------------------------------------------------------------
    # 1. Normalise to a sorted, dedup'd, DatetimeIndex'd pd.Series.
    # ------------------------------------------------------------------
    if isinstance(df.index, pd.DatetimeIndex):
        if value_col not in df.columns:
            raise ValueError(
                f"{_ADAPTER_NAME}: indexed input has no column "
                f"'{value_col}'; available: {list(df.columns)}."
            )
        payload = pd.Series(df[value_col].to_numpy(), index=df.index, dtype=float)
    else:
        if date_col not in df.columns or value_col not in df.columns:
            raise ValueError(
                f"{_ADAPTER_NAME}: long-format input must contain both "
                f"'{date_col}' and '{value_col}'; got {list(df.columns)}."
            )
        idx = pd.to_datetime(df[date_col])
        payload = pd.Series(
            pd.to_numeric(df[value_col], errors="coerce").to_numpy(),
            index=pd.DatetimeIndex(idx),
            dtype=float,
        )

    # Defensive: drop NaT/duplicated indices, sort, drop-NaN values.
    # ``clean_single_series`` already does this; the defence is for the
    # bypass path (``RawNoCleaning``) and for tests that pass raw frames.
    payload = payload[~payload.index.isna()]
    payload = payload[~payload.index.duplicated(keep="last")]
    payload = payload.sort_index()
    payload = payload.dropna()

    if payload.empty:
        raise ValueError(
            f"{_ADAPTER_NAME}: input produced an empty series for "
            f"series_key='{series_key}' (after coercion + dropna)."
        )

    # ------------------------------------------------------------------
    # 2. Build the lineage chain.
    # ------------------------------------------------------------------
    # Either prepend the caller-supplied steps, or synthesise a single
    # FetchStep from source_kind + source_params.
    upstream_steps: Tuple[LineageStep, ...]
    if upstream_lineage is None:
        fetch_step: LineageStep = FetchStep.build(
            name=source_kind,
            version="1.0.0",
            params=source_params,
        )
        upstream_steps = (fetch_step,)
    else:
        upstream_steps = upstream_lineage

    upstream_hashes = tuple(s.hash for s in upstream_steps)

    adapter_step = AdapterStep.build(
        name=_ADAPTER_NAME,
        version=_ADAPTER_VERSION,
        params={
            "series_key": series_key,
            "units": units.value,
            "source_kind": source_kind,
            "source_params": source_params,
            "missingness_policy": missingness_policy.model_dump(),
            "value_col": value_col,
            "date_col": date_col,
            "frequency": frequency,
        },
        input_hashes=upstream_hashes,
    )

    lineage = Lineage.from_steps(list(upstream_steps) + [adapter_step])

    return Series(
        series_key=series_key,
        payload=payload,
        units=units,
        frequency=frequency,
        missingness_policy=missingness_policy,
        lineage=lineage,
    )


__all__ = [
    "raw_dataframe_to_artifact_series",
    "SourceKind",
]
