"""
rates_fetch.py — Database helpers for rates analytics
======================================================

Thin SQL wrappers used by rates domain tools (sovereign_bonds, ois, and
any future sub-domain).  These functions query the shared enriched view
``macro_data.v_market_data_daily_enriched`` and return long-format
DataFrames that callers pivot / align as needed.

These helpers are instrument-type agnostic: sovereign bonds and OIS
swaps both live in the same base view, and the caller supplies the
``curve_family`` + ``field_name`` pair (and for scanners, the
``instrument_type``) that uniquely identifies the series of interest.

Query shapes
------------
Four canonical fetch shapes cover every rates tool we've built or
plan to build:

  - one curve, one tenor            → fetch_single_tenor
  - one curve, N tenors             → fetch_tenor_group
                                       (fetch_tenor_pair is a 2-tenor
                                        alias kept for compat with the
                                        original curve_spread refactor)
  - two curves, one tenor           → fetch_cross_market_pair
  - entire universe of one type     → fetch_scan_universe

All functions are parameterized (named SQL binds); no string
interpolation of user-supplied identifiers.
"""

from __future__ import annotations

from datetime import date
from typing import Dict, Iterable, Optional, Sequence

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine


# ============================================================================
# ONE CURVE, N TENORS
# ============================================================================
#
# ``contract_code`` (added in Step 0, TD#11): optional disambiguator for
# playbooks where ``(curve_family, tenor)`` is not unique. Today the only
# such playbook is ``bond_futures.yml`` (TY1/UXY1 both UST_FUT 10Y; US1/WN1
# both UST_FUT 30Y), where the per-row ``contract_code`` field (typed
# column on instrument_master) is the canonical disambiguator. Default
# ``None`` preserves the pre-Step-0 query exactly, so every existing
# sovereign / OIS / inflation caller works unchanged.

def fetch_tenor_group(
    engine: Engine,
    curve_family: str,
    tenors: Sequence[str],
    field_name: str,
    start_date: date,
    contract_code: Optional[str] = None,
) -> pd.DataFrame:
    """Fetch an arbitrary set of tenors on one curve.

    Returns a long-format DataFrame with columns
    ``['trade_date', 'tenor', 'field_value']``.  Empty DataFrame if no
    rows match the filter.

    ``tenors`` length can be 2 (curve_spread, curve_regime), 3
    (butterfly), or more.  Uses PostgreSQL's ``= ANY(:tenors)`` so the
    bound list can be arbitrary width without rewriting the SQL.

    ``contract_code`` (optional, default ``None``): when the
    ``(curve_family, tenor)`` pair is ambiguous (today: only
    ``bond_futures.yml`` — TY1 vs UXY1 share UST_FUT 10Y), pass the
    per-row ``contract_code`` from the playbook to narrow to a single
    instrument. Default ``None`` preserves the pre-Step-0 query.

    **Limitation.** The kwarg filters every requested tenor by the same
    ``contract_code``. That is correct for a single-contract single-tenor
    lookup but does NOT express the typical multi-leg futures-strip use
    case where each leg has its own ``contract_code`` (e.g. SFR1/SFR2/SFR3
    across the SOFR strip). The right shape for multi-leg futures fetches
    is a dedicated contract-keyed fetcher; that lands when the first
    bond-future / strip-snapshot primitive is built (Phase 2 of the
    primitive roadmap). Until then, callers needing multiple distinct
    contract codes call this fetcher once per ``contract_code``.
    """
    if not tenors:
        raise ValueError("fetch_tenor_group requires at least one tenor.")

    where_extra = " AND contract_code = :contract_code" if contract_code is not None else ""
    sql = text(f"""
        SELECT
            trade_date,
            tenor,
            field_value
        FROM macro_data.v_market_data_daily_enriched
        WHERE curve_family = :curve_family
          AND tenor        = ANY(:tenors)
          AND field_name   = :field_name
          AND trade_date  >= :start_date
          {where_extra}
        ORDER BY trade_date
    """)
    params: Dict[str, object] = {
        "curve_family": curve_family,
        "tenors": list(tenors),
        "field_name": field_name,
        "start_date": start_date.isoformat(),
    }
    if contract_code is not None:
        params["contract_code"] = contract_code

    with engine.connect() as conn:
        result = conn.execute(sql, params)
        rows = result.fetchall()
        columns = list(result.keys())
    return pd.DataFrame(rows, columns=columns)


# Backward-compatible 2-tenor alias so the existing curve_spread refactor
# keeps working unchanged.  Delegates to fetch_tenor_group so both shapes
# exercise the same underlying SQL.
def fetch_tenor_pair(
    engine: Engine,
    curve_family: str,
    short_tenor: str,
    long_tenor: str,
    field_name: str,
    start_date: date,
    contract_code: Optional[str] = None,
) -> pd.DataFrame:
    """Fetch exactly two tenors on one curve.  Thin wrapper around
    ``fetch_tenor_group`` preserved for the curve_spread tool's original
    call signature.

    ``contract_code`` (optional): see :func:`fetch_tenor_group` —
    disambiguator for playbooks where ``(curve_family, tenor)`` is not
    unique. Default ``None`` preserves the pre-Step-0 query.
    """
    return fetch_tenor_group(
        engine=engine,
        curve_family=curve_family,
        tenors=[short_tenor, long_tenor],
        field_name=field_name,
        start_date=start_date,
        contract_code=contract_code,
    )


# ============================================================================
# ONE CURVE, ONE TENOR
# ============================================================================

_FETCH_SINGLE_TENOR_SQL = text("""
    SELECT
        trade_date,
        field_value
    FROM macro_data.v_market_data_daily_enriched
    WHERE curve_family = :curve_family
      AND tenor        = :tenor
      AND field_name   = :field_name
      AND trade_date  >= :start_date
    ORDER BY trade_date
""")


_FETCH_SINGLE_TENOR_BY_CONTRACT_SQL = text("""
    SELECT
        trade_date,
        field_value
    FROM macro_data.v_market_data_daily_enriched
    WHERE curve_family  = :curve_family
      AND tenor         = :tenor
      AND field_name    = :field_name
      AND trade_date   >= :start_date
      AND contract_code = :contract_code
    ORDER BY trade_date
""")


# Same shape as ``_FETCH_SINGLE_TENOR_SQL`` plus an ``instrument_type``
# filter.  Kept as a separate static query so the unfiltered bind
# dictionary stays minimal and existing callers' DB query plans do not
# change.  The linker ``real_yield_level`` primitive uses this path with
# ``instrument_type='inflation_linker'`` to guarantee the tool cannot
# silently fall through to nominal sovereign rows under a real-yield
# label.
_FETCH_SINGLE_TENOR_TYPED_SQL = text("""
    SELECT
        trade_date,
        field_value
    FROM macro_data.v_market_data_daily_enriched
    WHERE curve_family    = :curve_family
      AND tenor           = :tenor
      AND field_name      = :field_name
      AND instrument_type = :instrument_type
      AND trade_date     >= :start_date
    ORDER BY trade_date
""")


# Both disambiguators at once: ``contract_code`` AND ``instrument_type``.
# No caller needs this combination today, but the public signature
# accepts both optional filters, so the both-set path must produce a
# correct query rather than silently dropping one filter.
_FETCH_SINGLE_TENOR_BY_CONTRACT_TYPED_SQL = text("""
    SELECT
        trade_date,
        field_value
    FROM macro_data.v_market_data_daily_enriched
    WHERE curve_family    = :curve_family
      AND tenor           = :tenor
      AND field_name      = :field_name
      AND contract_code   = :contract_code
      AND instrument_type = :instrument_type
      AND trade_date     >= :start_date
    ORDER BY trade_date
""")


def fetch_single_tenor(
    engine: Engine,
    curve_family: str,
    tenor: str,
    field_name: str,
    start_date: date,
    contract_code: Optional[str] = None,
    instrument_type: Optional[str] = None,
) -> pd.DataFrame:
    """Fetch a single-tenor series on one curve.

    Returns a long-format DataFrame with columns
    ``['trade_date', 'field_value']``.  Used by yield_levels and any
    future OIS single-rate tool.

    ``contract_code`` (optional, default ``None``): see
    :func:`fetch_tenor_group` — disambiguator for playbooks where
    ``(curve_family, tenor)`` is not unique (today: only
    ``bond_futures.yml``). Default ``None`` preserves the pre-Step-0
    query so every existing sovereign / OIS / inflation caller works
    unchanged.

    ``instrument_type`` (optional, default ``None``): when supplied
    (e.g. ``'inflation_linker'``), the row's ``instrument_type`` must
    match.  The linker ``real_yield_level`` primitive uses this to
    guarantee it cannot return nominal sovereign rows under a
    real-yield label.  Default ``None`` adds no instrument-type filter.

    The two filters are independent and orthogonal.  Passing neither
    reproduces the original query exactly — byte-for-byte unchanged SQL
    and bind dictionary for every pre-existing caller; either one alone,
    or both together, narrows the row set with the corresponding bound
    parameter(s).
    """
    params: Dict[str, object] = {
        "curve_family": curve_family,
        "tenor": tenor,
        "field_name": field_name,
        "start_date": start_date.isoformat(),
    }
    if contract_code is None and instrument_type is None:
        sql = _FETCH_SINGLE_TENOR_SQL
    elif instrument_type is None:
        sql = _FETCH_SINGLE_TENOR_BY_CONTRACT_SQL
        params["contract_code"] = contract_code
    elif contract_code is None:
        sql = _FETCH_SINGLE_TENOR_TYPED_SQL
        params["instrument_type"] = instrument_type
    else:
        sql = _FETCH_SINGLE_TENOR_BY_CONTRACT_TYPED_SQL
        params["contract_code"] = contract_code
        params["instrument_type"] = instrument_type
    with engine.connect() as conn:
        result = conn.execute(sql, params)
        rows = result.fetchall()
        columns = list(result.keys())
    return pd.DataFrame(rows, columns=columns)


# ============================================================================
# TWO CURVES, ONE TENOR
# ============================================================================

_FETCH_CROSS_MARKET_SQL = text("""
    SELECT
        trade_date,
        curve_family,
        field_value
    FROM macro_data.v_market_data_daily_enriched
    WHERE curve_family IN (:curve_family_1, :curve_family_2)
      AND tenor        = :tenor
      AND field_name   = :field_name
      AND trade_date  >= :start_date
    ORDER BY trade_date
""")


def fetch_cross_market_pair(
    engine: Engine,
    curve_family_1: str,
    curve_family_2: str,
    tenor: str,
    field_name: str,
    start_date: date,
    contract_code_1: Optional[str] = None,
    contract_code_2: Optional[str] = None,
) -> pd.DataFrame:
    """Fetch the same tenor on two different curves.

    Returns a long-format DataFrame with columns
    ``['trade_date', 'curve_family', 'field_value']``.  Callers pivot
    on ``curve_family`` (via ``pivot_and_align_tenors(key_col='curve_family')``).

    ``contract_code_1`` / ``contract_code_2`` (optional, both default
    ``None``): see :func:`fetch_tenor_group` — per-leg disambiguator for
    playbooks where ``(curve_family, tenor)`` is not unique (today: only
    bond_futures cross-country pairs like UST_FUT 10Y vs DE_FUT 10Y where
    each side has multiple `contract_code`s sharing 10Y). Pass each side's
    canonical contract code; pass ``None`` per leg if that side is
    unambiguous. Both default ``None`` preserves the pre-Step-0 query.
    """
    if contract_code_1 is None and contract_code_2 is None:
        sql = _FETCH_CROSS_MARKET_SQL
        params: Dict[str, object] = {
            "curve_family_1": curve_family_1,
            "curve_family_2": curve_family_2,
            "tenor": tenor,
            "field_name": field_name,
            "start_date": start_date.isoformat(),
        }
    else:
        # Per-leg disambiguator: each (curve_family, optional contract_code)
        # pair is matched independently with OR. A leg with no contract_code
        # matches every contract_code on that curve_family (pre-Step-0 shape).
        params = {
            "curve_family_1": curve_family_1,
            "curve_family_2": curve_family_2,
            "tenor": tenor,
            "field_name": field_name,
            "start_date": start_date.isoformat(),
        }
        leg1_clause = "curve_family = :curve_family_1"
        if contract_code_1 is not None:
            leg1_clause += " AND contract_code = :contract_code_1"
            params["contract_code_1"] = contract_code_1
        leg2_clause = "curve_family = :curve_family_2"
        if contract_code_2 is not None:
            leg2_clause += " AND contract_code = :contract_code_2"
            params["contract_code_2"] = contract_code_2
        sql = text(f"""
            SELECT
                trade_date,
                curve_family,
                field_value
            FROM macro_data.v_market_data_daily_enriched
            WHERE tenor       = :tenor
              AND field_name  = :field_name
              AND trade_date >= :start_date
              AND (
                    ({leg1_clause})
                 OR ({leg2_clause})
              )
            ORDER BY trade_date
        """)
    with engine.connect() as conn:
        result = conn.execute(sql, params)
        rows = result.fetchall()
        columns = list(result.keys())
    return pd.DataFrame(rows, columns=columns)


# ============================================================================
# CROSS-DOMAIN PAIR (ONE SOVEREIGN + ONE OIS, ONE TENOR)
# ============================================================================
#
# Distinct from ``fetch_cross_market_pair`` because the two legs use
# DIFFERENT ``field_name``s on the wire — sovereign yields are
# typically ``YLD_YTM_MID`` while OIS par swap rates are
# ``PX_LAST``.  ``fetch_cross_market_pair`` filters on a single
# field_name and therefore cannot be reused for cross-domain
# spreads.  This fetcher issues a single SQL query whose WHERE
# clause matches each leg's (curve_family, field_name) pair and
# returns the long-format frame the existing
# ``pivot_and_align_tenors(key_col='curve_family')`` consumes —
# same shape as ``fetch_cross_market_pair``'s output, so the
# downstream pipeline is unchanged.

_FETCH_CROSS_DOMAIN_SQL = text("""
    SELECT
        trade_date,
        curve_family,
        field_value
    FROM macro_data.v_market_data_daily_enriched
    WHERE tenor = :tenor
      AND trade_date >= :start_date
      AND (
            (curve_family = :sovereign_curve_family
             AND field_name = :sovereign_field_name)
         OR (curve_family = :ois_curve_family
             AND field_name = :ois_field_name)
      )
    ORDER BY trade_date
""")


def fetch_cross_domain_pair(
    engine: Engine,
    sovereign_curve_family: str,
    sovereign_field_name: str,
    ois_curve_family: str,
    ois_field_name: str,
    tenor: str,
    start_date: date,
) -> pd.DataFrame:
    """Fetch the same tenor on one sovereign curve + one OIS curve.

    Returns a long-format DataFrame with columns
    ``['trade_date', 'curve_family', 'field_value']`` — same shape
    as ``fetch_cross_market_pair`` returns, so the downstream
    pivot-and-align pipeline is unchanged.

    The two legs use different ``field_name`` mnemonics by
    convention (sovereign ``YLD_YTM_MID`` vs OIS ``PX_LAST``), so
    each leg's filter is matched explicitly in the WHERE clause.
    """
    with engine.connect() as conn:
        result = conn.execute(
            _FETCH_CROSS_DOMAIN_SQL,
            {
                "sovereign_curve_family": sovereign_curve_family,
                "sovereign_field_name": sovereign_field_name,
                "ois_curve_family": ois_curve_family,
                "ois_field_name": ois_field_name,
                "tenor": tenor,
                "start_date": start_date.isoformat(),
            },
        )
        rows = result.fetchall()
        columns = list(result.keys())
    return pd.DataFrame(rows, columns=columns)


# ============================================================================
# SCAN UNIVERSE (ALL INSTRUMENTS OF ONE TYPE)
# ============================================================================

_FETCH_SCAN_ALL_SQL = text("""
    SELECT
        trade_date,
        curve_family,
        tenor,
        contract_code,
        field_value
    FROM macro_data.v_market_data_daily_enriched
    WHERE instrument_type = :instrument_type
      AND field_name      = :field_name
      AND trade_date     >= :start_date
      AND tenor IS NOT NULL
    ORDER BY curve_family, tenor, trade_date
""")

_FETCH_SCAN_FILTERED_SQL = text("""
    SELECT
        trade_date,
        curve_family,
        tenor,
        contract_code,
        field_value
    FROM macro_data.v_market_data_daily_enriched
    WHERE instrument_type = :instrument_type
      AND field_name      = :field_name
      AND trade_date     >= :start_date
      AND tenor IS NOT NULL
      AND curve_family    = ANY(:curve_families)
    ORDER BY curve_family, tenor, trade_date
""")


def fetch_scan_universe(
    engine: Engine,
    instrument_type: str,
    field_name: str,
    start_date: date,
    curve_families: Optional[Iterable[str]] = None,
) -> pd.DataFrame:
    """Fetch every (curve_family, tenor) series of the given instrument
    type for use by scanner-style tools.

    Parameters
    ----------
    instrument_type : str
        Enriched-view instrument type — e.g. ``'sovereign_benchmark'``
        for sovereign scanners, ``'ois_swap'`` for OIS scanners.  This
        is the seam that makes a future ``ois_scanner`` a near-trivial
        port.
    curve_families : Optional[Iterable[str]]
        If None, scan everything of the given type.  If provided, scope
        to the named curves only.

    Returns
    -------
    pd.DataFrame with columns
    ``['trade_date', 'curve_family', 'tenor', 'contract_code', 'field_value']``.

    ``contract_code`` is included in the output so scanners that span a
    universe with non-unique ``(curve_family, tenor)`` (today: only
    ``bond_futures`` — TY1/UXY1 both UST_FUT 10Y, US1/WN1 both UST_FUT 30Y)
    can dedupe / disambiguate downstream. For unique-keyed universes
    (sovereign, OIS, inflation), every row in a given ``(curve_family,
    tenor)`` group will share the same ``contract_code`` (often NULL for
    cash-rate playbooks) — the column is harmless to ignore.

    Added in Step 0 (TD#11). Pre-Step-0 callers using named-column access
    are unaffected; the extra column is additive.
    """
    bind_params: dict = {
        "instrument_type": instrument_type,
        "field_name": field_name,
        "start_date": start_date.isoformat(),
    }
    if curve_families:
        bind_params["curve_families"] = list(curve_families)
        sql = _FETCH_SCAN_FILTERED_SQL
    else:
        sql = _FETCH_SCAN_ALL_SQL

    with engine.connect() as conn:
        result = conn.execute(sql, bind_params)
        rows = result.fetchall()
        columns = list(result.keys())
    return pd.DataFrame(rows, columns=columns)


# ============================================================================
# ON-THE-RUN (OTR) HISTORY FETCH — SCD2 substrate
# ============================================================================
#
# Reads ``macro_data.otr_history`` (ADR 0003) JOIN
# ``macro_data.instrument_master`` for one ``(country, tenor)`` slot,
# filtered to windows whose effective range intersects a calendar
# lookback window.  Used by the ``get_otr_history`` primitive today;
# future cash-bond primitives that resolve the OTR / OFR bond for a
# slot (``otr_ofr_spread``) call the same fetcher so the resolution
# logic exists in exactly one place (P10 — single source of truth).
#
# ``effective_to IS NULL`` (open window) is treated as
# ``'infinity'::date`` for the intersection check, matching the
# table's EXCLUDE GIST constraint convention from ADR 0003.

_FETCH_OTR_TRANSITIONS_SQL = text(
    """
    SELECT
        o.effective_from,
        o.effective_to,
        o.otr_instrument_id,
        i.cusip,
        i.isin,
        i.vendor_ticker,
        i.maturity_date
    FROM macro_data.otr_history o
    JOIN macro_data.instrument_master i
      ON i.instrument_id = o.otr_instrument_id
    WHERE o.country = :country
      AND o.tenor   = :tenor
      AND daterange(
              o.effective_from,
              COALESCE(o.effective_to, 'infinity'::date),
              '[]'
          ) && daterange(:window_start, :window_end, '[]')
    ORDER BY o.effective_from ASC
    """
)


def fetch_otr_transitions(
    engine: Engine,
    *,
    country: str,
    tenor: str,
    window_start: date,
    window_end: date,
) -> "list[dict]":
    """Fetch the SCD2 OTR transition log for one ``(country, tenor)`` slot.

    Returns one dict per ``otr_history`` row whose effective range
    intersects ``[window_start, window_end]``, sorted by
    ``effective_from`` ascending.  The currently-open window (the row
    with ``effective_to IS NULL``) is included when its
    ``effective_from`` falls on or before ``window_end``.

    Each dict has keys: ``effective_from``, ``effective_to`` (None on
    the open window), ``otr_instrument_id``, ``cusip``, ``isin``,
    ``vendor_ticker``, ``maturity_date``.  Date columns are returned
    as the DB driver's ``date`` objects; the caller is responsible for
    ISO-coercion at the wire layer if needed.

    Returns the empty list (never ``None``) when no window intersects
    the lookback — honest absence per P6 (the absence is information,
    not failure).  This is the expected pre-resolver-deployment shape
    documented in TD #27a.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine.
    country : str
        Sovereign country code as stored in ``macro_data.otr_history``
        (uppercase ISO-3166-alpha-2/3 per ADR 0007 §4 + 0005 §3).
    tenor : str
        Canonical slot tenor (integer-Y form matching
        ``sovereign_cash_bonds.yml``).
    window_start, window_end : date
        Calendar boundaries of the intersection check.  Windows whose
        effective range overlaps ``[window_start, window_end]``
        (inclusive on both ends, matching the table's EXCLUDE GIST
        boundary convention) are returned.
    """
    with engine.connect() as conn:
        rows = (
            conn.execute(
                _FETCH_OTR_TRANSITIONS_SQL,
                {
                    "country": str(country),
                    "tenor": str(tenor),
                    "window_start": window_start.isoformat(),
                    "window_end": window_end.isoformat(),
                },
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


# ============================================================================
# ON-THE-RUN / OFF-THE-RUN YIELD PAIR FETCH
# ============================================================================
#
# Reads ``macro_data.otr_history`` (ADR 0003) JOIN
# ``macro_data.market_data_daily`` to resolve, per trade_date, the OTR
# bond's yield AND the prior-bond's yield (the "first off-the-run" /
# OFR, defined as the bond from the SCD2 window immediately prior to
# whichever window covers the trade_date).
#
# The OFR-resolution shape is a window-function LAG over the SCD2
# rows for the slot: each row's OFR is the otr_instrument_id of the
# previous row by ``effective_from``.  This collapses to a single SQL
# query so the resolution logic lives in ONE place — the
# ``otr_ofr_spread`` primitive's compute() reads this output and runs
# pure arithmetic on it.
#
# Used by the ``otr_ofr_spread`` primitive (rates_agent/sovereign_bonds/
# tools/otr_ofr_spread/).  Future cash-bond primitives that need the
# same (date → OTR/OFR yield pair) resolution call this fetcher so
# the desk-concept "OFR = the bond that was OTR immediately prior" is
# defined in exactly one place (P10 — single source of truth).

_FETCH_OTR_OFR_YIELD_PAIR_SQL = text(
    """
    WITH slot_windows AS (
        SELECT
            o.effective_from,
            o.effective_to,
            o.otr_instrument_id,
            LAG(o.otr_instrument_id) OVER (
                PARTITION BY o.country, o.tenor
                ORDER BY o.effective_from
            ) AS ofr_instrument_id
        FROM macro_data.otr_history o
        WHERE o.country = :country
          AND o.tenor   = :tenor
    ),
    date_resolved AS (
        SELECT
            w.effective_from   AS window_effective_from,
            COALESCE(w.effective_to, 'infinity'::date)
                               AS window_effective_to,
            w.otr_instrument_id,
            w.ofr_instrument_id
        FROM slot_windows w
        WHERE daterange(
                  w.effective_from,
                  COALESCE(w.effective_to, 'infinity'::date),
                  '[]'
              ) && daterange(:window_start, :window_end, '[]')
    )
    SELECT
        d_otr.trade_date,
        dr.otr_instrument_id,
        dr.ofr_instrument_id,
        d_otr.field_value AS otr_yield,
        d_ofr.field_value AS ofr_yield
    FROM date_resolved dr
    JOIN macro_data.market_data_daily d_otr
      ON d_otr.instrument_id = dr.otr_instrument_id
     AND d_otr.field_name    = :field_name
     AND d_otr.trade_date BETWEEN
         GREATEST(dr.window_effective_from, :window_start::date)
         AND LEAST(dr.window_effective_to, :window_end::date)
    LEFT JOIN macro_data.market_data_daily d_ofr
      ON d_ofr.instrument_id = dr.ofr_instrument_id
     AND d_ofr.field_name    = :field_name
     AND d_ofr.trade_date    = d_otr.trade_date
    ORDER BY d_otr.trade_date ASC
    """
)


def fetch_otr_ofr_yield_pair(
    engine: Engine,
    *,
    country: str,
    tenor: str,
    field_name: str,
    window_start: date,
    window_end: date,
) -> pd.DataFrame:
    """Fetch the time-varying OTR/OFR yield pair for one ``(country, tenor)``
    sovereign cash-bond slot.

    For each ``trade_date`` in ``[window_start, window_end]``:

    - resolves the OTR bond as the ``otr_instrument_id`` of the
      ``otr_history`` window whose ``[effective_from, effective_to]``
      contains the date (NULL ``effective_to`` treated as
      ``'infinity'::date``);
    - resolves the OFR bond as the ``otr_instrument_id`` of the
      SCD2 row immediately prior (window function ``LAG`` over
      ``effective_from``);
    - pulls ``field_value`` for the OTR instrument from
      ``market_data_daily`` (required); pulls the OFR instrument's
      ``field_value`` (LEFT JOIN — may be NULL if the OFR bond has no
      observation on that date, e.g. the first OTR row of the slot
      whose LAG is NULL).

    The LEFT JOIN on the OFR yield means the returned frame has one
    row per OTR observation in the window; ``ofr_yield`` is ``None``
    for rows where either the SCD2 LAG is NULL (the very first
    resolver-observed window for the slot — no prior bond exists in
    history) or the OFR bond has no ingested yield for that date
    (e.g. the bond matured / lost coverage before the OTR roll).
    The compute layer treats ``None`` honestly — the spread is
    ``None`` on those dates, NOT a fabricated number.

    Returns
    -------
    pd.DataFrame with columns
    ``['trade_date', 'otr_instrument_id', 'ofr_instrument_id',
       'otr_yield', 'ofr_yield']``.

    Empty DataFrame when no OTR window overlaps the lookback (honest
    absence per P6 — the pre-resolver-deployment shape documented in
    TD #27a).

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine.
    country : str
        Sovereign country code as stored in ``macro_data.otr_history``
        (uppercase ISO-3166-alpha-2/3 per ADR 0007 §4 + ADR 0005 §3).
    tenor : str
        Canonical slot tenor (integer-Y form matching
        ``sovereign_cash_bonds.yml``).
    field_name : str
        Bloomberg field mnemonic to read from ``market_data_daily``.
        Sovereign cash-bond yields are stored under ``YLD_YTM_MID``
        (ADR 0005 — the verified 14-mnemonic real-bond field set).
    window_start, window_end : date
        Inclusive calendar boundaries of the lookback window the
        primitive displays.  Both the OTR window-intersection check
        and the per-date BETWEEN filter use this range.
    """
    with engine.connect() as conn:
        result = conn.execute(
            _FETCH_OTR_OFR_YIELD_PAIR_SQL,
            {
                "country": str(country),
                "tenor": str(tenor),
                "field_name": str(field_name),
                "window_start": window_start.isoformat(),
                "window_end": window_end.isoformat(),
            },
        )
        rows = result.fetchall()
        columns = list(result.keys())
    return pd.DataFrame(rows, columns=columns)
