"""Pydantic schemas for the cpi_surprise tool.

Per-release CPI surprise series (``actual − consensus_median`` in
percentage points of year-over-year CPI) for one country, plus a
rolling z-score over a window of N releases.  Built on the event-
calendar substrate landed by ADR 0004 and populated by the
economic-releases playbook per ADR 0008.

Output shape
------------
Snapshot + canonical ``TimeSeries`` — the curve_spread / yield_levels
/ otr_ofr_spread archetype.  Two canonical TimeSeries fields:

  - ``time_series_surprise`` (units = PERCENT) — the historical
    surprise values (actual − consensus_median in pct points).
  - ``time_series_zscore`` (units = Z_SCORE) — the rolling z-score
    over the trailing ``release_z_window`` realised releases.

Both fields plus the bespoke ``time_series`` array are built from the
same display DataFrame and cannot drift — proven by the parity test.

The time-series INDEX is the release date (``YYYY-MM-DD`` string),
NOT trade_date — releases happen monthly so the index is sparse
relative to a daily series.  Each row also carries the reference
``period`` (e.g. "Mar 2026") for desk readability.

Validation layering
-------------------
- ``country`` is the LLM-facing instrument selector (PR1).  No
  Pydantic-layer closed enum — the supported set is YAML-locked via
  the ``cpi_event_type_for_<country>`` conventions (compute layer
  returns a controlled error envelope for an unsupported country).
  Validators canonicalise lowercase / whitespace input (P5/P6 —
  loud rejection over silent SQL-layer miss).
- ``lookback_releases`` is the central methodology knob (PR8) —
  number of releases (NOT calendar days) to display.  Pydantic
  default is sourced from ``config.yaml``'s
  ``default_lookback_releases`` convention via the lazy-factory
  pattern (P10 — single source of truth for the default).

PR14 — wire-format honesty
--------------------------
``release_z_window_releases: int`` is included in ``current_metrics``
to echo the z-score window used.  No output field name embeds the
window length (no ``z_score_24r``-style methodology-encoded names),
so no NotImplementedError guard is needed on the z-score window
convention.  The ``surprise_formula`` convention IS guarded with
NotImplementedError because the identity ``actual − consensus_median``
is wire-frozen in V1 (PR14 + PR11 categorical convention).
"""

from __future__ import annotations

import re
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from shared.schemas import TimeSeries


# Country pattern fixed by the ``country_canonicalisation`` convention
# (config.yaml + economic_releases.yml).  The playbook ingests
# uppercase ISO-3166-alpha-2 (US / UK / JP) plus the regional code
# EU.  No alpha-3 here (the eurozone has no alpha-3) — only the four
# 2-letter values + lowercase variants are tolerated.
_COUNTRY_PATTERN = re.compile(r"^[A-Z]{2}$")


def _bundled_default_lookback_releases() -> int:
    """Read the default lookback (number of releases) from the
    bundled ``config.yaml``.

    Looked up lazily inside the field-default factory so circular-
    import risk is zero (the schema doesn't import compute or
    ToolConfig at module-load time; only the factory path touches
    them).  Cached by the underlying ``load_tool_config`` so this is
    a one-time cost.

    Raises loudly when ``config.yaml`` cannot be loaded or the
    ``default_lookback_releases`` convention is missing — per P6 (no
    silent failure) and P10 (single source of truth).  Same lazy-
    lookup pattern as get_otr_history / otr_ofr_spread.
    """
    # Local imports to avoid a top-of-module cycle through compute.
    from rates_agent.inflation_swaps.tools.cpi_surprise.compute import (
        CONFIG_PATH,
    )
    from shared.config import load_tool_config

    cfg = load_tool_config(CONFIG_PATH)
    return int(cfg.convention_value("default_lookback_releases"))


class CpiSurpriseInput(BaseModel):
    """Parameters the LLM extracts to query a single country's
    CPI-surprise series."""

    country: str = Field(
        ...,
        description=(
            "Country / region code as stored in macro_data.event_calendar. "
            "Supported values (per YAML conventions "
            "cpi_event_type_for_<country>): 'US', 'UK', 'JP', 'EU'.  US/UK/JP "
            "resolve to event_type='cpi_yoy'; EU resolves to "
            "event_type='hicp_yoy' (the eurozone equivalent of CPI YoY)."
        ),
    )
    lookback_releases: int = Field(
        default_factory=_bundled_default_lookback_releases,
        ge=4,
        le=200,
        description=(
            "Number of realised releases of trailing history to display in "
            "the time_series output.  The bundled default is read from "
            "config.yaml's ``default_lookback_releases`` convention "
            "(currently 24 ≈ 2Y at monthly CPI cadence).  The rolling "
            "z-score window is fixed by config convention "
            "``release_z_window`` (24 releases) regardless of this value."
        ),
    )

    # =====================================================================
    # Canonicalisation invariants — code-owned per PR7 (invariants in
    # code, not YAML).  The convention the rule documents under is
    # ``country_canonicalisation`` in config.yaml.  The invariants
    # below ENFORCE that convention at the API boundary so mistyped
    # inputs ("us", " jp ", "USA") fail loudly rather than degrading
    # silently to honest absence at the SQL layer (which would mask
    # the user's typo as a real no-data result).
    # =====================================================================

    @field_validator("country", mode="before")
    @classmethod
    def _canonicalise_country(cls, v: object) -> str:
        """Uppercase 2-letter country / region code (US, UK, JP, EU);
        reject anything else loudly.

        Per the ``country_canonicalisation`` convention (config.yaml).
        Lowercase input is upcased rather than rejected because the
        alias is unambiguous and rejecting it would be hostile.
        Other shapes (alpha-3, numeric, longer codes) are rejected
        loudly here so the caller learns the supported set at the
        API boundary, not from an empty-result envelope at the SQL
        layer (P6 — no silent failure).
        """
        if not isinstance(v, str):
            raise ValueError(
                f"country must be a string (uppercase 2-letter ISO-3166 "
                f"alpha-2 or EU); got {type(v).__name__}"
            )
        s = v.strip().upper()
        if not _COUNTRY_PATTERN.match(s):
            raise ValueError(
                f"country={v!r} does not match the event-calendar "
                f"convention (uppercase 2-letter: 'US', 'UK', 'JP', "
                f"or the regional code 'EU').  See the "
                f"``country_canonicalisation`` convention in config.yaml "
                f"+ rates_agent/playbooks/economic_releases.yml."
            )
        return s


class CpiSurpriseCurrentMetrics(BaseModel):
    """Snapshot metrics for the most-recent realised release.

    All numeric fields are ``Optional`` to surface honest absence
    when a release exists but has no consensus_median (survey=false
    indicators per ADR 0008 §2 — does not apply to CPI today, but
    the wire shape allows it for forward-compatibility).
    """

    release_date: str = Field(
        ...,
        description="Most recent realised release date (YYYY-MM-DD).",
    )
    country: str
    event_type: str = Field(
        ...,
        description=(
            "Resolved event_type — ``cpi_yoy`` (US/UK/JP) or "
            "``hicp_yoy`` (EU) — from the country_to_event_type YAML "
            "mapping."
        ),
    )
    period: Optional[str] = Field(
        None,
        description=(
            "Reference period of the release (e.g. 'Mar 2026') from "
            "event_calendar.period.  May be None on placeholder rows."
        ),
    )
    surprise_label: str = Field(
        ...,
        description=(
            "Human-readable label — e.g. 'US CPI YoY surprise'."
        ),
    )

    current_surprise_pct: Optional[float] = Field(
        None,
        description=(
            "Surprise (actual − consensus_median) at the latest "
            "realised release, in percentage points of YoY CPI.  "
            "None when consensus_median is NULL on the latest "
            "realised row (honest absence — Bloomberg has no "
            "survey for that release)."
        ),
    )
    current_z_score: Optional[float] = Field(
        None,
        description=(
            "Rolling z-score of the latest surprise vs the trailing "
            "release_z_window window.  None during warmup (fewer "
            "than release_z_min_periods realised releases) or when "
            "the latest surprise itself is None."
        ),
    )
    release_z_window_releases: int = Field(
        ...,
        ge=1,
        description=(
            "Window used for the rolling z-score, in number of "
            "releases (NOT calendar days).  Matches the "
            "``release_z_window`` config convention."
        ),
    )

    current_actual_pct: Optional[float] = Field(
        None,
        description=(
            "actual at the latest realised release (event_calendar."
            "actual), in percentage points of YoY CPI."
        ),
    )
    current_consensus_median_pct: Optional[float] = Field(
        None,
        description=(
            "consensus_median at the latest realised release "
            "(event_calendar.consensus_median), in percentage points "
            "of YoY CPI.  None when no survey was published for the "
            "release (ADR 0008 §2 ``survey=false`` shape)."
        ),
    )
    current_prior_pct: Optional[float] = Field(
        None,
        description=(
            "prior at the latest realised release "
            "(event_calendar.prior) — the previous realised release's "
            "actual.  In percentage points of YoY CPI."
        ),
    )

    observation_count: int = Field(
        ...,
        ge=0,
        description=(
            "Count of realised releases (actual IS NOT NULL) in the "
            "display time series — the length of ``time_series``."
        ),
    )


class CpiSurpriseTimeSeriesRow(BaseModel):
    """Single row in the CPI-surprise time-series array.

    The ``date`` is the release date (NOT trade_date).  ``surprise_pct``
    is None on rows where Bloomberg has no consensus survey
    (honest absence per ADR 0008 §2).  ``z_score`` is None during
    the rolling-window warmup or on rows whose surprise is None.
    """

    date: str = Field(
        ...,
        description=(
            "Release date in YYYY-MM-DD form.  Sparse — releases "
            "happen monthly so consecutive rows are typically "
            "~30 calendar days apart."
        ),
    )
    period: Optional[str] = Field(
        None,
        description=(
            "Reference period of the release (e.g. 'Mar 2026') from "
            "event_calendar.period."
        ),
    )
    surprise_pct: Optional[float] = None
    z_score: Optional[float] = None
    actual_pct: Optional[float] = None
    consensus_median_pct: Optional[float] = None


class CpiSurpriseOutput(BaseModel):
    """Top-level response for the cpi_surprise tool.

    Three time-series fields:
      - ``time_series`` — the bespoke wire shape (list of
        CpiSurpriseTimeSeriesRow), matching the curve_spread /
        yield_levels / otr_ofr_spread frontend read pattern.
      - ``time_series_surprise`` — canonical TimeSeries
        (units = PERCENT) for the bridge to consume.
      - ``time_series_zscore`` — canonical TimeSeries
        (units = Z_SCORE) for the bridge to consume.

    All three are computed from the same display DataFrame and
    cannot drift — proven by the parity test in
    tests/fixtures/cpi_surprise_v1/.

    ``methodology_note`` surfaces ADR 0008's P12 disclosure (the
    surprise identity is computed at the primitive layer, not read
    from a Bloomberg-supplied surprise field) and TD #28b (D-econ
    release-date window limit) at the user-facing layer per PR10.
    """

    current_metrics: CpiSurpriseCurrentMetrics
    time_series: List[CpiSurpriseTimeSeriesRow]
    time_series_surprise: TimeSeries = Field(
        ...,
        description=(
            "Historical CPI surprise series (actual − consensus_median, "
            "in PERCENT — percentage points of YoY CPI) over the "
            "displayed window.  Closed-enum ``TimeSeriesUnits.PERCENT``; "
            "series_name = '<country_lower>_cpi_surprise'.  Values match "
            "``time_series[i].surprise_pct`` 1-to-1 by construction."
        ),
    )
    time_series_zscore: TimeSeries = Field(
        ...,
        description=(
            "Historical rolling z-score of the CPI surprise vs its own "
            "trailing release-window.  Closed-enum "
            "``TimeSeriesUnits.Z_SCORE``; series_name = "
            "'<country_lower>_cpi_surprise_zscore'.  Values match "
            "``time_series[i].z_score`` 1-to-1 (None for rows in the "
            "rolling-window warmup)."
        ),
    )
    methodology_note: str = Field(
        ...,
        description=(
            "Plain-language disclosure surfacing (a) the surprise "
            "identity ``actual − consensus_median`` per ADR 0008 §2's "
            "P12 disclosure, (b) the TD #28b release-date-window "
            "scope limit, (c) that pre-release revisions of PRIOR "
            "surprises are NOT in this series (separate primitive)."
        ),
    )


__all__ = [
    "CpiSurpriseInput",
    "CpiSurpriseCurrentMetrics",
    "CpiSurpriseTimeSeriesRow",
    "CpiSurpriseOutput",
]
