"""
events_fetch.py — Database helpers for event-calendar analytics
================================================================

Thin SQL wrappers used by event-derived primitives (cpi_surprise,
nfp_surprise, fomc_surprise_label, auction_tail) over the
``macro_data.event_calendar`` substrate landed by ADR 0004 and
populated by the event playbooks per ADR 0008.

These helpers stay narrow and event-shaped: each fetcher returns a
DataFrame of one ``event_type``'s rows filtered by ``country`` and a
calendar window.  The economic-release fetcher additionally surfaces
the typed surprise inputs (``actual`` / ``consensus_median`` /
``prior`` / ``surprise_std_dev`` / ``period`` / ``revised_prior``)
that the surprise primitives consume.

Query shapes
------------
Two canonical fetch shapes cover the event-derived primitives in
scope of the 8-primitive batch:

  - economic releases (CPI, NFP, retail sales, PMI, jobless claims)
    → ``fetch_economic_release_surprises``
  - central-bank meetings (FOMC / ECB / BoE / BoJ decisions)
    → ``fetch_central_bank_meetings`` (forthcoming — primitive 6)

Bind-syntax discipline
----------------------
SQLAlchemy's ``text()`` bind regex
``(?<![:\\w\\x5c]):(\\w+)(?!:)`` excludes ``:name::cast`` patterns,
so the production fetchers below use ``CAST(:x AS DATE)`` instead of
the PostgreSQL shorthand ``:x::date``.  See the regression note on
:func:`shared.analytics.rates_fetch.fetch_otr_ofr_yield_pair`.

P12 (Bloomberg Accuracy Boundary)
---------------------------------
``event_calendar.surprise`` is ingested NULL by the event extractor
per ADR 0008 §2 — surprise is the exact identity
``actual − consensus_median``, computed downstream by the surprise
primitives WITH DISCLOSURE, never baked in at ingestion.  These
fetchers therefore return ``actual`` and ``consensus_median`` as
typed columns and let the primitive compute the surprise.  This is
NOT a proxy or recomputation; it is the desk-recognised identity
ADR 0008 deliberately left for the primitive layer to surface.
"""

from __future__ import annotations

from datetime import date
from typing import List, Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine


# ============================================================================
# ECONOMIC-RELEASE SURPRISE FETCH
# ============================================================================
#
# Pulls every ``event_calendar`` row of one ``event_type`` for one
# ``country`` whose ``release_date`` falls in ``[window_start, window_end]``
# (inclusive on both ends), ordered by ``release_date`` ascending.
#
# Returns the typed surprise inputs the cpi_surprise / nfp_surprise
# primitives need: ``actual``, ``consensus_median``, ``consensus_high``,
# ``consensus_low``, ``prior``, ``revised_prior``, ``surprise_std_dev``,
# ``period``, plus the lineage / identity columns (``event_id``,
# ``release_date``, ``release_time``, ``currency``, ``event_category``).
#
# Scheduled-placeholder rows (``actual`` IS NULL — release date is in
# the future, or the print has not yet been ingested) ARE returned —
# the caller decides whether to skip them.  This is the honest
# absence shape (P5 + P6 — the absence is information, not failure).

_FETCH_EVENT_RELEASES_SQL = text(
    """
    SELECT
        event_id,
        event_type,
        event_category,
        country,
        currency,
        release_date,
        release_time,
        period,
        actual,
        consensus_median,
        consensus_high,
        consensus_low,
        prior,
        revised_prior,
        surprise_std_dev
    FROM macro_data.event_calendar
    WHERE event_type   = :event_type
      AND country      = :country
      AND release_date BETWEEN CAST(:window_start AS DATE)
                            AND CAST(:window_end AS DATE)
    ORDER BY release_date ASC, event_id ASC
    """
)


def fetch_economic_release_surprises(
    engine: Engine,
    *,
    event_type: str,
    country: str,
    window_start: date,
    window_end: date,
) -> pd.DataFrame:
    """Fetch the typed surprise-input columns for one (event_type,
    country) economic-release series over a calendar window.

    Returns a DataFrame with columns
    ``['event_id', 'event_type', 'event_category', 'country',
       'currency', 'release_date', 'release_time', 'period',
       'actual', 'consensus_median', 'consensus_high',
       'consensus_low', 'prior', 'revised_prior',
       'surprise_std_dev']`` sorted by ``release_date`` ascending.

    Empty DataFrame when no row for ``(event_type, country)`` falls
    in the window — honest absence per P6 (the pre-deployment shape
    documented in ADR 0008 §6 / TD #28b).  Callers decide whether
    that is an error envelope or a legitimate "no releases" result.

    Scheduled-placeholder rows (``actual`` is NULL) are returned;
    the caller decides whether to skip them.  Rows where Bloomberg
    publishes the print but no consensus survey exists (e.g. Japan
    composite PMI per ADR 0008 §2) carry ``consensus_median`` NULL
    and are honestly distinct from a "release hasn't happened yet"
    scheduled placeholder.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine.
    event_type : str
        ``event_calendar.event_type`` value — e.g. ``cpi_yoy``,
        ``hicp_yoy``, ``nfp``, ``retail_sales_mom``.  See
        ``rates_agent/playbooks/economic_releases.yml`` for the
        ingested set.
    country : str
        ``event_calendar.country`` value — uppercase
        ISO-3166-alpha-2/3 per the playbook convention (``US``,
        ``EU``, ``UK``, ``JP``).
    window_start, window_end : date
        Inclusive calendar boundaries on ``release_date``.

    Notes
    -----
    The natural key on ``event_calendar`` is
    ``(event_type, country, release_date)`` per ADR 0004 — at most
    one row per ``(event_type, country)`` per date — so the
    ascending sort produces a strictly monotonic time series per
    series even when ``release_date`` ties cross unrelated
    ``event_type`` values (which this query already filters out).
    """
    with engine.connect() as conn:
        result = conn.execute(
            _FETCH_EVENT_RELEASES_SQL,
            {
                "event_type": str(event_type),
                "country": str(country),
                "window_start": window_start.isoformat(),
                "window_end": window_end.isoformat(),
            },
        )
        rows = result.fetchall()
        columns = list(result.keys())
    return pd.DataFrame(rows, columns=columns)


__all__ = [
    "fetch_economic_release_surprises",
]
