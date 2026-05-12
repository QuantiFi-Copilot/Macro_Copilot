"""panel_assembly.py — multi-instrument Panel fetcher
=====================================================

Shared compute backend for tools that assemble a wide
multi-instrument Panel (rows = dates, columns = instrument keys)
ready to be wrapped as a closed-family ``Panel`` artifact.

Mirrors the discipline of ``shared/analytics/rates_fetch.py``:
finance-aware (knows the ``instrument_master`` / ``market_data_daily``
schema) but agnostic to instrument family — the caller passes the
``(curve_family, tenor)`` pairs and per-pair ``field_name`` choices,
and this module pivots into a wide DataFrame.

Used by:
  - ``rates_agent/sovereign_bonds/tools/sovereign_yield_panel`` (sovereign
    curves only, default field = YLD_YTM_MID)
  - ``rates_agent/ois/tools/ois_rate_panel`` (OIS curves only, default
    field = PX_LAST) — future PR; same compute backend.

Single source of truth: the per-agent tool-surface lives in the agent's
folder; the SQL + Panel-construction logic lives here so the methodology
cannot drift across the two callers.

Closed-family discipline
------------------------
This module does NOT construct a ``Panel`` artifact itself.  It returns
a raw ``pd.DataFrame`` + a per-column units dict.  The caller (the
tool's compute.py) wraps the result with the appropriate ``Lineage``
+ ``MissingnessPolicy`` to produce the typed artifact.  Reason: artifact
construction needs the lineage step's input hashes, which only the
caller's primitive-step builder has.
"""

from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine


# ============================================================================
# CORE FETCHER
# ============================================================================


def fetch_instrument_panel(
    engine: Engine,
    leg_specs: Sequence[Tuple[str, str, str]],
    start_date: date,
    end_date: Optional[date] = None,
    ffill_limit_days: int = 5,
) -> pd.DataFrame:
    """Fetch a wide multi-instrument panel keyed by ``(curve_family,
    tenor)`` pairs.

    Parameters
    ----------
    engine :
        Live SQLAlchemy engine.
    leg_specs :
        Sequence of ``(curve_family, tenor, field_name)`` triples.
        ``field_name`` is per-leg so a sovereign + OIS mixed panel can
        carry both ``YLD_YTM_MID`` and ``PX_LAST`` columns side-by-side.
        Column names in the returned DataFrame follow the canonical
        ``<CURVE_FAMILY>_<TENOR>`` convention (e.g. ``UST_2Y``,
        ``USD_TIPS_10Y``).
    start_date :
        Earliest trade_date to include.
    end_date :
        Latest trade_date.  None → include up to the latest observation
        in the DB.
    ffill_limit_days :
        Maximum holiday-gap to bridge via forward-fill per leg.  Matches
        the standard ``ffill_limit_days`` convention used by every other
        rates tool (curve_spread, cross_market_spread, etc.).  Set to 0
        to disable forward-fill entirely.

    Returns
    -------
    pd.DataFrame
        Wide DataFrame with a ``DatetimeIndex`` (one row per trading
        date in the fetched window) and one column per leg.  Column
        ordering matches the input ``leg_specs`` order so the caller's
        ``units_by_column`` dict can be built deterministically.

        Empty DataFrame if NO leg has any matching rows.
    """
    if not leg_specs:
        raise ValueError("fetch_instrument_panel: leg_specs is empty.")

    # Resolve all legs in a single SQL round-trip.  The ``v_market_data_
    # daily_enriched`` view exposes curve_family + tenor + field_name
    # columns directly (see database/schema.sql); we OR-build a filter
    # for each leg.  No string interpolation of user input — every
    # filter value is a bound parameter.
    where_clauses: List[str] = []
    params: Dict[str, object] = {"start_date": start_date.isoformat()}
    if end_date is not None:
        params["end_date"] = end_date.isoformat()

    for i, (curve_family, tenor, field_name) in enumerate(leg_specs):
        params[f"cf_{i}"] = curve_family
        params[f"tn_{i}"] = tenor
        params[f"fn_{i}"] = field_name
        where_clauses.append(
            f"(curve_family = :cf_{i} "
            f"AND tenor = :tn_{i} "
            f"AND field_name = :fn_{i})"
        )

    end_date_filter = " AND trade_date <= :end_date" if end_date is not None else ""

    sql = text(
        f"""
        SELECT
            trade_date,
            curve_family,
            tenor,
            field_value
        FROM macro_data.v_market_data_daily_enriched
        WHERE trade_date >= :start_date{end_date_filter}
          AND ({' OR '.join(where_clauses)})
        ORDER BY trade_date
        """
    )

    with engine.connect() as conn:
        result = conn.execute(sql, params)
        rows = result.fetchall()
        columns = list(result.keys())

    raw_df = pd.DataFrame(rows, columns=columns)
    if raw_df.empty:
        return pd.DataFrame()

    # Pivot into wide format keyed by ``<curve_family>_<tenor>``.
    # Each leg's column carries its own field_value series.
    raw_df["leg_key"] = raw_df["curve_family"] + "_" + raw_df["tenor"]
    wide = (
        raw_df.pivot_table(
            index="trade_date",
            columns="leg_key",
            values="field_value",
            aggfunc="first",  # one obs per (date, leg) by construction
        )
        .sort_index()
    )
    wide.index = pd.DatetimeIndex(pd.to_datetime(wide.index))

    # Preserve caller's leg_specs ordering.  Missing legs (no data
    # at all) get a fully-NaN column so downstream code can detect
    # the gap explicitly instead of silently shrinking the panel.
    desired_columns = [f"{cf}_{tn}" for cf, tn, _fn in leg_specs]
    for col in desired_columns:
        if col not in wide.columns:
            wide[col] = float("nan")
    wide = wide[desired_columns]

    # Forward-fill across holiday gaps per leg.  Bounded by
    # ``ffill_limit_days`` so a long stretch of missing data isn't
    # silently extrapolated.  Same convention as the curve_spread /
    # cross_market_spread pivot_and_align_tenors helper.
    if ffill_limit_days and ffill_limit_days > 0:
        wide = wide.ffill(limit=ffill_limit_days)

    return wide


# ============================================================================
# UNIT INFERENCE
# ============================================================================


def infer_units_for_field(field_name: str) -> str:
    """Map a Bloomberg field name to the canonical unit family used
    in ``TimeSeriesUnits``.  Caller wraps the returned string with
    ``TimeSeriesUnits(value)`` to validate against the closed enum.

    The mapping mirrors what existing single-instrument tools do
    implicitly — sovereign yields and OIS rates are both stored as
    percent in the source DB (e.g. 4.25 = 4.25%), so both default
    to ``PERCENT``.

    Raises
    ------
    ValueError if the field_name is not recognised.  This is a fail-
    fast guard so the caller cannot silently produce a Panel with the
    wrong units tag.
    """
    # NB: values use the LOWERCASE enum-VALUE form (matches
    # ``TimeSeriesUnits.PERCENT.value == "percent"``).  Callers
    # wrap with ``TimeSeriesUnits(value)`` — Pydantic's Enum
    # construction is value-based, not name-based.
    _FIELD_UNIT_MAP: Dict[str, str] = {
        "YLD_YTM_MID": "percent",
        "YLD_YTM_BID": "percent",
        "YLD_YTM_ASK": "percent",
        "PX_LAST": "percent",  # OIS rates quoted as percent
        "PX_MID": "percent",
        "PX_BID": "percent",
        "PX_ASK": "percent",
    }
    if field_name not in _FIELD_UNIT_MAP:
        raise ValueError(
            f"Unknown field_name {field_name!r}; cannot infer units. "
            f"Known fields: {sorted(_FIELD_UNIT_MAP)}.  Extend "
            "``infer_units_for_field`` if adding a new field family."
        )
    return _FIELD_UNIT_MAP[field_name]


# ============================================================================
# MISSING-DATA POLICY ENFORCEMENT
# ============================================================================


def apply_missing_data_policy(
    panel_df: pd.DataFrame,
    policy: str,
) -> pd.DataFrame:
    """Apply the caller's missing-data policy to a fetched wide panel.

    Policies (closed enum — caller validates against the YAML's
    ``valid_values`` before passing here):

      - ``raise`` — any NaN cell after ffill raises ValueError with
        the (date, column) coordinates of the first miss.  Safest for
        backtests where a missing observation produces wrong P&L.
      - ``forward_fill_only`` — return the panel as-is (already ffilled
        by ``fetch_instrument_panel``).  Holes outside the ffill_limit
        remain as NaN; downstream code handles them.
      - ``drop_rows_any_missing`` — drop any row that has at least one
        NaN.  Aggressive: shrinks the panel to fully-observed dates
        only.  Useful for correlation / regression panels.

    Returns the (possibly-modified) DataFrame.
    """
    if policy == "raise":
        if panel_df.isna().any().any():
            first_nan = (
                panel_df.isna().stack().pipe(lambda s: s[s]).index[0]
            )
            raise ValueError(
                f"missing_data_policy='raise' but the assembled panel "
                f"has NaN at {first_nan!r}.  Either widen the start "
                "date, ingest the missing data, or relax the policy."
            )
        return panel_df
    if policy == "forward_fill_only":
        return panel_df
    if policy == "drop_rows_any_missing":
        return panel_df.dropna(how="any")
    raise ValueError(
        f"Unknown missing_data_policy {policy!r}.  Expected one of "
        "['raise', 'forward_fill_only', 'drop_rows_any_missing']."
    )


__all__ = [
    "fetch_instrument_panel",
    "infer_units_for_field",
    "apply_missing_data_policy",
]
