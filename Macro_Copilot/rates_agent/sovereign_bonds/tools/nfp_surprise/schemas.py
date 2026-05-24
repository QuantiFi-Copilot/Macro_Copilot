"""Pydantic schemas for the nfp_surprise tool.

Per-release US nonfarm payrolls (NFP) surprise series
(``actual − consensus_median`` in thousands of jobs) plus a rolling
z-score over a window of N releases.  Built on the event-calendar
substrate landed by ADR 0004 and populated by the economic-releases
playbook per ADR 0008.

Output shape
------------
Snapshot + canonical ``TimeSeries`` — the curve_spread / yield_levels
/ otr_ofr_spread / cpi_surprise archetype.  Two canonical TimeSeries
fields:

  - ``time_series_surprise`` (units = COUNT) — the historical surprise
    values (actual − consensus_median, in thousands of jobs).  Closed-
    enum ``TimeSeriesUnits.COUNT`` is the closest match for an
    integer-count delta; the ``description`` field on the TimeSeries
    states explicitly that the unit is "thousands of jobs" so a
    downstream consumer reading the canonical shape does not have
    to infer it from the series_name.
  - ``time_series_zscore`` (units = Z_SCORE) — the rolling z-score
    over the trailing ``release_z_window`` realised releases.

Both fields plus the bespoke ``time_series`` array are built from the
same display DataFrame and cannot drift — proven by the parity test.

The time-series INDEX is the release date (``YYYY-MM-DD`` string),
NOT trade_date — releases happen monthly so the index is sparse
relative to a daily series.  Each row also carries the reference
``period`` (e.g. "Apr 2026") for desk readability.

PR8 framing (per the brief)
---------------------------
This primitive has **no LLM-facing methodology knob**.  Per the
brief: "single-country single-event.  If you find you need a knob,
reconsider whether this is a primitive (PR5)."

- ``country`` and ``event_type`` are BOTH YAML-locked
  (config.yaml conventions, NOT Pydantic inputs) — wire-frozen at
  ``US`` and ``nfp`` respectively.  Adding another country /
  event_type would be a different primitive.
- ``lookback_releases`` is the DISPLAY-WINDOW knob — same shape as
  ``lookback_days`` in curve_spread / yield_levels (which PR8's
  worked-example table names as the "central methodology surface"
  for those primitives: a display window, not a methodology
  window).  The rolling z-score window is YAML-locked at
  ``release_z_window=24`` regardless of this value.

So the Pydantic input carries ONLY ``lookback_releases`` — the
display-window knob.  No instrument selector, no methodology
choice.  This is the cleanest possible PR8 expression: a single
display knob with everything else YAML-locked, matching the brief's
"none substantive" framing.

Validation
----------
``lookback_releases`` is bounded [4, 200] (same as cpi_surprise).
Pydantic default is sourced from ``config.yaml``'s
``default_lookback_releases`` convention via the lazy-factory
pattern (P10 — single source of truth).
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from shared.schemas import TimeSeries


def _bundled_default_lookback_releases() -> int:
    """Read the default lookback (number of releases) from the
    bundled ``config.yaml``.

    Looked up lazily inside the field-default factory so circular-
    import risk is zero (the schema doesn't import compute or
    ToolConfig at module-load time; only the factory path touches
    them).  Cached by the underlying ``load_tool_config`` so this is
    a one-time cost.

    Raises loudly when ``config.yaml`` cannot be loaded or the
    convention is missing — per P6 (no silent failure) and P10
    (single source of truth).  Same lazy-lookup pattern as
    cpi_surprise / get_otr_history / otr_ofr_spread.
    """
    from rates_agent.sovereign_bonds.tools.nfp_surprise.compute import (
        CONFIG_PATH,
    )
    from shared.config import load_tool_config

    cfg = load_tool_config(CONFIG_PATH)
    return int(cfg.convention_value("default_lookback_releases"))


class NfpSurpriseInput(BaseModel):
    """Parameters the LLM extracts to query the NFP-surprise series.

    Per the brief, this primitive has no LLM-facing instrument
    selector or methodology choice — both ``country`` and
    ``event_type`` are YAML-locked (US-only, event_type=nfp).  The
    only input is the display-window length in number of releases.
    """

    lookback_releases: int = Field(
        default_factory=_bundled_default_lookback_releases,
        ge=4,
        le=200,
        description=(
            "Number of realised releases of trailing history to display in "
            "the time_series output.  DISPLAY WINDOW ONLY — not a "
            "methodology choice.  The bundled default is read from "
            "config.yaml's ``default_lookback_releases`` convention "
            "(currently 24 ≈ 2Y at monthly NFP cadence).  The rolling "
            "z-score window is YAML-locked at "
            "``release_z_window=24`` regardless of this value (same "
            "display-vs-z-window separation as cpi_surprise / "
            "curve_spread / yield_levels)."
        ),
    )


class NfpSurpriseCurrentMetrics(BaseModel):
    """Snapshot metrics for the most-recent realised NFP release."""

    release_date: str = Field(
        ...,
        description="Most recent realised release date (YYYY-MM-DD).",
    )
    country: str = Field(
        ...,
        description=(
            "Country — wire-locked at 'US' (NFP is US-only)."
        ),
    )
    event_type: str = Field(
        ...,
        description=(
            "Event type — wire-locked at 'nfp' (Bloomberg ticker "
            "'NFP TCH Index')."
        ),
    )
    period: Optional[str] = Field(
        None,
        description=(
            "Reference period of the release (e.g. 'Apr 2026') from "
            "event_calendar.period."
        ),
    )
    surprise_label: str = Field(
        ...,
        description="Human-readable label — 'US NFP surprise'.",
    )

    current_surprise_k_jobs: Optional[float] = Field(
        None,
        description=(
            "Surprise (actual − consensus_median) at the latest "
            "realised release, in THOUSANDS of jobs.  None when "
            "consensus_median is NULL on the latest realised row "
            "(honest absence — no survey was published)."
        ),
    )
    current_z_score: Optional[float] = Field(
        None,
        description=(
            "Rolling z-score of the latest surprise vs the trailing "
            "release_z_window window.  None during warmup or when "
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

    current_actual_k_jobs: Optional[float] = Field(
        None,
        description=(
            "actual at the latest realised release "
            "(event_calendar.actual), in thousands of jobs."
        ),
    )
    current_consensus_median_k_jobs: Optional[float] = Field(
        None,
        description=(
            "consensus_median at the latest realised release "
            "(event_calendar.consensus_median), in thousands of jobs."
        ),
    )
    current_prior_k_jobs: Optional[float] = Field(
        None,
        description=(
            "prior at the latest realised release "
            "(event_calendar.prior) — the previous realised release's "
            "actual.  In thousands of jobs.  NOTE: the prior is the "
            "AS-RELEASED value at the time of THIS release; if a "
            "later release publishes a different ``revised_prior``, "
            "this field does NOT retroactively change (see the "
            "PRIOR-MONTH-REVISIONS caveat in methodology_note)."
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


class NfpSurpriseTimeSeriesRow(BaseModel):
    """Single row in the NFP-surprise time-series array.

    The ``date`` is the release date.  ``surprise_k_jobs`` is None on
    rows where Bloomberg has no consensus survey (honest absence per
    ADR 0008 §2 — has not happened for NFP historically but the wire
    shape supports it for forward-compatibility).  ``z_score`` is
    None during the rolling-window warmup or on rows whose surprise
    is None.
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
            "Reference period of the release (e.g. 'Apr 2026') from "
            "event_calendar.period."
        ),
    )
    surprise_k_jobs: Optional[float] = None
    z_score: Optional[float] = None
    actual_k_jobs: Optional[float] = None
    consensus_median_k_jobs: Optional[float] = None


class NfpSurpriseOutput(BaseModel):
    """Top-level response for the nfp_surprise tool.

    Three time-series fields:
      - ``time_series`` — the bespoke wire shape (list of
        NfpSurpriseTimeSeriesRow), matching the curve_spread /
        yield_levels / otr_ofr_spread / cpi_surprise frontend read
        pattern.
      - ``time_series_surprise`` — canonical TimeSeries
        (units = COUNT — the closest enum match for thousands-of-jobs;
        description field makes the exact unit explicit) for the
        bridge to consume.
      - ``time_series_zscore`` — canonical TimeSeries
        (units = Z_SCORE) for the bridge to consume.

    All three are computed from the same display DataFrame and
    cannot drift — proven by the parity test in
    tests/fixtures/nfp_surprise_v1/.

    ``methodology_note`` surfaces ADR 0008's P12 disclosure + the
    well-known PRIOR-MONTH-REVISIONS caveat + TD #28b (D-econ
    release-date window limit) at the user-facing layer per PR10.
    """

    current_metrics: NfpSurpriseCurrentMetrics
    time_series: List[NfpSurpriseTimeSeriesRow]
    time_series_surprise: TimeSeries = Field(
        ...,
        description=(
            "Historical US NFP surprise series (actual − consensus_median, "
            "in thousands of jobs) over the displayed window.  Closed-enum "
            "``TimeSeriesUnits.COUNT`` (the closest match for "
            "thousands-of-jobs deltas; description field carries the "
            "explicit 'thousands of jobs' label).  series_name = "
            "'us_nfp_surprise'.  Values match "
            "``time_series[i].surprise_k_jobs`` 1-to-1 by construction."
        ),
    )
    time_series_zscore: TimeSeries = Field(
        ...,
        description=(
            "Historical rolling z-score of the US NFP surprise vs its "
            "own trailing release-window.  Closed-enum "
            "``TimeSeriesUnits.Z_SCORE``; series_name = "
            "'us_nfp_surprise_zscore'.  Values match "
            "``time_series[i].z_score`` 1-to-1 (None for rows in the "
            "rolling-window warmup)."
        ),
    )
    methodology_note: str = Field(
        ...,
        description=(
            "Plain-language disclosure surfacing (a) the surprise "
            "identity ``actual − consensus_median`` per ADR 0008 §2's "
            "P12 disclosure, (b) the well-known NFP prior-month "
            "revisions caveat (revisions of prior actuals are NOT in "
            "this surprise series — the AS-RELEASED surprise is "
            "preserved), (c) the TD #28b release-date-window scope "
            "limit."
        ),
    )


__all__ = [
    "NfpSurpriseInput",
    "NfpSurpriseCurrentMetrics",
    "NfpSurpriseTimeSeriesRow",
    "NfpSurpriseOutput",
]
