"""
rates_fetch.py — Database helpers for rates analytics
======================================================

Thin SQL wrappers used by rates domain tools (sovereign_bonds, ois,
policy_futures, bond_futures, and any future sub-domain). Most helpers
return long-format DataFrames that callers pivot / align as needed.

Helper paths
------------
This module exposes **three distinct read paths**, and they are not
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
     - ``fetch_scan_universe_strip_position``  (policy futures universe
       scan, strip-keyed analogue of ``fetch_scan_universe``; one field
       across every (curve_family, strip_position) stem)
     - ``fetch_scan_universe_reference``  (per-(curve_family, tenor)
       reference metadata — maturity_date / country / vendor_ticker
       — joined sibling of ``fetch_scan_universe`` for scanners that
       need per-row instrument context alongside the market data)
     - ``fetch_strip_position``           (policy futures, strip-keyed)
     - ``fetch_strip_position_max_date``  (policy futures, strip-keyed
       MAX(trade_date) probe used as the future-anchor guard)
     - ``fetch_scan_universe_strip_position_max_date``  (policy futures
       universe MAX(trade_date) probe used as the future-anchor guard
       for the universe scan; universe-wide analogue of
       ``fetch_strip_position_max_date``)
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
   rationale, and ``ADR 0013`` for the bond-futures V1 scope.

3. **Strip-position reference helpers** — do **NOT** go through the
   enriched view either. They query ``instrument_master`` with a
   LATERAL JOIN to ``instrument_metadata_history`` (SCD2,
   ``as_of_date``-bounded) to surface per-contract disclosure metadata
   (security_name, expiry_date, contract_size, tick_size, tick_value)
   and the ``inverse_pricing`` flag from
   ``instrument_master.attributes`` for one strip position:

     - ``fetch_strip_position_reference``
     - ``fetch_scan_universe_policy_future_reference``  (per-(curve_family,
       strip_position) reference metadata + ``inverse_pricing`` flag,
       universe-wide analogue of ``fetch_strip_position_reference``;
       LATERAL SCD2 join on ``instrument_metadata_history`` against
       ``instrument_master``)

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
  - per-instrument reference metadata
    across a universe scan                → fetch_scan_universe_reference
  - entire universe of one type,
    strip-keyed                           → fetch_scan_universe_strip_position
  - per-(curve_family, strip_position) universe
    reference metadata + inverse_pricing  → fetch_scan_universe_policy_future_reference
  - entire universe of one type, strip-keyed,
    MAX(trade_date) probe                 → fetch_scan_universe_strip_position_max_date
  - one curve, one strip position         → fetch_strip_position
  - one curve, one strip position,
    MAX(trade_date) probe                 → fetch_strip_position_max_date
  - one curve, one strip position,
    SCD2-bounded reference metadata       → fetch_strip_position_reference
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

from datetime import date, timedelta
from typing import Dict, Iterable, Optional, Sequence

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine


# ============================================================================
# DATA-RELATIVE AS-OF ANCHOR
# ============================================================================
#
# A tool that wants "the most recent N days" must anchor its fetch window to
# the LATEST trade_date actually present for the series, NOT ``date.today()``.
# ``date.today()`` silently produces an EMPTY window whenever the data lags
# "now" — a weekend, a holiday, an ingestion gap, or a stale snapshot — and,
# because the answer then changes with the wall clock, it also breaks replay
# determinism (P4: "reopen a six-month-old workspace, see the same numbers").
# ``latest_trade_date`` is the data-relative anchor: a cheap MAX(trade_date)
# over the SAME enriched view the ``fetch_*`` helpers read, with the SAME
# optional filters, so the resolved anchor is exactly the latest date the
# subsequent fetch would return.  Callers anchor with:
#
#     anchor = latest_trade_date(engine, curve_family=cf, field_name=fld) \
#              or date.today()
#     start_date = anchor - timedelta(days=N)
#
# The ``or date.today()`` fallback preserves the historical behaviour on a
# genuinely empty universe (no rows at all), so the change is a strict
# robustness improvement: identical output when data is current (anchor ==
# today's data), graceful degradation when it is not.
#
# CHUNK-PRUNING FLOOR (critical):  ``market_data_daily`` is a TimescaleDB
# hypertable (~1100 chunks).  An UNBOUNDED ``MAX(trade_date)`` takes an
# AccessShareLock on EVERY chunk; under the Monitor's concurrent widget load
# that exhausts the shared lock table ("out of shared memory /
# max_locks_per_transaction").  So the probe ALWAYS bounds ``trade_date`` to a
# recent window, letting the planner prune to a handful of chunks — the same
# order the actual fetch queries (<= ~2y windows) already touch safely.  The
# window must stay CHEAPER than a normal fetch (a 365d fetch touches ~46
# chunks; the lock budget is max_connections(25) x max_locks_per_transaction
# (128) ~= 3200 shared slots).  200d touches ~22 chunks — under a fetch — so the
# probe never dominates the lock budget even at full concurrency, while still
# covering ~6.5 months of staleness (far beyond any operational ingestion lag,
# and well past the current dev-snapshot gap).  If data is older than this the
# probe returns None and the caller falls back to date.today(); the long-window
# tools still find the data via their own fetch window, and only the very
# short-window tools degrade — the correct, bounded trade-off.
_LATEST_PROBE_WINDOW_DAYS = 200


def latest_trade_date(
    engine: Engine,
    *,
    curve_family: Optional[str] = None,
    tenor: Optional[str] = None,
    field_name: Optional[str] = None,
    instrument_type: Optional[str] = None,
) -> Optional[date]:
    """Most recent ``trade_date`` available for the given filter, or ``None``
    if no rows match.

    Cheap single-aggregate probe over
    ``macro_data.v_market_data_daily_enriched`` mirroring the
    ``fetch_single_tenor`` / ``fetch_tenor_group`` / ``fetch_scan_universe``
    filter shape, so the resolved anchor is exactly the latest date those
    fetchers would surface for the same filter.  All filters are optional and
    AND-combined; pass the same ``curve_family`` / ``field_name`` / ``tenor`` /
    ``instrument_type`` the subsequent fetch will use.  Returns ``None`` (not an
    error) when the filtered universe is empty, so callers fall back to
    ``date.today()``.

    Returns ``None`` immediately when ``engine`` is falsy (e.g. ``None``).
    Compute-layer unit tests exercise the tools with ``engine=None`` and a
    monkeypatched ``fetch_*`` returning fixture data; this probe must stay
    transparent to that pattern (no usable engine → no probe → caller falls
    back to ``date.today()``, the historical behaviour).  Production always
    passes a live engine.
    """
    if engine is None:
        return None
    clauses = []
    params: Dict[str, object] = {}
    if curve_family is not None:
        clauses.append("curve_family = :curve_family")
        params["curve_family"] = curve_family
    if tenor is not None:
        clauses.append("tenor = :tenor")
        params["tenor"] = tenor
    if field_name is not None:
        clauses.append("field_name = :field_name")
        params["field_name"] = field_name
    if instrument_type is not None:
        clauses.append("instrument_type = :instrument_type")
        params["instrument_type"] = instrument_type
    # ALWAYS bound trade_date so the hypertable prunes chunks (see the
    # CHUNK-PRUNING FLOOR note above).  Without this the unbounded MAX locks
    # every chunk and exhausts the shared lock table under concurrent load.
    clauses.append("trade_date >= :_probe_floor")
    params["_probe_floor"] = (
        date.today() - timedelta(days=_LATEST_PROBE_WINDOW_DAYS)
    ).isoformat()
    sql = text(
        "SELECT MAX(trade_date) AS max_trade_date "
        "FROM macro_data.v_market_data_daily_enriched WHERE "
        + " AND ".join(clauses)
    )
    with engine.connect() as conn:
        value = conn.execute(sql, params).scalar()
    if value is None:
        return None
    # psycopg returns a ``date`` already (DATE column); datetime/Timestamp are
    # ``date`` subclasses, so this also catches them.
    if isinstance(value, date):
        return value
    # A non-date scalar only arises under a mocked engine in unit tests
    # (production always yields a python ``date`` or ``None``); treat it as
    # "no probe" so the caller falls back to ``date.today()``.
    try:
        return pd.Timestamp(value).date()
    except (TypeError, ValueError):
        return None


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
# PER-INSTRUMENT REFERENCE METADATA ALONGSIDE A UNIVERSE SCAN
# ============================================================================
#
# ``fetch_scan_universe`` returns only the five market-data columns
# every scanner needs (trade_date / curve_family / tenor /
# contract_code / field_value). Some scanners also want per-row
# instrument context — maturity_date, country, vendor_ticker — so
# the desk reading the output does not need a second tool call to
# identify which bond ranked extreme. The enriched view
# (``macro_data.v_market_data_daily_enriched``) already exposes
# those columns natively; this helper returns them per-(curve_family,
# tenor, contract_code) tuple within a universe scan.
#
# Why a separate helper rather than widen ``fetch_scan_universe``:
# the existing scanners (sovereign / OIS) ship a wire shape that does
# NOT carry these columns — widening the fetcher would either force
# every scanner to dedupe the extra columns or change every scanner's
# row-shape downstream. A separate helper keeps the existing scanners
# unchanged and lets the new linker scanner (and any future scanner
# that wants per-row context) attach reference columns via a single
# extra DB round-trip — one query, deduped by ``(curve_family, tenor,
# contract_code)``.
#
# Read-only; pure SELECT.

_FETCH_SCAN_UNIVERSE_REFERENCE_ALL_SQL = text("""
    SELECT DISTINCT
        curve_family,
        tenor,
        contract_code,
        maturity_date,
        country,
        vendor_ticker,
        underlying_index
    FROM macro_data.v_market_data_daily_enriched
    WHERE instrument_type = :instrument_type
      AND tenor          IS NOT NULL
""")

_FETCH_SCAN_UNIVERSE_REFERENCE_FILTERED_SQL = text("""
    SELECT DISTINCT
        curve_family,
        tenor,
        contract_code,
        maturity_date,
        country,
        vendor_ticker,
        underlying_index
    FROM macro_data.v_market_data_daily_enriched
    WHERE instrument_type = :instrument_type
      AND tenor          IS NOT NULL
      AND curve_family    = ANY(:curve_families)
""")


def fetch_scan_universe_reference(
    engine: Engine,
    instrument_type: str,
    curve_families: Optional[Iterable[str]] = None,
) -> pd.DataFrame:
    """Fetch per-instrument reference metadata for a universe scan.

    Returns one row per ``(curve_family, tenor, contract_code)`` tuple
    in the scan's universe, with the per-instrument reference columns
    the enriched view exposes natively:

      - ``maturity_date`` — instrument maturity (date; may be NULL for
        instruments without a fixed maturity).
      - ``country`` — country identifier (e.g. ``'US'``, ``'UK'``,
        ``'France'``, ``'Canada'``).
      - ``vendor_ticker`` — Bloomberg-grade desk identifier
        (e.g. ``'GTII10 Govt'`` for the US 10Y TIPS generic).

    Parameters
    ----------
    instrument_type : str
        Enriched-view instrument type — e.g. ``'inflation_linker'``
        for the linker scanner. Same closed-family field
        ``fetch_scan_universe`` filters on.
    curve_families : Optional[Iterable[str]]
        If None, return reference rows for every curve_family of the
        given instrument_type. If provided, scope to the named
        curves only — mirrors ``fetch_scan_universe``'s scope kwarg.

    Returns
    -------
    pd.DataFrame with columns
    ``['curve_family', 'tenor', 'contract_code', 'maturity_date',
       'country', 'vendor_ticker', 'underlying_index']``.

    DISTINCT collapses repeated rows in the enriched view to one row
    per instrument. The view's underlying join (instrument_master →
    market_data_daily) already returns one row per (instrument_id,
    trade_date, field_name), so a stable per-instrument view of the
    reference columns is preserved by selecting only the columns that
    are stable per instrument and applying DISTINCT.

    ``underlying_index`` was added to the projection by the ZCIS
    universe scan (catalog id
    ``inflation_swaps__scan_inflation_swaps_extremes``, build_order
    25) so the desk reader can see which inflation index a ZCIS row
    references (``CPURNSA Index`` for USD_ZCIS, ``CPTFEMU Index``
    for EUR_ZCIS, ``UKRPI Index`` for GBP_ZCIS).  The column is
    additive: existing call sites (the linker scanner) read only
    the columns they need, so the extension is non-breaking.

    Notes on missing fields
    -----------------------
    ``security_name`` is intentionally NOT returned by this helper:
    both the linker universe and the ZCIS universe currently have
    ``instrument_metadata_history.security_name`` universally NULL
    on the live DB snapshot, so returning it here would be a column-
    of-Nones. Consumers wanting a security identifier should use
    ``vendor_ticker`` (a Bloomberg-grade identifier that IS
    populated for both linkers and ZCIS).
    """
    bind_params: dict = {"instrument_type": instrument_type}
    if curve_families:
        bind_params["curve_families"] = list(curve_families)
        sql = _FETCH_SCAN_UNIVERSE_REFERENCE_FILTERED_SQL
    else:
        sql = _FETCH_SCAN_UNIVERSE_REFERENCE_ALL_SQL

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
      AND (CAST(:end_date AS DATE) IS NULL OR trade_date <= CAST(:end_date AS DATE))
    ORDER BY trade_date
""")


def fetch_strip_position(
    engine: Engine,
    curve_family: str,
    strip_position: int,
    field_name: str,
    start_date: date,
    end_date: Optional[date] = None,
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

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine.
    curve_family : str
        Policy-futures curve family (SOFR_FUT / EUR_SHORT_RATE_FUT /
        SONIA_FUT).
    strip_position : int
        1-based strip position (1 = front contract; 2..N = quarterly
        forwards).
    field_name : str
        Bloomberg observation field mnemonic — ``'PX_LAST'`` for price,
        ``'OPEN_INT'`` for open interest, ``'PX_VOLUME'`` for volume.
    start_date : date
        Inclusive lower bound on ``trade_date``.
    end_date : date, optional
        Inclusive upper bound on ``trade_date``. When supplied, the SQL
        predicate adds ``AND trade_date <= :end_date`` so the result is
        anchored at a specific DB-as-of for deterministic Layer-B
        validation and the policy-futures monitors' future-anchor guard
        (an LLM-supplied ``as_of_date`` beyond the universe's last
        ingested ``trade_date`` is rejected upstream; this upper bound
        is the belt-and-braces SQL-level scope so the per-strip series
        cannot contain rows past the requested anchor even if the
        upstream guard mis-fires). When ``None`` (default), behaviour is
        unchanged — the fetcher's only ``trade_date`` filter is the
        inclusive lower bound. Read-only; pure SELECT.

    Examples
    --------
    - ``futures_price_level`` for SFR1 (front SOFR):
      ``field_name='PX_LAST'``, ``curve_family='SOFR_FUT'``,
      ``strip_position=1``.
    - ``volume_open_interest_snapshot`` for SFR2:
      ``field_name='OPEN_INT'``, ``strip_position=2``.
    """
    with engine.connect() as conn:
        result = conn.execute(
            _FETCH_STRIP_POSITION_SQL,
            {
                "curve_family": curve_family,
                "strip_position": strip_position,
                "field_name": field_name,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat() if end_date is not None else None,
            },
        )
        rows = result.fetchall()
        columns = list(result.keys())
    return pd.DataFrame(rows, columns=columns)


# Reference metadata snapshot for one policy-futures strip position:
# current-front contract_code, expiry_date, security_name, tick_size,
# tick_value, contract_size — plus the per-curve_family
# ``inverse_pricing`` flag (from ``instrument_master.attributes`` JSONB)
# that drives the implied-rate conversion in the price-level monitor.
#
# The current-front contract is the SCD2 ``instrument_metadata_history``
# row whose effective window contains the supplied ``as_of_date`` — i.e.
# ``effective_from <= as_of_date AND (effective_to IS NULL OR
# effective_to > as_of_date)``. This is different from the bond_futures
# rolling-generic reference helper's ``ORDER BY effective_from DESC
# LIMIT 1`` shape: the policy-futures SCD2 history is pre-populated with
# every future quarterly contract (e.g. SFR1's history runs out to
# 2035), so a naive "latest effective_from" lookup returns the FAR-end
# row (SFRU35) instead of the actual current front contract. The
# as_of-date-bounded window predicate is the only correct shape for the
# policy-futures domain.
#
# The ``inverse_pricing`` flag lives on
# ``instrument_master.attributes->>'inverse_pricing'`` — set by the
# policy_futures playbook at ingestion. The fetcher surfaces it on the
# reference dict so the monitor's compute path drives the regime choice
# off metadata (P8 — methodology in metadata, not in code).
_FETCH_STRIP_POSITION_REFERENCE_SQL = text("""
    SELECT
        i.curve_family       AS curve_family,
        i.contract_code      AS contract_code,
        (i.attributes->>'strip_position')::int AS strip_position,
        (i.attributes->>'inverse_pricing')::boolean AS inverse_pricing,
        COALESCE(h.contract_code, i.contract_code)   AS underlying_contract_code,
        COALESCE(h.expiry_date, i.expiry_date)       AS expiry_date,
        h.security_name      AS security_name,
        h.tick_size          AS tick_size,
        h.tick_value         AS tick_value,
        h.contract_size      AS contract_size
    FROM macro_data.instrument_master i
    LEFT JOIN LATERAL (
        SELECT
            contract_code, expiry_date, security_name,
            tick_size, tick_value, contract_size
        FROM macro_data.instrument_metadata_history
        WHERE instrument_id = i.instrument_id
          AND effective_from <= CAST(:as_of_date AS DATE)
          AND (effective_to IS NULL OR effective_to > CAST(:as_of_date AS DATE))
        ORDER BY effective_from DESC
        LIMIT 1
    ) h ON TRUE
    WHERE i.curve_family   = :curve_family
      AND (i.attributes->>'strip_position')::int = :strip_position
      AND i.is_rolling_contract = TRUE
    LIMIT 1
""")


def fetch_strip_position_reference(
    engine: Engine,
    curve_family: str,
    strip_position: int,
    as_of_date: date,
) -> Optional[Dict[str, object]]:
    """Fetch the reference metadata for one policy-futures strip
    position as of a specific trading day.

    Returns ``None`` when the ``(curve_family, strip_position)`` pair is
    not present on ``instrument_master`` as a strip-position-keyed
    rolling contract. Returns a dict with keys ``curve_family``,
    ``contract_code`` (the master stem, e.g. ``'SFR1'``),
    ``strip_position``, ``inverse_pricing`` (bool, from
    ``instrument_master.attributes``), ``underlying_contract_code``
    (the current-front underlying contract, e.g. ``'SFRM26'``, from the
    SCD2 history bounded by ``as_of_date``), ``expiry_date`` (date or
    None), ``security_name`` (str or None), ``tick_size`` (float or
    None), ``tick_value`` (float or None), ``contract_size`` (float or
    None).

    Why an as_of-date-bounded SCD2 lookup (not "latest effective_from")
    -----------------------------------------------------------------
    The policy-futures SCD2 ``instrument_metadata_history`` is pre-
    populated with every quarterly contract in the rolling chain out to
    far-future expiry (e.g. SFR1's history runs out to 2035-09 / SFRU35).
    The bond_futures rolling-generic reference helper's ``ORDER BY
    effective_from DESC LIMIT 1`` shape returns the FAR-end row in this
    domain (SFRU35), not the actual current-front contract. The
    as_of-date-bounded window predicate (``effective_from <= as_of_date
    AND (effective_to IS NULL OR effective_to > as_of_date)``) is the
    only correct shape for policy-futures rolling chains where the SCD2
    history is dense with future windows. The same predicate also makes
    the lookup deterministic: a Layer-B SQL validator that passes the
    same as_of_date sees the same current-front contract the monitor
    sees.

    Used by the policy_futures monitor primitives so the wire payload
    carries the per-contract disclosure (security_name, expiry_date,
    contract_size, tick_size, tick_value) that P5 requires alongside
    the implied-rate level — without forcing a second tool call for
    metadata.
    """
    with engine.connect() as conn:
        row = conn.execute(
            _FETCH_STRIP_POSITION_REFERENCE_SQL,
            {
                "curve_family": curve_family,
                "strip_position": strip_position,
                "as_of_date": as_of_date.isoformat(),
            },
        ).mappings().first()
    if row is None:
        return None
    return dict(row)


_FETCH_STRIP_POSITION_MAX_DATE_SQL = text("""
    SELECT MAX(trade_date) AS max_trade_date
    FROM macro_data.v_market_data_daily_enriched
    WHERE curve_family = :curve_family
      AND (attributes->>'strip_position')::int = :strip_position
      AND field_name   = :field_name
""")


def fetch_strip_position_max_date(
    engine: Engine,
    curve_family: str,
    strip_position: int,
    field_name: str,
) -> Optional[date]:
    """Return the maximum ``trade_date`` available for one policy-
    futures strip position.

    Cheap single-aggregate probe that mirrors
    :func:`fetch_strip_position`'s filter shape (same enriched view,
    same JSONB strip_position extraction, same field_name filter), so
    the two helpers must agree on "what is this strip's last trading
    day". Used by the policy_futures monitor primitives' future-anchor
    guard: when an LLM-supplied ``as_of_date`` lies beyond this max,
    the monitor returns the documented controlled-error envelope
    instead of silently delivering a normal snapshot computed only on
    the actually-available rows. Read-only; pure SELECT.

    Returns ``None`` when the strip has no rows for the requested
    field (universe miss or ingestion gap).
    """
    with engine.connect() as conn:
        row = conn.execute(
            _FETCH_STRIP_POSITION_MAX_DATE_SQL,
            {
                "curve_family": curve_family,
                "strip_position": strip_position,
                "field_name": field_name,
            },
        ).first()
    if row is None or row[0] is None:
        return None
    value = row[0]
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


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
# TD#11; see also ADR 0013 §"Bond futures".

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
# bond-futures morning-extremes scanner (V1 monitor #3 per ADR 0013 —
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
    sovereign-bond futures families per ADR 0013, ``tenor IS NOT
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


# ============================================================================
# POLICY-FUTURES UNIVERSE SCAN — strip-position-keyed
# ============================================================================
#
# Universe-scan analogues of the per-strip / per-curve helpers above,
# scaled to N (curve_family, strip_position) stems in ONE query each.
# Used by the policy-futures morning-extremes scanner (catalog id
# ``policy_futures__scan_policy_futures_extremes``, build_order 29).
#
# Why a NEW pair of helpers rather than reuse ``fetch_scan_universe`` /
# ``fetch_scan_universe_reference``:
#
#   - The existing tenor-keyed ``fetch_scan_universe`` filters on
#     ``tenor IS NOT NULL``. ``instrument_type='policy_future'`` rows
#     carry ``tenor IS NULL`` on the enriched view; the strip-position
#     disambiguator lives in ``attributes->>'strip_position'`` JSONB.
#     Passing ``instrument_type='policy_future'`` to the tenor-keyed
#     helper returns zero rows. Extending the existing helper to also
#     handle the strip-position key would either complicate the
#     signature with a discriminator (loose-typed branching at a
#     boundary that should be tight per P3) or silently break the
#     existing sovereign / OIS / inflation callers that depend on the
#     ``tenor IS NOT NULL`` invariant.
#
#   - The existing ``fetch_scan_universe_reference`` projects
#     ``curve_family, tenor, contract_code, maturity_date, country,
#     vendor_ticker, underlying_index`` — none of the policy-futures-
#     specific columns (``strip_position``, ``inverse_pricing``,
#     ``security_name``, ``expiry_date``, ``contract_size``,
#     ``tick_size``, ``tick_value``, ``underlying_contract_code``). The
#     policy-futures monitor primitives source these per-leg via
#     ``fetch_strip_position_reference``; the universe scan needs them
#     per (curve_family, strip_position) in one round-trip.
#
# Both helpers are additive — they do not touch ``fetch_scan_universe``
# / ``fetch_scan_universe_reference`` / any rolling-generic /
# bond_futures / sovereign / OIS callsite. The bond_futures /
# inflation_linkers / inflation_swaps scanners continue to use the
# existing tenor-keyed helpers unchanged.
#
# Read-only; pure SELECT.

_FETCH_SCAN_UNIVERSE_STRIP_POSITION_ALL_SQL = text("""
    SELECT
        trade_date,
        curve_family,
        (attributes->>'strip_position')::int AS strip_position,
        contract_code,
        field_value
    FROM macro_data.v_market_data_daily_enriched
    WHERE instrument_type = :instrument_type
      AND field_name      = :field_name
      AND trade_date     >= :start_date
      AND (attributes->>'strip_position')::int IS NOT NULL
      AND (CAST(:end_date AS DATE) IS NULL OR trade_date <= CAST(:end_date AS DATE))
    ORDER BY curve_family, strip_position, trade_date
""")

_FETCH_SCAN_UNIVERSE_STRIP_POSITION_FILTERED_SQL = text("""
    SELECT
        trade_date,
        curve_family,
        (attributes->>'strip_position')::int AS strip_position,
        contract_code,
        field_value
    FROM macro_data.v_market_data_daily_enriched
    WHERE instrument_type = :instrument_type
      AND field_name      = :field_name
      AND trade_date     >= :start_date
      AND (attributes->>'strip_position')::int IS NOT NULL
      AND curve_family    = ANY(:curve_families)
      AND (CAST(:end_date AS DATE) IS NULL OR trade_date <= CAST(:end_date AS DATE))
    ORDER BY curve_family, strip_position, trade_date
""")


def fetch_scan_universe_strip_position(
    engine: Engine,
    instrument_type: str,
    field_name: str,
    start_date: date,
    curve_families: Optional[Iterable[str]] = None,
    end_date: Optional[date] = None,
) -> pd.DataFrame:
    """Fetch every (curve_family, strip_position) series of the given
    strip-position-keyed instrument type for use by scanner-style tools.

    Strip-position-keyed analogue of :func:`fetch_scan_universe`. Today
    the only ``instrument_type`` this helper is exercised against is
    ``'policy_future'`` (SOFR_FUT / EUR_SHORT_RATE_FUT / SONIA_FUT —
    24 stems × 8 strip positions × 3 curve families on the V1
    playbook); the signature is generic so a future strip-position-keyed
    instrument family (TIIE futures etc.) can reuse it.

    Returns a long-format DataFrame with columns
    ``['trade_date', 'curve_family', 'strip_position', 'contract_code',
       'field_value']``.

    ``strip_position`` is sourced via
    ``(attributes->>'strip_position')::int`` — exactly the same JSONB
    extraction the per-leg :func:`fetch_strip_position` and
    :func:`fetch_strip_group` helpers use, so the row-set this universe
    fetcher returns is byte-identical (per-row) with concatenating the
    per-leg fetcher's output across every (curve_family, strip_position)
    pair in the universe.

    ``contract_code`` is the enriched-view value, which for
    policy-futures resolves to the SCD2-history's per-window underlying
    contract code (e.g. ``'SFRH6 COMB'``) rather than the master stem
    (``'SFR1'``). Scanners that need the master stem should join the
    universe-series rows to
    :func:`fetch_scan_universe_policy_future_reference` output keyed on
    (curve_family, strip_position).

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine.
    instrument_type : str
        Enriched-view instrument type — for policy futures this is
        ``'policy_future'``. The closed-family invariant lives in each
        scanner's compute.py (a module-level constant, NOT in YAML —
        same discipline the inflation_swaps scanner uses).
    field_name : str
        Bloomberg observation field mnemonic — ``'PX_LAST'`` for
        price, ``'OPEN_INT'`` for open interest, ``'PX_VOLUME'`` for
        volume.
    start_date : date
        Inclusive lower bound on ``trade_date``.
    curve_families : Optional[Iterable[str]]
        If None, scan every curve_family of the given
        ``instrument_type`` (subject to the SQL's
        ``strip_position IS NOT NULL`` guard, which excludes the
        per-window underlying delivery contracts that share the same
        instrument_type). If provided, scope to the named curves only.
    end_date : date, optional
        Inclusive upper bound on ``trade_date``. When supplied, the SQL
        predicate adds ``AND trade_date <= :end_date`` so the result is
        anchored at a specific DB-as-of for deterministic Layer-B
        validation. Mirrors the same upper-bound contract the
        rolling-generic universe series fetcher uses. When None
        (default), behaviour is unchanged.
    """
    bind_params: dict = {
        "instrument_type": instrument_type,
        "field_name": field_name,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat() if end_date is not None else None,
    }
    if curve_families:
        bind_params["curve_families"] = list(curve_families)
        sql = _FETCH_SCAN_UNIVERSE_STRIP_POSITION_FILTERED_SQL
    else:
        sql = _FETCH_SCAN_UNIVERSE_STRIP_POSITION_ALL_SQL

    with engine.connect() as conn:
        result = conn.execute(sql, bind_params)
        rows = result.fetchall()
        columns = list(result.keys())
    return pd.DataFrame(rows, columns=columns)


# Per-(curve_family, strip_position) reference metadata for a
# policy-futures universe scan. Joins ``instrument_master`` directly to
# the SCD2 ``instrument_metadata_history`` (LATERAL, as_of-bounded) to
# surface per-stem disclosure metadata (security_name, expiry_date,
# contract_size, tick_size, tick_value, underlying_contract_code) AND
# the ``inverse_pricing`` flag from ``instrument_master.attributes``
# that drives the implied-rate conversion. One row per
# (curve_family, strip_position) — 24 rows for the V1 universe
# (8 strip positions × 3 curve families) — in ONE query.
#
# The as_of-bounded SCD2 window mirrors the per-leg
# ``fetch_strip_position_reference`` exactly so a Layer-B SQL validator
# that passes the same as_of_date sees the same current-front contract
# the scanner sees.

_FETCH_SCAN_UNIVERSE_POLICY_FUTURE_REFERENCE_ALL_SQL = text("""
    SELECT
        i.curve_family       AS curve_family,
        i.contract_code      AS contract_code,
        (i.attributes->>'strip_position')::int AS strip_position,
        (i.attributes->>'inverse_pricing')::boolean AS inverse_pricing,
        COALESCE(h.contract_code, i.contract_code)   AS underlying_contract_code,
        COALESCE(h.expiry_date, i.expiry_date)       AS expiry_date,
        h.security_name      AS security_name,
        h.tick_size          AS tick_size,
        h.tick_value         AS tick_value,
        h.contract_size      AS contract_size
    FROM macro_data.instrument_master i
    LEFT JOIN LATERAL (
        SELECT
            contract_code, expiry_date, security_name,
            tick_size, tick_value, contract_size
        FROM macro_data.instrument_metadata_history
        WHERE instrument_id = i.instrument_id
          AND effective_from <= CAST(:as_of_date AS DATE)
          AND (effective_to IS NULL OR effective_to > CAST(:as_of_date AS DATE))
        ORDER BY effective_from DESC
        LIMIT 1
    ) h ON TRUE
    WHERE i.is_rolling_contract = TRUE
      AND (i.attributes->>'strip_position')::int IS NOT NULL
    ORDER BY i.curve_family, (i.attributes->>'strip_position')::int
""")

_FETCH_SCAN_UNIVERSE_POLICY_FUTURE_REFERENCE_FILTERED_SQL = text("""
    SELECT
        i.curve_family       AS curve_family,
        i.contract_code      AS contract_code,
        (i.attributes->>'strip_position')::int AS strip_position,
        (i.attributes->>'inverse_pricing')::boolean AS inverse_pricing,
        COALESCE(h.contract_code, i.contract_code)   AS underlying_contract_code,
        COALESCE(h.expiry_date, i.expiry_date)       AS expiry_date,
        h.security_name      AS security_name,
        h.tick_size          AS tick_size,
        h.tick_value         AS tick_value,
        h.contract_size      AS contract_size
    FROM macro_data.instrument_master i
    LEFT JOIN LATERAL (
        SELECT
            contract_code, expiry_date, security_name,
            tick_size, tick_value, contract_size
        FROM macro_data.instrument_metadata_history
        WHERE instrument_id = i.instrument_id
          AND effective_from <= CAST(:as_of_date AS DATE)
          AND (effective_to IS NULL OR effective_to > CAST(:as_of_date AS DATE))
        ORDER BY effective_from DESC
        LIMIT 1
    ) h ON TRUE
    WHERE i.is_rolling_contract = TRUE
      AND (i.attributes->>'strip_position')::int IS NOT NULL
      AND i.curve_family = ANY(:curve_families)
    ORDER BY i.curve_family, (i.attributes->>'strip_position')::int
""")


def fetch_scan_universe_policy_future_reference(
    engine: Engine,
    as_of_date: date,
    curve_families: Optional[Iterable[str]] = None,
) -> pd.DataFrame:
    """Fetch per-(curve_family, strip_position) reference metadata for
    the policy-futures universe scan.

    Returns one row per ``(curve_family, strip_position)`` stem with the
    columns the universe scanner attaches to every output row:

      - ``curve_family``        — STIR curve family (``'SOFR_FUT'``,
        ``'EUR_SHORT_RATE_FUT'``, ``'SONIA_FUT'``).
      - ``contract_code``       — master rolling-generic stem
        (``'SFR1'``, ``'ER1'``, ``'SFI1'``, ...) from
        ``instrument_master``.
      - ``strip_position``      — 1-based strip slot.
      - ``inverse_pricing``     — bool flag from
        ``instrument_master.attributes->>'inverse_pricing'``; drives
        the implied-rate conversion (when true:
        ``implied_rate_pct = 100 - raw_price``).
      - ``underlying_contract_code`` — current-front underlying contract
        code from the SCD2 history bounded by ``as_of_date``.
      - ``expiry_date``         — current-front expiry from the SCD2
        history; date or None.
      - ``security_name``       — current-front security name from the
        SCD2 history; str or None.
      - ``tick_size`` / ``tick_value`` / ``contract_size`` — current-
        front contract economics; floats or None.

    Why an as_of-bounded SCD2 lookup
    --------------------------------
    The policy-futures SCD2 ``instrument_metadata_history`` is
    pre-populated with every quarterly contract in the rolling chain
    out to far-future expiry (e.g. SFR1's history runs out to
    2035-09 / SFRU35). A naive ``ORDER BY effective_from DESC LIMIT 1``
    returns the FAR-end row, not the actual current-front contract.
    The ``effective_from <= as_of_date AND (effective_to IS NULL OR
    effective_to > as_of_date)`` predicate is the only correct shape
    for policy-futures rolling chains — same predicate
    :func:`fetch_strip_position_reference` uses for the per-leg
    monitors.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine.
    as_of_date : date
        SCD2 anchor date. The scanner passes its resolved as_of_date
        (post-fetch data-max or the LLM-supplied anchor) so the
        disclosed ``underlying_contract_code`` / ``expiry_date`` /
        ``security_name`` are the values that were effective on the
        snapshot's anchor date.
    curve_families : Optional[Iterable[str]]
        If None, return reference rows for every strip-position-keyed
        rolling-contract on instrument_master. If provided, scope to
        the named curves only — mirrors
        :func:`fetch_scan_universe_strip_position`'s scope kwarg.

    Returns
    -------
    pd.DataFrame
        Long-format DataFrame; empty when no rows match. One row per
        (curve_family, strip_position) stem, ordered by curve_family
        ascending then strip_position ascending — deterministic for
        Layer-B SQL validation.
    """
    bind_params: dict = {"as_of_date": as_of_date.isoformat()}
    if curve_families:
        bind_params["curve_families"] = list(curve_families)
        sql = _FETCH_SCAN_UNIVERSE_POLICY_FUTURE_REFERENCE_FILTERED_SQL
    else:
        sql = _FETCH_SCAN_UNIVERSE_POLICY_FUTURE_REFERENCE_ALL_SQL

    with engine.connect() as conn:
        result = conn.execute(sql, bind_params)
        rows = result.fetchall()
        columns = list(result.keys())
    return pd.DataFrame(rows, columns=columns)


_FETCH_SCAN_UNIVERSE_STRIP_POSITION_MAX_DATE_ALL_SQL = text("""
    SELECT MAX(trade_date) AS max_trade_date
    FROM macro_data.v_market_data_daily_enriched
    WHERE instrument_type = :instrument_type
      AND (attributes->>'strip_position')::int IS NOT NULL
""")

_FETCH_SCAN_UNIVERSE_STRIP_POSITION_MAX_DATE_FILTERED_SQL = text("""
    SELECT MAX(trade_date) AS max_trade_date
    FROM macro_data.v_market_data_daily_enriched
    WHERE instrument_type = :instrument_type
      AND (attributes->>'strip_position')::int IS NOT NULL
      AND curve_family    = ANY(:curve_families)
""")


def fetch_scan_universe_strip_position_max_date(
    engine: Engine,
    instrument_type: str,
    curve_families: Optional[Iterable[str]] = None,
) -> Optional[date]:
    """Return the maximum ``trade_date`` available across the
    strip-position-keyed universe scan for the named instrument_type
    (and optionally the named curve_families).

    Cheap single-aggregate probe that mirrors
    :func:`fetch_scan_universe_strip_position`'s filter shape exactly
    (same enriched-view source, same ``strip_position IS NOT NULL``
    guard, same instrument_type / curve_families scoping). Used by the
    policy_futures morning-extremes scanner's future-anchor guard: when
    an LLM-supplied ``as_of_date`` lies beyond this max, the scanner
    returns the documented controlled-error envelope instead of
    silently delivering an unbounded ranking under a future-anchored
    label.

    Returns ``None`` when the universe has no rows (empty scope or
    ingestion gap).
    """
    bind_params: dict = {"instrument_type": instrument_type}
    if curve_families:
        bind_params["curve_families"] = list(curve_families)
        sql = _FETCH_SCAN_UNIVERSE_STRIP_POSITION_MAX_DATE_FILTERED_SQL
    else:
        sql = _FETCH_SCAN_UNIVERSE_STRIP_POSITION_MAX_DATE_ALL_SQL

    with engine.connect() as conn:
        row = conn.execute(sql, bind_params).first()
    if row is None or row[0] is None:
        return None
    value = row[0]
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))




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

# NOTE on bind syntax: SQLAlchemy's text() bind regex is
# ``(?<![:\w\x5c]):(\w+)(?!:)`` — the negative-lookahead ``(?!:)``
# excludes ``:name::cast`` patterns from bind recognition.  Using
# ``CAST(:window_start AS DATE)`` instead of ``:window_start::date``
# is required for the binds to be substituted; the ``::``-shortcut
# form is silently passed through to psycopg2 verbatim and PostgreSQL
# raises a syntax error.  See the SQLAlchemy source for
# ``TextClause._bind_params_regex`` if confirming.
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
        i_otr.cusip          AS otr_cusip,
        i_otr.isin           AS otr_isin,
        i_otr.vendor_ticker  AS otr_vendor_ticker,
        i_ofr.cusip          AS ofr_cusip,
        i_ofr.isin           AS ofr_isin,
        i_ofr.vendor_ticker  AS ofr_vendor_ticker,
        d_otr.field_value AS otr_yield,
        d_ofr.field_value AS ofr_yield
    FROM date_resolved dr
    JOIN macro_data.market_data_daily d_otr
      ON d_otr.instrument_id = dr.otr_instrument_id
     AND d_otr.field_name    = :field_name
     AND d_otr.trade_date BETWEEN
         GREATEST(dr.window_effective_from, CAST(:window_start AS DATE))
         AND LEAST(dr.window_effective_to, CAST(:window_end AS DATE))
    JOIN macro_data.instrument_master i_otr
      ON i_otr.instrument_id = dr.otr_instrument_id
    LEFT JOIN macro_data.instrument_master i_ofr
      ON i_ofr.instrument_id = dr.ofr_instrument_id
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
       'otr_cusip', 'otr_isin', 'otr_vendor_ticker',
       'ofr_cusip', 'ofr_isin', 'ofr_vendor_ticker',
       'otr_yield', 'ofr_yield']``.

    The CUSIP/ISIN/vendor_ticker columns come from
    ``macro_data.instrument_master``; OFR identity columns are
    ``None`` on the slot's first observed window (LAG is NULL — no
    prior bond exists in history) and on rows where the OFR bond's
    instrument_master row has NULL CUSIP/ISIN (non-US sovereigns
    typically carry ISIN only).

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


# ============================================================================
# WIRP — per-meeting implied-rate snapshot fetch
# ============================================================================
#
# Reads the four WIRP fields ingested per ADR 0009 for one central
# bank's synthetic per-meeting instruments:
#
#   - WIRP_IMPLIED_RATE  — post-meeting implied effective policy rate
#   - WIRP_MOVE_PROB     — signed probability of a single 25bp hike(+)/cut(-)
#   - WIRP_NUM_MOVES     — number of 25bp moves priced
#   - WIRP_RATE_CHANGE   — implied change in rate vs current effective
#                          (in NATIVE Bloomberg units — see ADR 0009 §1)
#
# The synthetic ``wirp_meeting`` instruments are keyed by
# ``vendor_ticker = 'WIRP:{central_bank}:{meeting_date}'`` and carry
# ``maturity_date`` = the meeting date plus the per-metric Bloomberg
# tickers in the ``attributes`` JSONB (per ADR 0009 §1).
#
# This fetcher returns the LATEST observation per (instrument, field)
# — i.e. a snapshot of the four WIRP fields per meeting at the
# latest available trade_date.  Used by the ``wirp_meeting_pricing``
# primitive in V1; a future "WIRP history per meeting" primitive
# would use a different fetcher that returns the full daily series
# rather than the latest snapshot.
#
# Bind-syntax discipline: ``CAST(:x AS DATE)`` rather than
# ``:x::date`` per the Codex P0 lesson from PR #186 (SQLAlchemy's
# bind regex excludes ``:name::cast`` patterns).

_WIRP_FIELDS: tuple = (
    "WIRP_IMPLIED_RATE",
    "WIRP_MOVE_PROB",
    "WIRP_NUM_MOVES",
    "WIRP_RATE_CHANGE",
)


_FETCH_WIRP_MEETING_SNAPSHOTS_SQL = text(
    """
    SELECT DISTINCT ON (md.instrument_id, md.field_name)
        i.instrument_id,
        i.vendor_ticker,
        i.maturity_date            AS meeting_date,
        i.attributes->>'central_bank'        AS central_bank,
        i.attributes->>'wirp_meeting_token'  AS meeting_token,
        i.attributes->>'wirp_ticker_fr'      AS bloomberg_ticker_fr,
        i.attributes->>'wirp_ticker_pr'      AS bloomberg_ticker_pr,
        i.attributes->>'wirp_ticker_nm'      AS bloomberg_ticker_nm,
        i.attributes->>'wirp_ticker_ch'      AS bloomberg_ticker_ch,
        md.field_name,
        md.field_value,
        md.trade_date              AS as_of_date
    FROM macro_data.instrument_master i
    JOIN macro_data.market_data_daily md
      ON md.instrument_id = i.instrument_id
    WHERE i.instrument_type = 'wirp_meeting'
      AND i.attributes->>'central_bank' = :central_bank
      AND md.field_name IN (
          'WIRP_IMPLIED_RATE', 'WIRP_MOVE_PROB',
          'WIRP_NUM_MOVES', 'WIRP_RATE_CHANGE'
      )
      AND i.maturity_date >= CAST(:earliest_meeting_date AS DATE)
      AND i.maturity_date <= CAST(:latest_meeting_date AS DATE)
    ORDER BY md.instrument_id, md.field_name, md.trade_date DESC
    """
)


def fetch_wirp_meeting_snapshots(
    engine: Engine,
    *,
    central_bank: str,
    earliest_meeting_date: date,
    latest_meeting_date: date,
) -> pd.DataFrame:
    """Fetch the LATEST snapshot of the four WIRP fields per meeting
    for one central bank, restricted to meetings whose
    ``maturity_date`` (= meeting date) falls in
    ``[earliest_meeting_date, latest_meeting_date]``.

    Returns a long-format DataFrame with columns
    ``['instrument_id', 'vendor_ticker', 'meeting_date',
      'central_bank', 'meeting_token',
      'bloomberg_ticker_fr', 'bloomberg_ticker_pr',
      'bloomberg_ticker_nm', 'bloomberg_ticker_ch',
      'field_name', 'field_value', 'as_of_date']``

    Each (instrument_id, field_name) pair contributes exactly one
    row — the latest ``trade_date`` for that pair.  A meeting with
    full 4/4 coverage contributes 4 rows; a meeting missing one
    field (e.g. region-opted-out via the ADR 0009 §4
    ``available: false`` shape) contributes <4 rows and the caller
    handles the absence honestly (None on the wire).

    Empty DataFrame when no meeting matches the filter — honest
    absence per P6 (pre-extractor-deployment shape per ADR 0008 §6
    / TD #28b, or a central_bank outside the four supported regions).

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine.
    central_bank : str
        One of the four supported values per ADR 0009 §1:
        ``'FOMC'``, ``'ECB'``, ``'BOE'``, ``'BOJ'``.
    earliest_meeting_date, latest_meeting_date : date
        Inclusive calendar boundaries on the meeting date (the
        synthetic instrument's ``maturity_date`` column, per
        ADR 0009 §1's typed-column mapping).

    Notes
    -----
    The shipped WIRP data is INGESTED per ADR 0009 verbatim from
    Bloomberg's WIRP screen.  Units are stored as Bloomberg returns
    them (P12 — the field name asserts a *quantity*, not a unit;
    see ADR 0009 §1's "rate_change is in NATIVE units" disclosure).
    Callers must NOT apply unit conversion — surfaces the values as
    fetched.
    """
    with engine.connect() as conn:
        result = conn.execute(
            _FETCH_WIRP_MEETING_SNAPSHOTS_SQL,
            {
                "central_bank": str(central_bank),
                "earliest_meeting_date": earliest_meeting_date.isoformat(),
                "latest_meeting_date": latest_meeting_date.isoformat(),
            },
        )
        rows = result.fetchall()
        columns = list(result.keys())
    return pd.DataFrame(rows, columns=columns)
