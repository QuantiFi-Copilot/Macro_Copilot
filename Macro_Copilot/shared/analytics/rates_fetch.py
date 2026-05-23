"""
rates_fetch.py — Database helpers for rates analytics
======================================================

Thin SQL wrappers used by rates domain tools (sovereign_bonds, ois,
policy_futures, bond_futures, and any future sub-domain). Most helpers
return long-format DataFrames that callers pivot / align as needed.

Helper paths
------------
This module exposes **two distinct read paths**, and they are not
interchangeable. Honest single-source-of-truth disclosure (P10):

1. **Enriched-view helpers** — query the shared denormalized view
   ``macro_data.v_market_data_daily_enriched``. The view joins
   ``market_data_daily`` to ``instrument_master`` and exposes
   ``curve_family`` / ``tenor`` / ``instrument_type`` / ``contract_code``
   / ``attributes`` directly, so the caller supplies the
   ``curve_family`` + ``field_name`` pair (plus ``tenor`` /
   ``strip_position`` / ``instrument_type`` as appropriate) that
   uniquely identifies the series of interest:

     - ``fetch_single_tenor``
     - ``fetch_tenor_group``
     - ``fetch_tenor_pair``  (2-tenor alias of ``fetch_tenor_group``
       kept for compat with the original curve_spread refactor)
     - ``fetch_cross_market_pair``
     - ``fetch_scan_universe``
     - ``fetch_strip_position``           (policy futures, strip-keyed)
     - ``fetch_strip_group``              (policy futures, strip-keyed)
     - ``fetch_cross_market_strip``       (policy futures, strip-keyed)

   These cover sovereign bonds, OIS swaps, and policy futures cleanly:
   they all live in the same base view and the desk instrument identity
   is a tenor or a strip position.

2. **Direct-join helpers for rolling-generic bond futures** — do **NOT**
   go through the enriched view. They join
   ``macro_data.market_data_daily`` to ``macro_data.instrument_master``
   directly and filter on the master stem with
   ``is_rolling_contract = TRUE``:

     - ``fetch_rolling_generic_series``
     - ``fetch_rolling_generic_reference``
     - ``fetch_rolling_generic_universe_series``

   Why a second path exists: the enriched view's ``contract_code``
   column COALESCEs the per-day-effective SCD2 history value (TYH6,
   TYM6, TYU6, … rotating as the front rolls) onto the
   ``instrument_master`` stem. For rolling-generic rows the history
   value wins, so filtering the enriched view by
   ``contract_code = 'TY1'`` returns ZERO rows even though TY1 has
   years of price history. The canonical rolling-generic stem (TY1,
   UXY1, US1, WN1, RX1, JB1, OAT1, YM1, XM1, …) lives only on
   ``instrument_master.contract_code``, so the rolling-generic
   helpers must filter the master directly. See the in-file section
   comments at the strip-aware block (L544–587) and the
   ``ROLLING-GENERIC FUTURES`` block (L748–810) for the full
   rationale, and ``ADR 0011`` for the bond-futures V1 scope.

Query shapes
------------
The currently-shipped fetch shapes are:

  - one curve, one tenor                  → fetch_single_tenor
  - one curve, N tenors                   → fetch_tenor_group
                                             (fetch_tenor_pair is a
                                              2-tenor alias kept for
                                              compat with the original
                                              curve_spread refactor)
  - two curves, one tenor                 → fetch_cross_market_pair
  - entire universe of one type           → fetch_scan_universe
  - one curve, one strip position         → fetch_strip_position
  - one curve, N strip positions          → fetch_strip_group
  - two curves, matched strip positions   → fetch_cross_market_strip
  - one rolling-generic futures series    → fetch_rolling_generic_series
  - rolling-generic instrument metadata   → fetch_rolling_generic_reference
  - rolling-generic universe scan, one    → fetch_rolling_generic_universe_series
    field across many stems

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
    ``contract_code`` AND filters via the enriched view's
    ``contract_code`` column. Neither matches the bond-futures rolling-
    generic shape: (a) the enriched view's ``contract_code`` exposes the
    per-window SCD2 history value (TYH6 / TYM6 / ... rotating as the
    front rolls), NOT the master stem (TY1), so filtering by
    ``contract_code = 'TY1'`` here returns ZERO rows; (b) multi-leg
    futures strips have a distinct ``contract_code`` per leg
    (SFR1/SFR2/... across the SOFR strip). The right shapes are the
    dedicated fetchers in the futures sections below:
    ``fetch_strip_position`` / ``fetch_strip_group`` /
    ``fetch_cross_market_strip`` for policy-futures strip-position-keyed
    series, and ``fetch_rolling_generic_series`` /
    ``fetch_rolling_generic_reference`` for bond-futures rolling-generic
    stems (TY1, UXY1, RX1, JB1, ...). This kwarg path remains for the
    sovereign / cash playbooks where ``(curve_family, tenor)`` is
    occasionally non-unique and the enriched view's ``contract_code``
    matches the master stem.
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
# keyed by ``(curve_family, contract_code)`` where ``contract_code`` is
# the master rolling-generic stem (TY1, UXY1, US1, WN1, RX1, JB1, ...).
# The strip-aware helpers below are for the ``policy_futures`` domain
# specifically.
#
# Bond-futures monitors do NOT use the ``fetch_single_tenor(...,
# contract_code=)`` path. That path filters on the enriched view's
# ``contract_code`` column, whose value is the per-day-effective SCD2
# history value (TYH6, TYM6, TYU6, ... rotating as the front rolls) — NOT
# the master stem. Filtering the enriched view by ``contract_code = 'TY1'``
# therefore returns ZERO rows even though TY1 has years of price history.
#
# The canonical path for bond-futures rolling-generic series is the pair
# of helpers in the ``ROLLING-GENERIC FUTURES`` section below
# (``fetch_rolling_generic_series`` / ``fetch_rolling_generic_reference``).
# Those helpers join ``market_data_daily`` directly to ``instrument_master``
# and filter on the master stem with ``is_rolling_contract = TRUE``, which
# is the only filter that selects the bond-futures rolling-generic
# universe correctly.


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


# ============================================================================
# ROLLING-GENERIC FUTURES (bond futures: TY1, UXY1, RX1, JB1, ...)
# ============================================================================
#
# Bond-futures rolling-generics are keyed by ``(curve_family, contract_code)``
# where ``contract_code`` is the rolling-generic stem (TY1, UXY1, US1, WN1,
# RX1, JB1, ...) — distinct from per-window underlying contract codes
# (TYH6, TYM6, ...). The enriched view's ``contract_code`` column COALESCEs
# the per-day-effective ``instrument_metadata_history.contract_code`` (which
# rotates as the front rolls: TYH6 -> TYM6 -> TYU6 -> ...) onto the
# instrument_master stem; for rolling-generic rows the history value wins,
# so filtering the enriched view by ``contract_code = 'TY1'`` returns ZERO
# rows even though TY1 has years of price history.
#
# The canonical stem (TY1 etc.) lives only on ``instrument_master.contract_code``.
# To fetch a rolling-generic series we therefore join ``market_data_daily``
# to ``instrument_master`` directly and filter on the master stem. This is
# the read-side analogue of the ``contract_code`` disambiguator named in
# TD#11; see also ADR 0011 §"Bond futures".

_FETCH_ROLLING_GENERIC_SERIES_SQL = text("""
    SELECT
        d.trade_date,
        d.field_value
    FROM macro_data.market_data_daily d
    JOIN macro_data.instrument_master i
      ON d.instrument_id = i.instrument_id
    WHERE i.curve_family   = :curve_family
      AND i.contract_code  = :contract_code
      AND i.is_rolling_contract = TRUE
      AND d.field_name     = :field_name
      AND d.trade_date    >= :start_date
    ORDER BY d.trade_date
""")


def fetch_rolling_generic_series(
    engine: Engine,
    curve_family: str,
    contract_code: str,
    field_name: str,
    start_date: date,
) -> pd.DataFrame:
    """Fetch a single rolling-generic futures series (e.g. TY1 PX_LAST).

    Returns a long-format DataFrame with columns
    ``['trade_date', 'field_value']`` — same shape as
    :func:`fetch_single_tenor` so downstream cleaners (
    ``clean_single_series``) work without per-fetcher branching.

    Why this is its own fetcher (not ``fetch_single_tenor`` with
    ``contract_code=``). The enriched view's ``contract_code`` column
    COALESCEs the per-day-effective rolling history onto the master
    stem, so for rolling-generic rows the value is the per-window
    underlying (TYH6 etc.), NOT the stem (TY1). Filtering the enriched
    view by ``contract_code = 'TY1'`` therefore returns zero rows.
    The canonical rolling-generic stem lives only on
    ``instrument_master.contract_code``, so this fetcher joins
    ``market_data_daily`` to ``instrument_master`` directly and filters
    on the master stem. Restricted to ``is_rolling_contract = TRUE``
    so it cannot silently pick up a cash sovereign row whose
    ``contract_code`` happens to collide.

    Parameters
    ----------
    curve_family : str
        Futures curve family (e.g. ``'UST_FUT'``, ``'DE_FUT'``,
        ``'UK_FUT'``, ``'JP_FUT'``, ...).
    contract_code : str
        Rolling-generic stem from the playbook universe — exactly as
        stored on ``instrument_master.contract_code``. Examples:
        ``'TY1'``, ``'UXY1'``, ``'US1'``, ``'WN1'``, ``'RX1'``,
        ``'JB1'``, ``'OAT1'``, ``'YM1'``, ``'XM1'``.
    field_name : str
        Bloomberg observation field mnemonic — typically ``'PX_LAST'``
        for price, ``'OPEN_INT'`` for open interest, ``'PX_VOLUME'``
        for volume.
    start_date : date
        Inclusive lower bound on ``trade_date``.
    """
    with engine.connect() as conn:
        result = conn.execute(
            _FETCH_ROLLING_GENERIC_SERIES_SQL,
            {
                "curve_family": curve_family,
                "contract_code": contract_code,
                "field_name": field_name,
                "start_date": start_date.isoformat(),
            },
        )
        rows = result.fetchall()
        columns = list(result.keys())
    return pd.DataFrame(rows, columns=columns)


# Reference metadata snapshot for one rolling-generic: latest effective
# expiry / security_name from the SCD2 history table (falling back to
# instrument_master typed columns when the history table has no row),
# plus ``quote_units`` and ``contract_size`` from
# ``instrument_master.attributes`` JSONB (these live on master, not on
# the per-window history, because the rolling-generic's quote
# convention does not change as the front rolls).

_FETCH_ROLLING_GENERIC_REFERENCE_SQL = text("""
    SELECT
        i.contract_code      AS contract_code,
        i.curve_family       AS curve_family,
        i.tenor              AS tenor,
        COALESCE(h.expiry_date, i.expiry_date)   AS expiry_date,
        h.security_name      AS security_name,
        i.attributes->>'quote_units'   AS quote_units,
        (i.attributes->>'contract_size')::double precision AS contract_size
    FROM macro_data.instrument_master i
    LEFT JOIN LATERAL (
        SELECT expiry_date, security_name
        FROM macro_data.instrument_metadata_history
        WHERE instrument_id = i.instrument_id
        ORDER BY effective_from DESC
        LIMIT 1
    ) h ON TRUE
    WHERE i.curve_family   = :curve_family
      AND i.contract_code  = :contract_code
      AND i.is_rolling_contract = TRUE
    LIMIT 1
""")


def fetch_rolling_generic_reference(
    engine: Engine,
    curve_family: str,
    contract_code: str,
) -> Optional[Dict[str, object]]:
    """Fetch the reference metadata for one rolling-generic stem.

    Returns ``None`` when the ``(curve_family, contract_code)`` pair is
    not present on ``instrument_master`` as a rolling-generic. Returns
    a dict with keys ``contract_code``, ``curve_family``, ``tenor``,
    ``expiry_date`` (date or None), ``security_name`` (str or None),
    ``quote_units`` (str or None), ``contract_size`` (float or None).

    The ``expiry_date`` / ``security_name`` are taken from the latest
    effective-window row in ``instrument_metadata_history`` (rolling-
    generic metadata rotates as the front contract rolls), falling
    back to ``instrument_master`` columns when the history table has
    no rows for the instrument. ``quote_units`` and ``contract_size``
    come from ``instrument_master.attributes`` JSONB — these don't
    rotate with the front contract for a given rolling-generic.

    Used by ``bond_futures`` monitor primitives so the wire payload
    carries the per-contract disclosure (e.g. ``quote_units = "points"``
    for TY1, ``"% of par value"`` for RX1) that P5 requires alongside
    the rolling-generic price level.
    """
    with engine.connect() as conn:
        row = conn.execute(
            _FETCH_ROLLING_GENERIC_REFERENCE_SQL,
            {
                "curve_family": curve_family,
                "contract_code": contract_code,
            },
        ).mappings().first()
    if row is None:
        return None
    return dict(row)


# ============================================================================
# ROLLING-GENERIC UNIVERSE SCAN — many stems × one field, one round-trip
# ============================================================================
#
# Universe-scan analogue of ``fetch_rolling_generic_series``. Returns
# every (curve_family, contract_code) rolling-generic stem's series for
# one field across the named curve families, in ONE query. Used by the
# bond-futures morning-extremes scanner (V1 monitor #3 per ADR 0011 —
# ``rates_agent.bond_futures.tools.scan_bond_futures_extremes``); the
# per-stem ``fetch_rolling_generic_series`` would otherwise force the
# scanner into one round-trip per stem per field (19 stems × 3 fields =
# 57 round-trips per scan), which is structurally wrong for a morning
# sweep that wants ONE consistent snapshot across the universe.
#
# Why a NEW helper rather than reuse ``fetch_scan_universe``:
# ``fetch_scan_universe`` queries the enriched view, whose
# ``contract_code`` column COALESCEs the per-day-effective SCD2 history
# value (TYH6 / TYM6 / TYU6 ...) onto the master stem — for rolling-
# generic rows the history value wins. Filtering or grouping on that
# column splits each rolling-generic into N per-window groups instead
# of one stable stem-keyed group. The direct-join path (this helper)
# preserves the stem (TY1, UXY1, ...) as the group key, which is the
# only correct shape for a universe scan over rolling-generics.
#
# This helper is the read-side mirror of the per-stem helper above,
# scaled to N stems in one query. Single source of truth (P10): the
# scanner reaches the DB through this helper rather than embedding raw
# SQL in its ``compute.py``.

_FETCH_ROLLING_GENERIC_UNIVERSE_SERIES_SQL = text("""
    SELECT
        d.trade_date,
        i.curve_family,
        i.contract_code,
        i.tenor,
        d.field_value
    FROM macro_data.market_data_daily d
    JOIN macro_data.instrument_master i
      ON d.instrument_id = i.instrument_id
    WHERE i.curve_family        = ANY(:curve_families)
      AND i.is_rolling_contract = TRUE
      AND i.tenor              IS NOT NULL
      AND d.field_name          = :field_name
      AND d.trade_date         >= :start_date
      AND (CAST(:end_date AS DATE) IS NULL OR d.trade_date <= CAST(:end_date AS DATE))
    ORDER BY i.curve_family, i.contract_code, d.trade_date
""")


# Cheap "is the requested as-of beyond what we have ingested" probe used
# by the bond-futures universe scanner's future-anchor guard. Mirrors the
# universe-series fetcher's filter shape exactly (same join, same
# rolling-contract filter, same NULL-tenor guard) so the MAX(trade_date)
# it returns is the SAME observation date the series fetcher would have
# bounded against — no possibility of the probe and the fetcher
# disagreeing about "what's the universe's last trading day".
_FETCH_ROLLING_GENERIC_UNIVERSE_MAX_DATE_SQL = text("""
    SELECT MAX(d.trade_date) AS max_trade_date
    FROM macro_data.market_data_daily d
    JOIN macro_data.instrument_master i
      ON d.instrument_id = i.instrument_id
    WHERE i.curve_family        = ANY(:curve_families)
      AND i.is_rolling_contract = TRUE
      AND i.tenor              IS NOT NULL
""")


def fetch_rolling_generic_universe_series(
    engine: Engine,
    curve_families: Sequence[str],
    field_name: str,
    start_date: date,
    end_date: Optional[date] = None,
) -> pd.DataFrame:
    """Fetch one field across the entire rolling-generic universe for
    the named curve families.

    Returns a long-format DataFrame with columns
    ``['trade_date', 'curve_family', 'contract_code', 'tenor',
       'field_value']``.

    Filters the direct-join path
    (``market_data_daily JOIN instrument_master``) on
    ``is_rolling_contract = TRUE`` and the named ``curve_families`` —
    so for the bond-futures scanner's typical call (curve_families =
    sovereign-bond futures families per ADR 0011, ``tenor IS NOT
    NULL`` excludes the policy-futures strip-position-keyed rows
    that have NULL tenor on instrument_master). Returns every stem's
    series in one query; the caller groups by
    ``(curve_family, contract_code)`` to materialise per-stem series.

    Why ``tenor IS NOT NULL``: in the rolling-generic universe today,
    sovereign-bond futures (UST_FUT etc.) carry a tenor (2Y / 5Y /
    10Y / 30Y); policy-futures (SOFR_FUT etc.) carry NULL tenor on
    instrument_master (strip-position-keyed via ``attributes`` JSONB
    instead). Filtering ``tenor IS NOT NULL`` is the cheapest correct
    guard against accidentally including a mis-labelled policy-
    futures stem in a bond-futures scanner call — even if a caller
    passes a policy-futures family in ``curve_families`` (which the
    scanner's input schema rejects via the closed-family whitelist),
    this SQL still returns zero policy-futures rows. The schema
    validation is the primary refusal; this filter is belt-and-
    braces.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine.
    curve_families : Sequence[str]
        Bond-futures curve families to scan (e.g. ``['UST_FUT',
        'DE_FUT', 'UK_FUT', 'JP_FUT', 'FR_FUT', 'IT_FUT', 'ES_FUT',
        'CA_FUT', 'AU_FUT']``).
    field_name : str
        Bloomberg observation field mnemonic — typically ``'PX_LAST'``
        for price, ``'OPEN_INT'`` for open interest, ``'PX_VOLUME'``
        for volume.
    start_date : date
        Inclusive lower bound on ``trade_date``.
    end_date : date, optional
        Inclusive upper bound on ``trade_date``. When supplied, the
        SQL predicate adds ``AND d.trade_date <= :end_date`` so the
        result set is anchored at a specific DB-as-of for
        deterministic Layer-B validation and to support the bond-
        futures scanner's future-anchor guard (an LLM-supplied
        ``as_of_date`` beyond the universe's last ingested
        ``trade_date`` is rejected upstream; this upper bound is the
        belt-and-braces SQL-level scope so the per-stem series cannot
        contain rows past the requested anchor even if the upstream
        guard mis-fires). When ``None`` (default), behaviour is
        unchanged — the fetcher's only ``trade_date`` filter is the
        inclusive lower bound. Read-only; pure SELECT.

    Returns
    -------
    pd.DataFrame
        Long-format DataFrame; empty if no rows match. Columns are
        ``['trade_date', 'curve_family', 'contract_code', 'tenor',
        'field_value']`` so callers groupby
        ``['curve_family', 'contract_code']`` to materialise per-stem
        series. ``tenor`` is included so the scanner can surface it
        on the output row without a second ``fetch_rolling_generic_
        reference`` round-trip per stem.
    """
    if not curve_families:
        raise ValueError(
            "fetch_rolling_generic_universe_series requires at least "
            "one curve family."
        )
    with engine.connect() as conn:
        result = conn.execute(
            _FETCH_ROLLING_GENERIC_UNIVERSE_SERIES_SQL,
            {
                "curve_families": list(curve_families),
                "field_name": field_name,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat() if end_date is not None else None,
            },
        )
        rows = result.fetchall()
        columns = list(result.keys())
    return pd.DataFrame(rows, columns=columns)


def fetch_rolling_generic_universe_max_date(
    engine: Engine,
    curve_families: Sequence[str],
) -> Optional[date]:
    """Return the maximum ``trade_date`` available across the rolling-
    generic bond-futures universe for the named curve families.

    Cheap single-aggregate probe (``SELECT MAX(d.trade_date) ...``)
    that mirrors ``fetch_rolling_generic_universe_series``'s join /
    filter shape exactly (same direct join through ``instrument_
    master``, same ``is_rolling_contract = TRUE`` filter, same
    ``tenor IS NOT NULL`` guard). The two helpers must agree on
    "what is the universe's last trading day" — that is why this
    probe re-uses the series fetcher's WHERE clause rather than
    inventing its own.

    Used by the bond-futures scanner's future-anchor guard
    (``calculate_scan_bond_futures_extremes``): when the caller
    supplies an ``as_of_date`` beyond this max, the scanner returns
    the documented controlled-error envelope instead of silently
    delivering a normal scan computed only on the actually-available
    rows. Read-only; pure SELECT.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine.
    curve_families : Sequence[str]
        Bond-futures curve families to probe.

    Returns
    -------
    Optional[date]
        The maximum ``trade_date`` observed across the named
        universe, or ``None`` if no rows match (empty universe;
        ingestion has not yet landed).
    """
    if not curve_families:
        raise ValueError(
            "fetch_rolling_generic_universe_max_date requires at "
            "least one curve family."
        )
    with engine.connect() as conn:
        row = conn.execute(
            _FETCH_ROLLING_GENERIC_UNIVERSE_MAX_DATE_SQL,
            {"curve_families": list(curve_families)},
        ).first()
    if row is None or row[0] is None:
        return None
    value = row[0]
    # SQLAlchemy returns ``date`` for ``DATE`` columns on the standard
    # psycopg drivers; defensive isoformat-parse handles any driver
    # that hands back a string instead.
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))
