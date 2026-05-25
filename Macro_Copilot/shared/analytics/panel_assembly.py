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

Helper paths (currently shipped)
--------------------------------
- ``fetch_instrument_panel`` — pivots into wide DataFrame keyed by
  ``<curve_family>_<tenor>``.  Used by
  ``rates_agent/sovereign_bonds/tools/sovereign_yield_panel`` (sovereign
  curves only, default field = YLD_YTM_MID).  Does NOT constrain on
  ``pricing_type``; the caller (sovereign) is responsible for any
  pricing-type filtering it needs.

- ``fetch_inflation_swap_panel_by_vendor_ticker`` — ZCIS-specific
  panel pivot keyed by ``vendor_ticker`` (e.g. ``'USSWIT1 Curncy'``,
  ``'EUSWI10 Curncy'``, ``'BPSWIT10 Curncy'``).  Filters on
  ``instrument_type='inflation_swap'`` AND
  ``pricing_type='zero_coupon_breakeven'`` (the inflation-swap
  no-proxy guard, mirroring ``inflation_swap_rate_level``'s
  structural invariant) AND ``curve_family IN (...)`` AND
  (optionally) ``tenor IN (...)``.  Used by
  ``rates_agent/inflation_swaps/tools/build_zcis_panel``.

- ``fetch_linker_panel_by_vendor_ticker`` — inflation-linker-specific
  panel pivot keyed by ``vendor_ticker`` (e.g. ``'GTII10 Govt'``,
  ``'GTGBPII10Y Govt'``, ``'GTFRFII10Y Govt'``,
  ``'GTCADII10Y Govt'``).  Filters on
  ``instrument_type='inflation_linker'`` AND ``curve_family IN
  (...)``.  Column-order sort is ``(curve_family, maturity_date,
  vendor_ticker)`` — linkers are specific-maturity bonds rather
  than tenor-pillar swaps, so the maturity_date is the desk-
  honest secondary sort.  Used by
  ``rates_agent/inflation_indexed_bonds/tools/build_linker_panel``.
  No ``pricing_type`` no-proxy guard is needed: every linker row
  carries ``pricing_type='real_yield'`` on
  ``instrument_master.attributes`` and there is no
  proxy-pricing variant analogous to the ZCIS year-on-year /
  zero-coupon split that requires a structural filter at the
  fetcher layer; ``instrument_type='inflation_linker'`` alone is
  sufficient to scope the universe.

- ``fetch_linker_universe`` — universe-membership helper for the
  inflation-linker universe.  Returns one row per
  ``(curve_family, vendor_ticker)`` with the load-bearing
  reference columns the methodology card needs
  (``maturity_date``, ``country``, ``tenor``,
  ``underlying_index``, and the JSONB-resident
  ``inflation_index_family`` / ``pricing_type`` /
  ``security_name``).  Filters identically to
  ``fetch_linker_panel_by_vendor_ticker`` (``instrument_type=
  'inflation_linker'``).  Used by ``build_linker_panel`` to
  surface the per-column reference metadata on the methodology
  card.

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

    # Postgres NUMERIC fields land as ``decimal.Decimal`` instances.
    # Cast to float64 at the fetcher boundary so all downstream
    # arithmetic (forward-fill, comparisons, pivot operations) works
    # uniformly.  Same defensive cast curve_spread / cross_market_spread
    # apply at their pivot layer.
    raw_df["field_value"] = raw_df["field_value"].astype(float)

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


# ============================================================================
# INFLATION-SWAP PANEL FETCHER — pivots on vendor_ticker
# ============================================================================
#
# ZCIS instruments are uniquely identified by ``vendor_ticker`` (the
# canonical Bloomberg identifier, e.g. ``'USSWIT1 Curncy'``,
# ``'EUSWI10 Curncy'``, ``'BPSWIT10 Curncy'``).  Per the
# ``build_zcis_panel`` catalog entry the panel's column key SHOULD
# be ``security_name``, but the ZCIS universe's
# ``macro_data.instrument_metadata_history.security_name`` is
# universally NULL on the live SCD2 rows (the
# ``inflation_swaps.yml`` playbook maps ``SECURITY_DES`` to
# ``security_name`` but the field is not populated).  Surfacing
# NULL would be a dead column key; relabelling ``vendor_ticker``
# under the ``security_name`` label would violate the no-proxy
# rule.  Same no-proxy treatment ``scan_inflation_swaps_extremes``
# applies (which surfaces ``vendor_ticker`` under its own name).
#
# The ``zero_coupon_breakeven`` ``pricing_type`` filter is the
# structural no-proxy guard mirroring ``inflation_swap_rate_level``:
# without it a future ingest of a different ZCIS pricing variant
# (e.g. year-on-year inflation swaps) sharing a ``curve_family``
# label would silently flow through.  ``instrument_type`` and
# ``pricing_type`` are code-owned invariants (NOT YAML-tunable);
# DESIGN_PRINCIPLES.md §5 — structural identity stays in code.


def fetch_inflation_swap_panel_by_vendor_ticker(
    engine: Engine,
    *,
    curve_families: Sequence[str],
    tenors: Optional[Sequence[str]],
    field_name: str,
    start_date: date,
    end_date: Optional[date] = None,
    ffill_limit_days: int = 5,
) -> pd.DataFrame:
    """Fetch a wide ZCIS panel keyed by ``vendor_ticker``.

    Parameters
    ----------
    engine :
        Live SQLAlchemy engine.
    curve_families :
        Sequence of ZCIS curve families to include (e.g.
        ``['USD_ZCIS', 'EUR_ZCIS', 'GBP_ZCIS']``).  Must be
        non-empty; the caller's input-validation layer rejects
        any non-ZCIS curve_family.
    tenors :
        Optional sequence of tenor identifiers to include
        (e.g. ``['1Y', '5Y', '10Y']``).  ``None`` means
        "all tenors present in the DB for the requested
        curve_families".  Empty sequence behaves identically
        to ``None``.
    field_name :
        Bloomberg field name for the ZCIS rate (e.g.
        ``'PX_MID'``).  Caller resolves the per-query sentinel
        against the YAML's ``default_zcis_rate_field``
        convention before passing.
    start_date :
        Earliest ``trade_date`` to include (inclusive).
    end_date :
        Latest ``trade_date`` (inclusive).  ``None`` → include
        every observation up to the latest in the DB.
    ffill_limit_days :
        Maximum holiday-gap to bridge via forward-fill per
        column.  Matches the standard ``ffill_limit_days``
        convention used by every other rates tool.  Set to 0
        to disable forward-fill entirely.

    Returns
    -------
    pd.DataFrame
        Wide DataFrame with a ``DatetimeIndex`` (one row per
        trading date) and one column per ZCIS instrument keyed
        by its ``vendor_ticker``.  Column order is sorted by
        ``(curve_family, tenor_year_fraction)`` so callers get
        a deterministic, desk-readable layout.  Empty
        DataFrame if no ZCIS row matches the filters.
    """
    if not curve_families:
        raise ValueError(
            "fetch_inflation_swap_panel_by_vendor_ticker: "
            "curve_families is empty."
        )

    # No string interpolation of user input — every filter value
    # is a bound parameter.  ``= ANY(:list)`` is the parameterised
    # equivalent of ``IN (...)``.
    sql_params: Dict[str, object] = {
        "instrument_type": "inflation_swap",
        "pricing_type": "zero_coupon_breakeven",
        "field_name": field_name,
        "curve_families": list(curve_families),
        "start_date": start_date.isoformat(),
    }

    tenor_filter = ""
    if tenors:
        sql_params["tenors"] = list(tenors)
        tenor_filter = " AND v.tenor = ANY(:tenors)"

    end_filter = ""
    if end_date is not None:
        sql_params["end_date"] = end_date.isoformat()
        end_filter = " AND v.trade_date <= :end_date"

    sql = text(
        f"""
        SELECT
            v.trade_date,
            v.curve_family,
            v.tenor,
            v.vendor_ticker,
            v.field_value::double precision AS field_value
        FROM macro_data.v_market_data_daily_enriched AS v
        JOIN macro_data.instrument_master AS i
          ON v.instrument_id = i.instrument_id
        WHERE v.instrument_type = :instrument_type
          AND (i.attributes ->> 'pricing_type') = :pricing_type
          AND v.field_name      = :field_name
          AND v.curve_family    = ANY(:curve_families)
          AND v.tenor          IS NOT NULL
          AND v.field_value    IS NOT NULL
          AND v.vendor_ticker  IS NOT NULL
          AND v.trade_date     >= :start_date{end_filter}{tenor_filter}
        ORDER BY v.trade_date
        """
    )

    with engine.connect() as conn:
        result = conn.execute(sql, sql_params)
        rows = result.fetchall()
        columns = list(result.keys())

    raw_df = pd.DataFrame(rows, columns=columns)
    if raw_df.empty:
        return pd.DataFrame()

    # Build the column-order map: (curve_family, tenor_year_fraction)
    # then vendor_ticker.  Reading off the actual rows means the
    # caller does not need to hard-code the universe; new tenors /
    # tickers added to ``instrument_master`` flow through.
    instrument_meta = (
        raw_df[["vendor_ticker", "curve_family", "tenor"]]
        .drop_duplicates()
        .reset_index(drop=True)
    )

    # Convert tenor (e.g. '1Y', '10Y', '30Y') to integer years for
    # sort stability.  Anything not in the ``<int>Y`` shape sorts
    # to the end alphabetically as a safe fallback (no inflation
    # swap tenor in the V1 universe deviates from this shape).
    def _tenor_sort_key(tnr: str) -> Tuple[int, str]:
        if isinstance(tnr, str) and tnr.endswith("Y") and tnr[:-1].isdigit():
            return (int(tnr[:-1]), tnr)
        return (10**9, str(tnr))

    instrument_meta["_tenor_key"] = instrument_meta["tenor"].map(_tenor_sort_key)
    instrument_meta = instrument_meta.sort_values(
        by=["curve_family", "_tenor_key", "vendor_ticker"],
        kind="mergesort",
    )
    ordered_tickers: List[str] = instrument_meta["vendor_ticker"].tolist()

    # Pivot — one column per vendor_ticker.  ``aggfunc='first'``
    # because the DB has exactly one row per
    # (vendor_ticker, trade_date) for the same field_name (the
    # enriched view dedups on the underlying ingest key).
    wide = (
        raw_df.pivot_table(
            index="trade_date",
            columns="vendor_ticker",
            values="field_value",
            aggfunc="first",
        )
        .sort_index()
    )
    wide.index = pd.DatetimeIndex(pd.to_datetime(wide.index))

    # Reorder columns by the (curve_family, tenor_year) sort.  Any
    # column missing from ``wide`` (no observations at all on the
    # ticker) sticks as a fully-NaN column so downstream code can
    # detect the gap explicitly rather than silently shrinking the
    # panel.
    for ticker in ordered_tickers:
        if ticker not in wide.columns:
            wide[ticker] = float("nan")
    wide = wide[ordered_tickers]

    if ffill_limit_days and ffill_limit_days > 0:
        wide = wide.ffill(limit=ffill_limit_days)

    return wide


def fetch_inflation_swap_universe(
    engine: Engine,
    *,
    curve_families: Sequence[str],
) -> pd.DataFrame:
    """Resolve the live ZCIS universe (instrument-level reference rows)
    for the requested curve families.

    Returns one row per (curve_family, tenor, vendor_ticker) with
    the load-bearing reference columns the methodology card needs
    (``underlying_index``, ``maturity_date``, and the JSONB-resident
    ``inflation_index_family`` / ``index_lag`` / ``interpolation`` /
    ``pricing_type``).  Filters identically to
    ``fetch_inflation_swap_panel_by_vendor_ticker``'s no-proxy
    guard (``instrument_type='inflation_swap'`` AND
    ``pricing_type='zero_coupon_breakeven'``).

    Used by ``build_zcis_panel`` to surface the per-column
    reference metadata on the methodology card.  Caller filters
    on ``is_active=TRUE`` so de-listed tickers (none today, but
    the safety net is cheap) don't leak in.
    """
    if not curve_families:
        raise ValueError(
            "fetch_inflation_swap_universe: curve_families is empty."
        )

    sql = text(
        """
        SELECT
            i.curve_family,
            i.tenor,
            i.vendor_ticker,
            i.underlying_index,
            i.maturity_date,
            i.attributes ->> 'pricing_type'           AS pricing_type,
            i.attributes ->> 'inflation_index_family' AS inflation_index_family,
            i.attributes ->> 'index_lag'              AS index_lag,
            i.attributes ->> 'interpolation'          AS interpolation
        FROM macro_data.instrument_master AS i
        WHERE i.instrument_type = :instrument_type
          AND (i.attributes ->> 'pricing_type') = :pricing_type
          AND i.curve_family = ANY(:curve_families)
          AND i.is_active = TRUE
          AND i.tenor IS NOT NULL
          AND i.vendor_ticker IS NOT NULL
        ORDER BY i.curve_family, i.tenor, i.vendor_ticker
        """
    )

    with engine.connect() as conn:
        rows = conn.execute(
            sql,
            {
                "instrument_type": "inflation_swap",
                "pricing_type": "zero_coupon_breakeven",
                "curve_families": list(curve_families),
            },
        ).mappings().all()

    return pd.DataFrame([dict(r) for r in rows])


# ============================================================================
# INFLATION-LINKER PANEL FETCHER — pivots on vendor_ticker
# ============================================================================
#
# Inflation-linker bonds (USD TIPS, UK Gilt linkers, French OATei,
# Canadian RRBs) are uniquely identified by ``vendor_ticker`` (the
# canonical Bloomberg identifier, e.g. ``'GTII10 Govt'``,
# ``'GTGBPII10Y Govt'``, ``'GTFRFII10Y Govt'``,
# ``'GTCADII10Y Govt'``).  Per the ``build_linker_panel`` catalog
# entry the panel's column key SHOULD be ``security_name``, but the
# inflation-linker universe's
# ``macro_data.instrument_metadata_history.security_name`` is
# universally NULL on the live SCD2 rows (the orchestrator's
# pre-flight verified 0 non-NULL security_name rows across all 24
# inflation_linker instruments — USD_TIPS=4 / GBP_LINKER=9 /
# EUR_FR_LINKER=5 / CAD_RRB=6).  Surfacing NULL would be a dead
# column key; relabelling ``vendor_ticker`` under the
# ``security_name`` label would violate the no-proxy rule.  Same
# no-proxy treatment ``build_zcis_panel`` (commit 32c386f) and
# ``scan_inflation_linkers_extremes`` (commit 91a5714) apply.
#
# Linkers do NOT need the ``pricing_type`` no-proxy guard the ZCIS
# helpers carry: every linker row has
# ``attributes ->> 'pricing_type' = 'real_yield'`` (verified across
# all 24 instruments) and there is no proxy-pricing variant
# analogous to the ZCIS year-on-year / zero-coupon split that would
# require a structural filter at the fetcher layer.  The
# ``instrument_type='inflation_linker'`` filter alone is sufficient
# to scope the universe; this is the same shape
# ``real_yield_level`` and ``scan_inflation_linkers_extremes`` use
# at their respective fetcher seams.


def fetch_linker_panel_by_vendor_ticker(
    engine: Engine,
    *,
    curve_families: Sequence[str],
    field_name: str,
    start_date: date,
    end_date: Optional[date] = None,
    ffill_limit_days: int = 5,
) -> pd.DataFrame:
    """Fetch a wide inflation-linker panel keyed by ``vendor_ticker``.

    Parameters
    ----------
    engine :
        Live SQLAlchemy engine.
    curve_families :
        Sequence of inflation-linker curve families to include
        (e.g. ``['USD_TIPS', 'GBP_LINKER', 'EUR_FR_LINKER',
        'CAD_RRB']``).  Must be non-empty; the caller's
        input-validation layer rejects any non-linker
        curve_family.
    field_name :
        Bloomberg field name for the linker real yield (e.g.
        ``'YLD_YTM_MID'``).  Caller resolves the per-query
        sentinel against the YAML's ``default_field_name``
        convention before passing.
    start_date :
        Earliest ``trade_date`` to include (inclusive).
    end_date :
        Latest ``trade_date`` (inclusive).  ``None`` → include
        every observation up to the latest in the DB.
    ffill_limit_days :
        Maximum holiday-gap to bridge via forward-fill per
        column.  Matches the standard ``ffill_limit_days``
        convention used by every other rates tool.  Set to 0
        to disable forward-fill entirely.

    Returns
    -------
    pd.DataFrame
        Wide DataFrame with a ``DatetimeIndex`` (one row per
        trading date) and one column per inflation-linker bond
        keyed by its ``vendor_ticker``.  Column order is sorted
        by ``(curve_family, maturity_date, vendor_ticker)`` so
        callers get a deterministic, desk-readable layout that
        reflects each linker's specific-maturity nature (linkers
        are per-bond instruments, NOT tenor-pillar swaps).
        Empty DataFrame if no inflation-linker row matches the
        filters.
    """
    if not curve_families:
        raise ValueError(
            "fetch_linker_panel_by_vendor_ticker: "
            "curve_families is empty."
        )

    # No string interpolation of user input — every filter value
    # is a bound parameter.  ``= ANY(:list)`` is the parameterised
    # equivalent of ``IN (...)``.
    sql_params: Dict[str, object] = {
        "instrument_type": "inflation_linker",
        "field_name": field_name,
        "curve_families": list(curve_families),
        "start_date": start_date.isoformat(),
    }

    end_filter = ""
    if end_date is not None:
        sql_params["end_date"] = end_date.isoformat()
        end_filter = " AND v.trade_date <= :end_date"

    sql = text(
        f"""
        SELECT
            v.trade_date,
            v.curve_family,
            v.vendor_ticker,
            i.maturity_date,
            v.field_value::double precision AS field_value
        FROM macro_data.v_market_data_daily_enriched AS v
        JOIN macro_data.instrument_master AS i
          ON v.instrument_id = i.instrument_id
        WHERE v.instrument_type = :instrument_type
          AND v.field_name      = :field_name
          AND v.curve_family    = ANY(:curve_families)
          AND v.field_value    IS NOT NULL
          AND v.vendor_ticker  IS NOT NULL
          AND v.trade_date     >= :start_date{end_filter}
        ORDER BY v.trade_date
        """
    )

    with engine.connect() as conn:
        result = conn.execute(sql, sql_params)
        rows = result.fetchall()
        columns = list(result.keys())

    raw_df = pd.DataFrame(rows, columns=columns)
    if raw_df.empty:
        return pd.DataFrame()

    # Build the column-order map: (curve_family, maturity_date,
    # vendor_ticker).  Reading off the actual rows means the caller
    # does not need to hard-code the universe; new linker bonds
    # added to ``instrument_master`` flow through.  Maturity_date
    # is the desk-honest secondary sort because linkers are per-
    # bond instruments — each ticker is a specific maturity, not
    # a tenor pillar (the ``tenor`` column on instrument_master is
    # a labelling convenience for sibling per-pillar tools).
    instrument_meta = (
        raw_df[["vendor_ticker", "curve_family", "maturity_date"]]
        .drop_duplicates()
        .reset_index(drop=True)
    )

    def _maturity_sort_key(md: Any) -> Tuple[int, str]:
        """Sort by maturity_date asc; NULL maturity_date sorts last."""
        if md is None or (isinstance(md, float) and pd.isna(md)):
            return (10**9, "")
        if hasattr(md, "toordinal"):
            return (int(md.toordinal()), str(md))
        return (10**9, str(md))

    instrument_meta["_mat_key"] = instrument_meta["maturity_date"].map(
        _maturity_sort_key,
    )
    instrument_meta = instrument_meta.sort_values(
        by=["curve_family", "_mat_key", "vendor_ticker"],
        kind="mergesort",
    )
    ordered_tickers: List[str] = instrument_meta["vendor_ticker"].tolist()

    # Pivot — one column per vendor_ticker.  ``aggfunc='first'``
    # because the DB has exactly one row per
    # (vendor_ticker, trade_date) for the same field_name (the
    # enriched view dedups on the underlying ingest key).
    wide = (
        raw_df.pivot_table(
            index="trade_date",
            columns="vendor_ticker",
            values="field_value",
            aggfunc="first",
        )
        .sort_index()
    )
    wide.index = pd.DatetimeIndex(pd.to_datetime(wide.index))

    # Reorder columns by the (curve_family, maturity_date) sort.
    # Any column missing from ``wide`` (no observations at all on
    # the ticker) sticks as a fully-NaN column so downstream code
    # can detect the gap explicitly rather than silently shrinking
    # the panel.
    for ticker in ordered_tickers:
        if ticker not in wide.columns:
            wide[ticker] = float("nan")
    wide = wide[ordered_tickers]

    if ffill_limit_days and ffill_limit_days > 0:
        wide = wide.ffill(limit=ffill_limit_days)

    return wide


def fetch_linker_universe(
    engine: Engine,
    *,
    curve_families: Sequence[str],
) -> pd.DataFrame:
    """Resolve the live inflation-linker universe (instrument-level
    reference rows) for the requested curve families.

    Returns one row per (curve_family, vendor_ticker) with the
    load-bearing reference columns the methodology card needs
    (``maturity_date``, ``country``, ``tenor``,
    ``underlying_index``, and the JSONB-resident
    ``inflation_index_family`` / ``pricing_type`` /
    ``security_name``).  Filters identically to
    ``fetch_linker_panel_by_vendor_ticker``'s
    ``instrument_type='inflation_linker'`` guard.

    Used by ``build_linker_panel`` to surface the per-curve-family
    reference metadata on the methodology card (per-country
    inflation_index_family caveats are load-bearing for
    cross-country operators consuming the panel).  Caller filters
    on ``is_active=TRUE`` so de-listed tickers (none today, but
    the safety net is cheap) don't leak in.
    """
    if not curve_families:
        raise ValueError(
            "fetch_linker_universe: curve_families is empty."
        )

    sql = text(
        """
        SELECT
            i.curve_family,
            i.vendor_ticker,
            i.tenor,
            i.country,
            i.maturity_date,
            i.underlying_index,
            i.attributes ->> 'pricing_type'           AS pricing_type,
            i.attributes ->> 'inflation_index_family' AS inflation_index_family,
            i.attributes ->> 'security_name'          AS security_name_attr
        FROM macro_data.instrument_master AS i
        WHERE i.instrument_type = :instrument_type
          AND i.curve_family = ANY(:curve_families)
          AND i.is_active = TRUE
          AND i.vendor_ticker IS NOT NULL
        ORDER BY i.curve_family, i.maturity_date, i.vendor_ticker
        """
    )

    with engine.connect() as conn:
        rows = conn.execute(
            sql,
            {
                "instrument_type": "inflation_linker",
                "curve_families": list(curve_families),
            },
        ).mappings().all()

    return pd.DataFrame([dict(r) for r in rows])


# ============================================================================
# POLICY-FUTURES STRIP PANEL FETCHER — pivots on (curve_family,
# strip_position) encoded as a flat string column key.
# ============================================================================
#
# Policy-futures strip instruments are uniquely identified by
# ``(curve_family, strip_position)`` — the V1 playbook universe
# (rates_agent/playbooks/policy_futures.yml) ships three families
# (SOFR_FUT / SONIA_FUT / EUR_SHORT_RATE_FUT) × eight strip positions
# (1..8) = 24 rolling-generic stems.  The desk-recognised reading is
# the whole 3-curve-family × 8-strip-position matrix, side-by-side
# per date (Plan §5 Group 3 #21).
#
# Column-key encoding (load-bearing)
# ----------------------------------
# The Panel typed artifact's ``units_by_column`` is declared
# ``Dict[str, TimeSeriesUnits]``: Pydantic v2 strictly enforces str
# keys and ``Panel`` validates that ``set(units_by_column.keys()) ==
# set(payload.columns)``.  A literal ``pd.MultiIndex`` over
# ``(curve_family, strip_position)`` tuples therefore cannot live on
# the artifact without breaking that contract.  We encode the
# 2-level key into a flat string ``"<CURVE_FAMILY>|<STRIP_POSITION>"``
# (e.g. ``"SOFR_FUT|1"``, ``"EUR_SHORT_RATE_FUT|8"``) so the Panel
# artifact's column axis is honest str-keyed AND every column carries
# BOTH pieces of information visibly inline.  The ``|`` separator does
# not appear in any V1 ``curve_family`` literal or in any strip-
# position integer.  Callers that need the decomposition split the
# key on ``|`` and cast position to ``int``; the build_policy_futures_
# strip_panel primitive's methodology card surfaces the
# ``column_axis_encoding`` rule explicitly + lists the per-column
# ``{column_key, curve_family, strip_position, vendor_ticker}``
# decomposition under ``curve_family_reference``.
#
# Structural identity guard
# -------------------------
# The fetcher constrains on
# ``instrument_type='policy_future'`` (the structural identity
# guard).  Without it a future ingest of a different
# instrument_type sharing a ``curve_family`` label would silently
# flow through.  Same discipline ``fetch_inflation_swap_panel_by_
# vendor_ticker`` / ``fetch_linker_panel_by_vendor_ticker`` apply.
# Inverse-pricing handling (``implied_rate_pct = 100 - raw_price``
# for inverse-priced strips) is owned by the per-tool ``compute.py``
# layer — the fetcher returns raw last_price observations so the
# fetcher stays mechanism-agnostic and the inverse-pricing flag is
# resolved off ``instrument_master.attributes`` (P5 / P6 — metadata-
# driven, no hidden methodology in code).


def _strip_panel_column_key(curve_family: str, strip_position: int) -> str:
    """Encode ``(curve_family, strip_position)`` as the canonical flat
    column key ``"<CURVE_FAMILY>|<STRIP_POSITION>"``.

    The ``|`` separator does not appear in any V1 ``curve_family``
    literal (SOFR_FUT / SONIA_FUT / EUR_SHORT_RATE_FUT) or in any
    integer strip-position render.  Used by the build_policy_futures_
    strip_panel primitive AND by the SQL-validation runner so the
    two cannot drift.  Public so external callers (validator, future
    operators that consume the panel by column key) can build the
    same key without re-implementing the encoding.
    """
    return f"{curve_family}|{int(strip_position)}"


def fetch_policy_futures_strip_panel(
    engine: Engine,
    *,
    curve_families: Sequence[str],
    strip_positions: Sequence[int],
    field_name: str,
    start_date: date,
    end_date: Optional[date] = None,
    ffill_limit_days: int = 5,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Fetch a wide policy-futures strip panel keyed by the
    ``"<CURVE_FAMILY>|<STRIP_POSITION>"`` flat string encoding +
    the per-(curve_family, strip_position) universe metadata.

    Parameters
    ----------
    engine :
        Live SQLAlchemy engine.
    curve_families :
        Sequence of policy-futures curve families to include
        (e.g. ``['SOFR_FUT', 'SONIA_FUT', 'EUR_SHORT_RATE_FUT']``).
        Must be non-empty; the caller's input-validation layer
        rejects any non-policy-futures curve_family.
    strip_positions :
        Sequence of integer strip positions to include
        (e.g. ``[1, 2, 3, 4, 5, 6, 7, 8]`` for the full strip).
        Must be non-empty; the caller's input-validation layer
        rejects out-of-range positions.
    field_name :
        Bloomberg field name for the policy-futures price (e.g.
        ``'PX_LAST'``).  Caller resolves the per-query sentinel
        against the YAML's ``default_price_field`` convention
        before passing.  The fetcher returns RAW field values —
        inverse-pricing handling (``implied_rate_pct = 100 -
        raw_price``) is owned by the per-tool ``compute.py``
        layer so the fetcher stays mechanism-agnostic.
    start_date :
        Earliest ``trade_date`` to include (inclusive).
    end_date :
        Latest ``trade_date`` (inclusive).  ``None`` → include
        every observation up to the latest in the DB.
    ffill_limit_days :
        Maximum holiday-gap to bridge via forward-fill per
        column.  Matches the standard ``ffill_limit_days``
        convention used by every other rates tool.  Set to 0 to
        disable forward-fill entirely.

    Returns
    -------
    panel_df : pd.DataFrame
        Wide DataFrame with a ``DatetimeIndex`` (one row per
        trading date in the fetched window) and one column per
        ``(curve_family, strip_position)`` cell, keyed by the
        flat ``"<CURVE_FAMILY>|<STRIP_POSITION>"`` string
        encoding.  Column ordering follows the caller-supplied
        ``curve_families`` × ``strip_positions`` Cartesian
        product so the wire is deterministic.  Empty DataFrame
        if no policy-futures row matches the filters.
    universe_meta : pd.DataFrame
        One row per ``(curve_family, strip_position)`` cell with
        ``vendor_ticker``, ``contract_code``, ``country``,
        ``currency``, the JSONB-resident ``inverse_pricing``
        flag, and the ``instrument_master.is_active`` /
        ``is_rolling_contract`` invariants.  Empty DataFrame if
        no policy-futures instrument matches the filters.
    """
    if not curve_families:
        raise ValueError(
            "fetch_policy_futures_strip_panel: curve_families is empty."
        )
    if not strip_positions:
        raise ValueError(
            "fetch_policy_futures_strip_panel: strip_positions is empty."
        )

    sql_params: Dict[str, object] = {
        "instrument_type": "policy_future",
        "field_name": field_name,
        "curve_families": list(curve_families),
        "strip_positions": [int(p) for p in strip_positions],
        "start_date": start_date.isoformat(),
    }

    end_filter = ""
    if end_date is not None:
        sql_params["end_date"] = end_date.isoformat()
        end_filter = " AND v.trade_date <= :end_date"

    sql = text(
        f"""
        SELECT
            v.trade_date,
            v.curve_family,
            (v.attributes->>'strip_position')::int AS strip_position,
            v.field_value::double precision        AS field_value
        FROM macro_data.v_market_data_daily_enriched AS v
        WHERE v.instrument_type = :instrument_type
          AND v.field_name      = :field_name
          AND v.curve_family    = ANY(:curve_families)
          AND (v.attributes->>'strip_position')::int = ANY(:strip_positions)
          AND v.field_value    IS NOT NULL
          AND v.trade_date     >= :start_date{end_filter}
        ORDER BY v.trade_date
        """
    )

    with engine.connect() as conn:
        result = conn.execute(sql, sql_params)
        rows = result.fetchall()
        columns = list(result.keys())

    universe_meta = fetch_policy_futures_strip_universe(
        engine=engine,
        curve_families=curve_families,
        strip_positions=strip_positions,
    )

    if not rows:
        return pd.DataFrame(), universe_meta

    raw_df = pd.DataFrame(rows, columns=columns)

    # Build the deterministic ordered list of (curve_family,
    # strip_position) column keys.  Cartesian product over the
    # caller-supplied curve_families × strip_positions so the wire
    # ordering is stable AND reproducible across runs (P4 +
    # determinism contract).  Missing cells (no observations at
    # all on a given (curve_family, strip_position) pair) stay as
    # fully-NaN columns so the caller's empty-column detection can
    # surface them explicitly.
    sorted_positions = sorted(int(p) for p in strip_positions)
    ordered_cells: List[Tuple[str, int]] = [
        (cf, pos)
        for cf in curve_families
        for pos in sorted_positions
    ]
    ordered_columns: List[str] = [
        _strip_panel_column_key(cf, pos) for cf, pos in ordered_cells
    ]

    # Encode the long-format raw frame's (curve_family,
    # strip_position) pairs into the flat column key BEFORE the
    # pivot so we can pivot on a single column.  ``aggfunc='first'``
    # because the DB has exactly one row per
    # (curve_family, strip_position, trade_date) for the same
    # field_name (the enriched view dedups on the underlying ingest
    # key).
    raw_df["_col_key"] = [
        _strip_panel_column_key(cf, int(pos))
        for cf, pos in zip(raw_df["curve_family"], raw_df["strip_position"])
    ]
    wide = (
        raw_df.pivot_table(
            index="trade_date",
            columns="_col_key",
            values="field_value",
            aggfunc="first",
        )
        .sort_index()
    )
    wide.index = pd.DatetimeIndex(pd.to_datetime(wide.index))

    # Reorder columns by the Cartesian (curve_family,
    # strip_position) ordering above.  Any column missing from
    # ``wide`` (no observations at all on the cell) sticks as a
    # fully-NaN column so downstream code can detect the gap
    # explicitly rather than silently shrinking the panel.
    for col in ordered_columns:
        if col not in wide.columns:
            wide[col] = float("nan")
    wide = wide[ordered_columns]

    if ffill_limit_days and ffill_limit_days > 0:
        wide = wide.ffill(limit=ffill_limit_days)

    return wide, universe_meta


def fetch_policy_futures_strip_universe(
    engine: Engine,
    *,
    curve_families: Sequence[str],
    strip_positions: Sequence[int],
) -> pd.DataFrame:
    """Resolve the live policy-futures strip universe (instrument-
    level reference rows) for the requested
    ``(curve_family, strip_position)`` cells.

    Returns one row per ``(curve_family, strip_position)`` with the
    load-bearing reference columns the methodology card needs
    (``vendor_ticker``, ``contract_code``, ``country``,
    ``currency``, and the JSONB-resident ``inverse_pricing`` flag).
    Filters identically to ``fetch_policy_futures_strip_panel``'s
    ``instrument_type='policy_future'`` AND
    ``is_rolling_contract=TRUE`` guards so the universe meta and the
    panel cannot drift.

    Used by ``build_policy_futures_strip_panel`` to surface the
    per-(curve_family, strip_position) reference metadata on the
    methodology card AND to derive the per-curve-family
    inverse-pricing flag used by the implied-rate conversion.
    """
    if not curve_families:
        raise ValueError(
            "fetch_policy_futures_strip_universe: curve_families is empty."
        )
    if not strip_positions:
        raise ValueError(
            "fetch_policy_futures_strip_universe: strip_positions is empty."
        )

    sql = text(
        """
        SELECT
            i.curve_family,
            (i.attributes->>'strip_position')::int  AS strip_position,
            i.vendor_ticker,
            i.contract_code,
            i.country,
            i.currency,
            (i.attributes->>'inverse_pricing')::bool AS inverse_pricing
        FROM macro_data.instrument_master AS i
        WHERE i.instrument_type      = :instrument_type
          AND i.is_rolling_contract  = TRUE
          AND i.is_active            = TRUE
          AND i.curve_family         = ANY(:curve_families)
          AND (i.attributes->>'strip_position')::int = ANY(:strip_positions)
        ORDER BY i.curve_family,
                 (i.attributes->>'strip_position')::int,
                 i.vendor_ticker
        """
    )

    with engine.connect() as conn:
        rows = conn.execute(
            sql,
            {
                "instrument_type": "policy_future",
                "curve_families": list(curve_families),
                "strip_positions": [int(p) for p in strip_positions],
            },
        ).mappings().all()

    return pd.DataFrame([dict(r) for r in rows])


__all__ = [
    "fetch_instrument_panel",
    "fetch_inflation_swap_panel_by_vendor_ticker",
    "fetch_inflation_swap_universe",
    "fetch_linker_panel_by_vendor_ticker",
    "fetch_linker_universe",
    "fetch_policy_futures_strip_panel",
    "fetch_policy_futures_strip_universe",
    "_strip_panel_column_key",
    "infer_units_for_field",
    "apply_missing_data_policy",
]
