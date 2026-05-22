"""
event_calendar_fetch.py — Database helpers for event-driven analytics
=====================================================================

Thin SQL wrappers over ``macro_data.event_calendar`` (the event substrate
introduced by ADR 0008 — event-playbook contract). Two canonical query
shapes cover the in-scope event primitives:

  - by ``event_type`` (+ optional ``country``) → series of economic-release
    rows (CPI, NFP, retail sales, PMI, claims, …).
  - by ``central_bank``                       → series of central-bank
    policy decisions (FOMC / ECB / BOE / BOJ / RBA / BOC — hike / hold /
    cut + statement classification + policy-rate level).

These cover the three event primitives currently catalogued:

  - ``cpi_surprise``        (event_type=``cpi_yoy``/``hicp_yoy``)
  - ``nfp_surprise``        (event_type=``nfp``)
  - ``fomc_surprise_label`` (central_bank=``FOMC``)

Out of scope (deliberately omitted):
  - ``auction_tail``           — D-auctions deferred per ``docs/technical_debt.md`` #28.
  - ``wirp_meeting_pricing``   — ADR 0009 still Proposed.

All functions are parameterised (named SQL binds); no string interpolation
of user-supplied identifiers. Returns long-format DataFrames; primitives
pivot / compute downstream.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine


# ============================================================================
# ECONOMIC RELEASES (one series of one economic release over time)
# ============================================================================

_FETCH_ECONOMIC_RELEASES_SQL = text("""
    SELECT
        release_date,
        release_time,
        period,
        country,
        actual,
        consensus_median,
        consensus_high,
        consensus_low,
        prior,
        revised_prior,
        surprise,
        surprise_std_dev
    FROM macro_data.event_calendar
    WHERE event_category = 'economic_release'
      AND event_type    = :event_type
      AND release_date >= :start_date
    ORDER BY release_date
""")


_FETCH_ECONOMIC_RELEASES_BY_COUNTRY_SQL = text("""
    SELECT
        release_date,
        release_time,
        period,
        country,
        actual,
        consensus_median,
        consensus_high,
        consensus_low,
        prior,
        revised_prior,
        surprise,
        surprise_std_dev
    FROM macro_data.event_calendar
    WHERE event_category = 'economic_release'
      AND event_type    = :event_type
      AND country       = :country
      AND release_date >= :start_date
    ORDER BY release_date
""")


def fetch_economic_releases(
    engine: Engine,
    event_type: str,
    start_date: date,
    country: Optional[str] = None,
) -> pd.DataFrame:
    """Fetch the historical series of one economic release.

    For event-study signals (CPI, NFP, retail sales, PMI, claims): returns
    the actual print + consensus + pre-computed surprise (when present),
    plus prior, revised_prior, and surprise_std_dev when available.

    The DB ingestion path (per ADR 0008) stores ``surprise`` as a typed
    column when the source provides it; downstream primitives may also
    recompute ``actual − consensus_median`` if the stored ``surprise``
    is NULL for a row. This helper returns both, leaving the choice to
    the primitive.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine.
    event_type : str
        The canonical event slug from ``event_calendar.event_type`` (e.g.
        ``cpi_yoy`` for US/UK/JP CPI, ``hicp_yoy`` for EU HICP, ``nfp``,
        ``retail_sales_mom``, ``ism_manufacturing``, …).
    start_date : date
        Inclusive lower bound on ``release_date``.
    country : Optional[str]
        Optional ISO country slug (``US`` / ``EU`` / ``UK`` / ``JP`` / …).
        When set, narrows to that country; otherwise returns every
        country's rows for the requested ``event_type``. Default ``None``.

    Returns
    -------
    pd.DataFrame with long-format columns
    ``['release_date', 'release_time', 'period', 'country', 'actual',
    'consensus_median', 'consensus_high', 'consensus_low', 'prior',
    'revised_prior', 'surprise', 'surprise_std_dev']``.

    Examples
    --------
    - ``cpi_surprise`` (US):   ``event_type='cpi_yoy', country='US'``.
    - ``cpi_surprise`` (HICP): ``event_type='hicp_yoy', country='EU'``.
    - ``nfp_surprise``:        ``event_type='nfp', country='US'``.
    """
    bind_params: dict = {
        "event_type": event_type,
        "start_date": start_date.isoformat(),
    }
    if country is not None:
        bind_params["country"] = country
        sql = _FETCH_ECONOMIC_RELEASES_BY_COUNTRY_SQL
    else:
        sql = _FETCH_ECONOMIC_RELEASES_SQL

    with engine.connect() as conn:
        result = conn.execute(sql, bind_params)
        rows = result.fetchall()
        columns = list(result.keys())
    return pd.DataFrame(rows, columns=columns)


# ============================================================================
# CENTRAL-BANK MEETINGS (one series of policy decisions over time)
# ============================================================================
#
# Decision type (hike / hold / cut), the policy-rate levels before and
# after, and the optional statement classification live in the
# ``attributes`` JSONB (per ADR 0008 — typed columns reserve the
# cross-category surprise model; per-decision structured fields ride
# the escape hatch). The helper extracts them as typed columns in the
# SELECT so downstream primitives do not parse JSONB themselves.

_FETCH_CENTRAL_BANK_MEETINGS_SQL = text("""
    SELECT
        release_date,
        release_time,
        period,
        central_bank,
        attributes->>'decision_type'                    AS decision_type,
        attributes->>'statement_classification'         AS statement_classification,
        (attributes->>'policy_rate_after')::numeric     AS policy_rate_after,
        (attributes->>'policy_rate_before')::numeric    AS policy_rate_before,
        related_instrument_id,
        attributes
    FROM macro_data.event_calendar
    WHERE event_category = 'central_bank_meeting'
      AND central_bank   = :central_bank
      AND release_date  >= :start_date
    ORDER BY release_date
""")


def fetch_central_bank_meetings(
    engine: Engine,
    central_bank: str,
    start_date: date,
) -> pd.DataFrame:
    """Fetch the historical series of one central bank's policy decisions.

    Returns the decision type (``hike`` / ``hold`` / ``cut``), the
    policy-rate level before and after (when recorded), and the optional
    statement classification — extracted from the ``attributes`` JSONB
    into typed SELECT columns so downstream primitives do not parse
    JSONB themselves. The raw ``attributes`` column is also returned
    for primitives that need additional event-specific fields.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine.
    central_bank : str
        The canonical CB slug (``FOMC``, ``ECB``, ``BOE``, ``BOJ``,
        ``RBA``, ``BOC``).
    start_date : date
        Inclusive lower bound on ``release_date``.

    Returns
    -------
    pd.DataFrame with columns
    ``['release_date', 'release_time', 'period', 'central_bank',
    'decision_type', 'statement_classification',
    'policy_rate_after', 'policy_rate_before',
    'related_instrument_id', 'attributes']``.

    Examples
    --------
    - ``fomc_surprise_label``: ``central_bank='FOMC'``.
    - ECB analogue:             ``central_bank='ECB'``.
    """
    with engine.connect() as conn:
        result = conn.execute(
            _FETCH_CENTRAL_BANK_MEETINGS_SQL,
            {
                "central_bank": central_bank,
                "start_date": start_date.isoformat(),
            },
        )
        rows = result.fetchall()
        columns = list(result.keys())
    return pd.DataFrame(rows, columns=columns)
