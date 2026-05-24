"""
fx_fetch.py — Database helpers for FX analytics
================================================

Thin SQL wrappers used by FX domain tools (spot, forwards, vol, NDFs,
and any future FX sub-domain). Mirrors the discipline of
``shared/analytics/rates_fetch.py``: finance-aware (knows the
``instrument_master`` / ``market_data_daily`` schema and the FX-specific
metadata layout under ``instrument_master.attributes``) but agnostic to
FX sub-family — the caller supplies the ``market_scope`` filter
(G10 / EM / G10_CROSSES / ALL) and the field name (default PX_LAST), and
this module returns a long-format DataFrame ready to be pivoted into a
wide Panel.

Query shapes
------------
V1 ships one canonical fetch shape for the Phase B ``calculate_fx_panel``
primitive:

  - whole spot universe filtered by market_scope → fetch_fx_spot_panel

Additional shapes (forwards by tenor, NDF by pair, vol-surface by smile
point) land in future PRs as Phase C/D/E primitives need them. Each
new fetch shape gets its own focused function here — same discipline
as ``rates_fetch.py``.

Asset-aware loader, not an operator
-----------------------------------
Per the project's architectural discipline (see ``fx_agent/ROADMAP.md``
§"Architectural disciplines" — Primitives vs operators):
``shared/analytics/`` hosts **asset-aware** loaders. The pair-keyed
``market_scope`` filter here is FX-specific and that is expected. It
parallels ``rates_fetch.py``'s curve_family/tenor vocabulary on the
rates side.

All functions are parameterised (named SQL binds); no string
interpolation of user-supplied identifiers. Filters that target the
JSONB ``attributes`` column use the ``->>'key'`` operator so the index
on ``(instrument_type, attributes)`` (if any) is still usable.
"""

from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine


# Closed-enum of accepted market_scope filters for spot FX, mapped to
# the precise fx_family values used in instrument_master.attributes.
# Why fx_family and not market_scope: the underlying tagging puts ALL
# G10-currency instruments under market_scope='G10' (including the 11
# G10 crosses), which makes market_scope='G10' ambiguous between
# "G10 majors only" and "G10 majors + crosses". Filtering by fx_family
# resolves this cleanly:
#   - "G10"          → 9  G10 majors (fx_family='G10_SPOT')
#   - "EM"           → 9  EM majors  (fx_family='EM_SPOT')
#   - "G10_CROSSES"  → 11 G10 crosses (fx_family='G10_CROSSES')
#   - "ALL"          → 29 all fx_spot instruments (no fx_family filter)
# The Pydantic Literal in fx_agent/spot/tools/fx_panel/schemas.py
# mirrors this same set so callers fail-loud at both boundaries.
_SCOPE_TO_FX_FAMILIES: Dict[str, Optional[Tuple[str, ...]]] = {
    "G10": ("G10_SPOT",),
    "EM": ("EM_SPOT",),
    "G10_CROSSES": ("G10_CROSSES",),
    "ALL": None,  # no fx_family filter
}
_VALID_SPOT_MARKET_SCOPES: frozenset[str] = frozenset(_SCOPE_TO_FX_FAMILIES.keys())


def fetch_fx_spot_panel(
    engine: Engine,
    market_scope: str,
    start_date: date,
    end_date: Optional[date] = None,
    field_name: str = "PX_LAST",
) -> Tuple[pd.DataFrame, List[Dict[str, str]]]:
    """Fetch the long-format FX spot panel for a market_scope subset.

    Returns a tuple ``(long_df, instrument_meta)`` where:
      * ``long_df`` has columns ``['trade_date', 'pair', 'field_value']``,
        one row per (instrument, date) observation, ordered by
        ``(pair, trade_date)``.  Empty DataFrame if no rows match.
      * ``instrument_meta`` is a list of per-pair dicts with the keys
        ``{'ticker', 'pair', 'base_ccy', 'quote_ccy', 'market_scope',
        'fx_family', 'region'}`` for downstream lineage / units /
        diagnostics. Order matches the alphabetical pair order in
        ``long_df``.

    The caller pivots ``long_df`` into a wide Panel (rows = dates,
    columns = pair) and constructs the typed ``Panel`` artifact with
    appropriate units (``TimeSeriesUnits.PRICE`` for spot levels).

    The function does NOT raise on empty results — that is the caller's
    decision (the calling primitive may want to convert an empty fetch
    into a domain-specific error). It DOES raise ``ValueError`` if
    ``market_scope`` is not in the closed set, and on negative date
    ranges.

    Parameters
    ----------
    engine
        SQLAlchemy engine for the macro_data Postgres / Timescale.
    market_scope
        One of {"G10", "EM", "G10_CROSSES", "ALL"}. Filters via
        ``instrument_master.attributes->>'market_scope'``. ``"ALL"``
        skips the scope filter (returns every fx_spot instrument).
    start_date
        Inclusive lower bound on ``trade_date``.
    end_date
        Inclusive upper bound on ``trade_date``. ``None`` ⇒ latest
        observation in DB.
    field_name
        Bloomberg field on ``market_data_daily``. Default ``PX_LAST``.

    Raises
    ------
    ValueError
        If ``market_scope`` is not in the closed set, or
        ``end_date < start_date``.
    """
    if market_scope not in _VALID_SPOT_MARKET_SCOPES:
        raise ValueError(
            f"market_scope={market_scope!r} not in closed set "
            f"{sorted(_VALID_SPOT_MARKET_SCOPES)}. Extend "
            "shared/analytics/fx_fetch.py and the Pydantic Literal "
            "in fx_panel/schemas.py together if adding a new scope."
        )
    if end_date is not None and end_date < start_date:
        raise ValueError(
            f"end_date={end_date} cannot be before start_date={start_date}."
        )

    # Build the scope predicate. "ALL" = no fx_family filter; the
    # other three map to a tuple of fx_family values (see
    # _SCOPE_TO_FX_FAMILIES). We always pin instrument_type='fx_spot'
    # so this fetcher is single-purpose and cannot accidentally pull
    # forwards / vol / NDF rows even if the universe grows.
    fx_families = _SCOPE_TO_FX_FAMILIES[market_scope]
    scope_predicate = ""
    if fx_families is not None:
        scope_predicate = " AND attributes->>'fx_family' = ANY(:fx_families)"

    sql_instruments = text(f"""
        SELECT
            instrument_id,
            vendor_ticker AS ticker,
            attributes->>'pair'         AS pair,
            attributes->>'base_ccy'     AS base_ccy,
            attributes->>'quote_ccy'    AS quote_ccy,
            attributes->>'market_scope' AS market_scope,
            attributes->>'fx_family'    AS fx_family,
            attributes->>'region'       AS region
        FROM macro_data.instrument_master
        WHERE instrument_type = 'fx_spot'
          {scope_predicate}
        ORDER BY attributes->>'pair'
    """)
    params: Dict[str, object] = {}
    if fx_families is not None:
        params["fx_families"] = list(fx_families)

    with engine.connect() as conn:
        rows = conn.execute(sql_instruments, params).mappings().all()

    if not rows:
        # No instruments at all in the requested scope.  Return empty
        # DataFrame + empty meta — the caller decides whether to raise.
        return pd.DataFrame(columns=["trade_date", "pair", "field_value"]), []

    instrument_ids = [int(r["instrument_id"]) for r in rows]
    pair_by_id = {int(r["instrument_id"]): str(r["pair"]) for r in rows}
    instrument_meta: List[Dict[str, str]] = [
        {
            "ticker": str(r["ticker"]),
            "pair": str(r["pair"]),
            "base_ccy": str(r["base_ccy"]) if r["base_ccy"] is not None else "",
            "quote_ccy": str(r["quote_ccy"]) if r["quote_ccy"] is not None else "",
            "market_scope": str(r["market_scope"]) if r["market_scope"] is not None else "",
            "fx_family": str(r["fx_family"]) if r["fx_family"] is not None else "",
            "region": str(r["region"]) if r["region"] is not None else "",
        }
        for r in rows
    ]

    # Fetch the time series in a single query bound to the
    # instrument_id list (avoids round-tripping per pair).
    end_predicate = " AND trade_date <= :end_date" if end_date is not None else ""
    sql_data = text(f"""
        SELECT
            d.trade_date,
            d.instrument_id,
            d.field_value
        FROM macro_data.market_data_daily d
        WHERE d.instrument_id = ANY(:instrument_ids)
          AND d.field_name    = :field_name
          AND d.trade_date   >= :start_date
          {end_predicate}
        ORDER BY d.instrument_id, d.trade_date
    """)
    data_params: Dict[str, object] = {
        "instrument_ids": instrument_ids,
        "field_name": field_name,
        "start_date": start_date.isoformat(),
    }
    if end_date is not None:
        data_params["end_date"] = end_date.isoformat()

    with engine.connect() as conn:
        result = conn.execute(sql_data, data_params)
        data_rows = result.fetchall()
        columns = list(result.keys())

    long_df = pd.DataFrame(data_rows, columns=columns)
    if long_df.empty:
        return pd.DataFrame(columns=["trade_date", "pair", "field_value"]), instrument_meta

    long_df["pair"] = long_df["instrument_id"].map(pair_by_id)
    long_df = long_df[["trade_date", "pair", "field_value"]]
    # Coerce types — trade_date as pd.Timestamp, field_value as float
    long_df["trade_date"] = pd.to_datetime(long_df["trade_date"])
    long_df["field_value"] = pd.to_numeric(long_df["field_value"], errors="coerce")

    return long_df.sort_values(["pair", "trade_date"]).reset_index(drop=True), instrument_meta


__all__ = [
    "fetch_fx_spot_panel",
]
