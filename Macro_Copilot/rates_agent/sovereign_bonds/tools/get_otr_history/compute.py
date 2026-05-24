"""
compute.py — Config-driven on-the-run (OTR) history primitive
==============================================================

Read-only monitor of ``macro_data.otr_history`` (the SCD2 substrate
ADR 0003 landed and ADR 0007's resolver populates).  For one
(country, tenor) sovereign cash-bond slot, returns:

  - ``current_metrics``: snapshot of which bond is currently OTR
    for the slot, plus the transition count over the displayed
    lookback window;
  - ``transitions``: chronological list of every otr_history row
    whose effective window intersects the lookback;
  - ``methodology_note``: surfaces TD #27's forward-only +
    detection-date limits at the user-facing layer (P5 + PR10).

This is a Bucket 1A primitive in the read-the-SCD2-table family.
Every methodology choice — default window length, window-boundary
semantics, sort order — flows from the bundled ``config.yaml``.

P12 (Bloomberg Accuracy Boundary)
---------------------------------
Pure INGEST primitive: the OTR mapping over time IS the data
ADR 0007's resolver writes; this primitive reads identifier rows
and recomputes nothing.  There is no Bloomberg-computed quantity to
match here — Bloomberg has no historical OTR-window screen.

P11 (honest refusal) — single-method primitive
----------------------------------------------
There is no ``method`` enum.  When no OTR window covers the slot in
the displayed window (e.g. the resolver has not yet run for the
slot, or the requested dates pre-date the resolver's first run),
the response is honest absence: ``current_metrics`` identity fields
are ``None`` and ``transitions`` is the empty list.  This is the
correct shape per P6 ("the absence is information, not failure")
and matches ``get_otr_at``'s ``Optional[Dict]`` contract.

Test seam
---------
Tests patch ``date`` at this module's namespace
(``...get_otr_history.compute.date``) — same pattern as
``yield_levels.compute.date``.  The DB engine is a SQLAlchemy
``Engine`` and tests pass a ``MagicMock`` whose ``.connect()``
context manager yields a connection whose ``.execute()`` returns a
``mappings()`` view.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

from rates_agent.sovereign_bonds.tools.get_otr_history.schemas import (
    OtrHistoryCurrentMetrics,
    OtrHistoryInput,
    OtrHistoryOutput,
    OtrHistoryTransitionRow,
)
from shared.config import ToolConfig, load_tool_config


# Bundled config — public symbol so external callers (mcp_server,
# REST routes, tests, parity-fixture capture) can build a ToolConfig
# from the same source the tool uses.
CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# Per PR14: ``window_boundary_semantics`` and ``transition_sort_order``
# are categorical conventions; their accepted values are wire-frozen
# in V1.  The methodology.planned_extensions block documents the
# path to widening these.
_SUPPORTED_BOUNDARY_SEMANTICS: str = "range_intersection"
_SUPPORTED_SORT_ORDER: str = "ascending"

# Per PR5: the methodology_note that surfaces TD #27 at the
# user-facing layer is conceptually a methodology disclosure, not
# free-form text.  It's a mathematical constant (string literal,
# unchanged across calls); keeping it here in code rather than in
# YAML matches PR7's "mathematical truths stay in code" — the YAML
# disclosure lives in methodology.assumptions + methodology.citations
# + methodology.planned_extensions; this constant is the runtime
# user-facing echo of those.
_METHODOLOGY_NOTE: str = (
    "Source: macro_data.otr_history (ADR 0003), populated by the "
    "on-the-run resolver per ADR 0007. Forward-only ingest: pre-"
    "resolver OTR windows are NOT reconstructed — queries that "
    "pre-date the resolver's first run return honest absence "
    "(transitions=[], current_metrics identity fields None), never "
    "a fabricated mapping. Detection-date precision: a window's "
    "effective_from is the resolver's first confirmed observation, "
    "~1-2 days after the true auction date at daily incremental "
    "cadence (TD #27). Bloomberg has no historical OTR-window "
    "screen; this primitive does not recompute what the source of "
    "record provides (P12)."
)


# ============================================================================
# SQL — read-only point-in-time + history queries
# ============================================================================

# Chronological SCD2 history for one slot.  Uses range-intersection
# boundary semantics: include every window whose effective range
# intersects the lookback.  ``effective_to IS NULL`` (open window) is
# treated as ``'infinity'::date`` for the intersection check, matching
# the table's EXCLUDE GIST constraint convention.
_OTR_HISTORY_SQL = text(
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


def _fetch_otr_transitions(
    engine: Engine,
    *,
    country: str,
    tenor: str,
    window_start: date,
    window_end: date,
) -> List[Dict[str, Any]]:
    """Run the SCD2 history query and return one dict per window.

    Private to this primitive's compute layer.  When a future primitive
    needs the same shape (e.g. ``otr_ofr_spread`` resolving the prior
    off-the-run bond), promote this to ``shared/analytics/`` and
    update both call sites in the same PR (P10 — one definition).

    Returns an empty list (never ``None``) when the slot has no
    resolver-observed windows that intersect the lookback — honest
    absence per P6.
    """
    with engine.connect() as conn:
        rows = (
            conn.execute(
                _OTR_HISTORY_SQL,
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
# CONVENTION VALIDATION
# ============================================================================

def _validate_conventions(config: ToolConfig) -> None:
    """Refuse loudly when a convention is set to a value V1 does not
    yet support (per PR14 + PR11 — categorical conventions whose set
    is wire-frozen until a follow-up PR widens them).

    Raises NotImplementedError naming the offending value and pointing
    at ``methodology.planned_extensions`` in ``config.yaml``.
    """
    boundary = config.convention_value("window_boundary_semantics")
    if boundary != _SUPPORTED_BOUNDARY_SEMANTICS:
        raise NotImplementedError(
            f"window_boundary_semantics={boundary!r} is documented in "
            f"this tool's config.yaml as a future-supported value "
            f"(see methodology.planned_extensions) but is not yet "
            f"implemented.  V1 supports only "
            f"{_SUPPORTED_BOUNDARY_SEMANTICS!r}.  Either restore the "
            f"value or implement the new branch in compute._fetch_otr_transitions."
        )
    sort_order = config.convention_value("transition_sort_order")
    if sort_order != _SUPPORTED_SORT_ORDER:
        raise NotImplementedError(
            f"transition_sort_order={sort_order!r} is documented in "
            f"this tool's config.yaml as a future-supported value "
            f"(see methodology.planned_extensions) but is not yet "
            f"implemented.  V1 supports only "
            f"{_SUPPORTED_SORT_ORDER!r}."
        )


# ============================================================================
# PUBLIC API
# ============================================================================

def get_otr_history(
    engine: Engine,
    params: OtrHistoryInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Return the OTR transition log + current snapshot for one
    (country, tenor) sovereign on-the-run slot.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine.
    params : OtrHistoryInput
        Validated input.  ``lookback_days`` is the single
        LLM-controlled central methodology knob (PR8); ``country``
        and ``tenor`` are the instrument selectors (PR1).
    config : ToolConfig, optional
        Bundled config.yaml is auto-loaded when None.  Tests pass a
        custom ToolConfig to exercise convention overrides.

    Returns
    -------
    dict
        Serialised ``OtrHistoryOutput``.  When no OTR window covers
        the slot in the lookback (honest absence per P6),
        ``current_metrics`` identity fields are ``None`` and
        ``transitions`` is the empty list.  Never an ``{"error": ...}``
        envelope — empty absence is a valid, well-defined result for
        this primitive.
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    _validate_conventions(config)

    # ------------------------------------------------------------------
    # 1. Window
    # ------------------------------------------------------------------
    today = date.today()
    window_start = today - timedelta(days=params.lookback_days)
    window_end = today

    # ------------------------------------------------------------------
    # 2. Fetch
    # ------------------------------------------------------------------
    rows = _fetch_otr_transitions(
        engine=engine,
        country=params.country,
        tenor=params.tenor,
        window_start=window_start,
        window_end=window_end,
    )

    # ------------------------------------------------------------------
    # 3. Map DB rows → typed transitions
    # ------------------------------------------------------------------
    transitions: List[OtrHistoryTransitionRow] = [
        OtrHistoryTransitionRow(
            effective_from=_iso(row["effective_from"]),
            effective_to=_iso_or_none(row.get("effective_to")),
            otr_instrument_id=int(row["otr_instrument_id"]),
            cusip=_str_or_none(row.get("cusip")),
            isin=_str_or_none(row.get("isin")),
            vendor_ticker=_str_or_none(row.get("vendor_ticker")),
            maturity_date=_iso_or_none(row.get("maturity_date")),
        )
        for row in rows
    ]

    # ------------------------------------------------------------------
    # 4. Snapshot — the currently-open window (effective_to IS NULL).
    # Multiple open windows for a slot would violate the EXCLUDE GIST
    # constraint at write time, so there is at most one.  Take the last
    # row in the ascending-sorted list that has effective_to is None;
    # falling through to "none open" honest-absence when there is none.
    # ------------------------------------------------------------------
    open_row: Optional[OtrHistoryTransitionRow] = next(
        (t for t in reversed(transitions) if t.effective_to is None),
        None,
    )

    current_metrics = OtrHistoryCurrentMetrics(
        country=params.country,
        tenor=params.tenor,
        as_of_date=today.isoformat(),
        otr_instrument_id=open_row.otr_instrument_id if open_row else None,
        cusip=open_row.cusip if open_row else None,
        isin=open_row.isin if open_row else None,
        vendor_ticker=open_row.vendor_ticker if open_row else None,
        maturity_date=open_row.maturity_date if open_row else None,
        current_effective_from=open_row.effective_from if open_row else None,
        transition_count_in_window=len(transitions),
        lookback_days=params.lookback_days,
    )

    output = OtrHistoryOutput(
        current_metrics=current_metrics,
        transitions=transitions,
        methodology_note=_METHODOLOGY_NOTE,
    )
    return output.model_dump()


# ============================================================================
# Small typed coercions — wire-format honesty (PR14)
# ============================================================================

def _iso(value: Any) -> str:
    """Coerce ``date`` / ``datetime`` / ``YYYY-MM-DD`` string to
    canonical ``YYYY-MM-DD``.  Required value; raises ``TypeError`` if
    the input is None (the SCD2 row's ``effective_from`` is
    ``NOT NULL`` in the schema, so this should never fire — included
    as defence in depth)."""
    if value is None:
        raise TypeError(
            "effective_from is NOT NULL in macro_data.otr_history "
            "(ADR 0003 schema) — None should never reach this coercion."
        )
    if hasattr(value, "isoformat"):
        iso = value.isoformat()
        return iso[:10]
    return str(value)[:10]


def _iso_or_none(value: Any) -> Optional[str]:
    """Like ``_iso`` but returns ``None`` for nullable columns
    (``effective_to``, ``maturity_date``)."""
    if value is None:
        return None
    return _iso(value)


def _str_or_none(value: Any) -> Optional[str]:
    """Coerce nullable identifier-string columns (CUSIP, ISIN,
    vendor_ticker) to ``Optional[str]``.  Empty strings are coerced
    to None — they reach the primitive only if instrument_master has
    a malformed row, and the right shape on the wire is honest
    absence."""
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None
