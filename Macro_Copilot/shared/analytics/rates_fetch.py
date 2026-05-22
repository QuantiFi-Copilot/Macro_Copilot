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
# ONE CURVE, ONE / N STRIP POSITION(S) — futures-keyed analogs
# ============================================================================
#
# Policy futures (SOFR_FUT, EUR_SHORT_RATE_FUT, SONIA_FUT) are keyed by
# ``(curve_family, strip_position)`` — NOT ``(curve_family, tenor)`` —
# because the desk instrument identity is the position on the strip
# (SFR1 = front; SFR2..SFR8 = quarterly forwards), not a calendar tenor.
# ``strip_position`` lives in ``instrument_master.attributes`` JSONB as an
# integer; the enriched view exposes the whole JSONB so we extract it via
# ``(attributes->>'strip_position')::int`` in the WHERE clause.
#
# These helpers mirror the tenor-keyed helpers above 1:1 in shape and return
# the same long-format DataFrame columns, so callers downstream of the fetch
# (pivot, align, compute_level_metrics, etc.) work without per-fetcher
# branching. The single difference is the key: ``strip_position: int``
# replaces ``tenor: str``.
#
# Bond futures are NOT strip-position-keyed in the same way — they are
# keyed by ``(curve_family, contract_code)`` (TY1 vs UXY1 share UST_FUT 10Y;
# US1 vs WN1 share UST_FUT 30Y) — so bond-futures monitors use the existing
# ``fetch_single_tenor(..., contract_code=)`` path. The strip-aware helpers
# below are for the policy_futures domain specifically.


_FETCH_STRIP_POSITION_SQL = text("""
    SELECT
        trade_date,
        field_value
    FROM macro_data.v_market_data_daily_enriched
    WHERE curve_family = :curve_family
      AND (attributes->>'strip_position')::int = :strip_position
      AND field_name   = :field_name
      AND trade_date  >= :start_date
    ORDER BY trade_date
""")


def fetch_strip_position(
    engine: Engine,
    curve_family: str,
    strip_position: int,
    field_name: str,
    start_date: date,
) -> pd.DataFrame:
    """Fetch a single strip-position series on one futures curve.

    For strip-position-keyed instruments — today: policy futures
    (SOFR_FUT, EUR_SHORT_RATE_FUT, SONIA_FUT) — where the desk
    instrument is identified by ``(curve_family, strip_position)``
    rather than ``(curve_family, tenor)``. ``strip_position`` lives
    in ``instrument_master.attributes`` JSONB as an integer; this
    helper extracts it via ``(attributes->>'strip_position')::int``
    in the WHERE clause.

    Returns a long-format DataFrame with columns
    ``['trade_date', 'field_value']``.

    Examples
    --------
    - ``futures_price_level`` for SFR1 (front SOFR):
      ``field_name='last_price'``, ``curve_family='SOFR_FUT'``,
      ``strip_position=1``.
    - ``volume_open_interest_snapshot`` for SFR2:
      ``field_name='open_interest'``, ``strip_position=2``.
    """
    with engine.connect() as conn:
        result = conn.execute(
            _FETCH_STRIP_POSITION_SQL,
            {
                "curve_family": curve_family,
                "strip_position": strip_position,
                "field_name": field_name,
                "start_date": start_date.isoformat(),
            },
        )
        rows = result.fetchall()
        columns = list(result.keys())
    return pd.DataFrame(rows, columns=columns)


_FETCH_STRIP_GROUP_SQL = text("""
    SELECT
        trade_date,
        (attributes->>'strip_position')::int AS strip_position,
        field_value
    FROM macro_data.v_market_data_daily_enriched
    WHERE curve_family = :curve_family
      AND (attributes->>'strip_position')::int = ANY(:strip_positions)
      AND field_name   = :field_name
      AND trade_date  >= :start_date
    ORDER BY trade_date, strip_position
""")


def fetch_strip_group(
    engine: Engine,
    curve_family: str,
    strip_positions: Sequence[int],
    field_name: str,
    start_date: date,
) -> pd.DataFrame:
    """Fetch a set of strip positions on one futures curve.

    Multi-position analogue of :func:`fetch_strip_position`. Used by
    every policy-futures primitive that consumes ≥ 2 strip positions
    on one curve — ``futures_calendar_spread`` (2 positions),
    ``futures_butterfly_simple`` (3 positions),
    ``futures_pack_average_simple`` (whites = positions 1-4 or reds =
    positions 5-8), and ``futures_strip_snapshot`` (all 8).

    Returns a long-format DataFrame with columns
    ``['trade_date', 'strip_position', 'field_value']``. Callers pivot
    on ``strip_position`` (via ``pivot_and_align_tenors(key_col='strip_position')``)
    to align across positions.
    """
    if not strip_positions:
        raise ValueError(
            "fetch_strip_group requires at least one strip_position."
        )
    with engine.connect() as conn:
        result = conn.execute(
            _FETCH_STRIP_GROUP_SQL,
            {
                "curve_family": curve_family,
                "strip_positions": list(strip_positions),
                "field_name": field_name,
                "start_date": start_date.isoformat(),
            },
        )
        rows = result.fetchall()
        columns = list(result.keys())
    return pd.DataFrame(rows, columns=columns)


_FETCH_CROSS_MARKET_STRIP_SQL = text("""
    SELECT
        trade_date,
        curve_family,
        field_value
    FROM macro_data.v_market_data_daily_enriched
    WHERE curve_family IN (:curve_family_1, :curve_family_2)
      AND (attributes->>'strip_position')::int = :strip_position
      AND field_name   = :field_name
      AND trade_date  >= :start_date
    ORDER BY trade_date, curve_family
""")


def fetch_cross_market_strip(
    engine: Engine,
    curve_family_1: str,
    curve_family_2: str,
    strip_position: int,
    field_name: str,
    start_date: date,
) -> pd.DataFrame:
    """Fetch the same strip position on two different futures curves.

    Used by ``futures_cross_market_spread`` for matched-strip cross-CB
    implied-rate differentials (e.g. SOFR_FUT SFR2 vs SONIA_FUT SFI2).

    Returns a long-format DataFrame with columns
    ``['trade_date', 'curve_family', 'field_value']``. Callers pivot
    on ``curve_family`` to align the two legs.

    P5 caveat (benchmark-family mismatch). SOFR / SONIA futures reference
    a compounded RFR (3-month look-back at expiry); Euribor
    (``EUR_SHORT_RATE_FUT``) references unsecured 3M term-Euribor —
    structurally different rate objects. Cross-CB spreads computed off
    this fetcher ALWAYS ship with a methodology-card disclosure naming
    the two underlyings explicitly.
    """
    with engine.connect() as conn:
        result = conn.execute(
            _FETCH_CROSS_MARKET_STRIP_SQL,
            {
                "curve_family_1": curve_family_1,
                "curve_family_2": curve_family_2,
                "strip_position": strip_position,
                "field_name": field_name,
                "start_date": start_date.isoformat(),
            },
        )
        rows = result.fetchall()
        columns = list(result.keys())
    return pd.DataFrame(rows, columns=columns)
