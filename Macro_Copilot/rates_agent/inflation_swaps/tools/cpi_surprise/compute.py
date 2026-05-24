"""
compute.py — Deterministic CPI-surprise math (config-driven)
============================================================

For one country's headline CPI YoY release series:
  - resolves the ``event_type`` via the YAML
    ``cpi_event_type_for_<country>`` conventions (US/UK/JP → ``cpi_yoy``,
    EU → ``hicp_yoy``);
  - fetches realised releases from ``macro_data.event_calendar`` via
    ``shared.analytics.events_fetch.fetch_economic_release_surprises``;
  - computes the per-release surprise = ``actual − consensus_median``
    (in percentage points of YoY CPI) — the exact identity ADR 0008 §2
    designates as the primitive layer's P12-disclosed computation;
  - applies a rolling z-score over the trailing
    ``release_z_window`` realised releases.

This is a Bucket 1A primitive in the simple-arithmetic-on-stored-data
family.  Every methodology choice (z-score window in releases, ddof,
warmup periods, rounding, surprise formula, country → event_type
mapping) flows from the bundled ``config.yaml``.

P12 (Bloomberg Accuracy Boundary) — explicit disclosure
-------------------------------------------------------
Per ADR 0008 §2, ``event_calendar.surprise`` is intentionally left
NULL by ingestion so the *primitive* layer can carry the
exact-identity computation with explicit disclosure.  This primitive
DOES NOT read ``event_calendar.surprise``; it subtracts
``actual − consensus_median`` row by row, and surfaces the identity
in ``methodology_note``.  Bloomberg's ECO screen reports the same
number; this primitive is not re-deriving anything — it's
materialising a desk-recognised identity at the right architectural
layer.

P11 (honest refusal) — single-method primitive
----------------------------------------------
There is no ``method`` enum.  When no realised release falls in the
display window, the response is a controlled error envelope naming
the missing data — matching curve_spread / otr_ofr_spread's
recoverable-failure shape.  Releases without a survey
(consensus_median IS NULL — honest absence per ADR 0008 §2) emit
``surprise_pct=None`` on that row rather than dropping the row.

PR14 — wire-format honesty
--------------------------
``surprise_formula`` is a categorical convention; the only V1-
supported value is ``actual_minus_consensus_median``.
``_validate_conventions`` raises NotImplementedError on any other
value, pointing at ``methodology.planned_extensions``.

Test seam
---------
Tests patch ``fetch_economic_release_surprises`` and ``date`` at this
module's namespace (``...cpi_surprise.compute.X``) — same pattern as
yield_levels / curve_spread / otr_ofr_spread.  The package
``__init__.py`` re-exports only public symbols; test seams must
target this module directly.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.inflation_swaps.tools.cpi_surprise.schemas import (
    CpiSurpriseCurrentMetrics,
    CpiSurpriseInput,
    CpiSurpriseOutput,
    CpiSurpriseTimeSeriesRow,
)
from shared.analytics.events_fetch import fetch_economic_release_surprises
from shared.config import ToolConfig, load_tool_config
from shared.schemas import TimeSeries, TimeSeriesRow, TimeSeriesUnits


# Bundled config — relative to this file.  Public symbol so external
# callers (mcp_server, REST routes, tests, parity-fixture capture)
# can build their own ToolConfig from the same source.
CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# Per PR14: ``surprise_formula`` is a categorical convention whose
# accepted-value set is wire-frozen in V1.  methodology.planned_extensions
# documents the path to widening.
_SUPPORTED_SURPRISE_FORMULA: str = "actual_minus_consensus_median"


# Per PR10: methodology_note surfaces ADR 0008's P12 disclosure +
# TD #28b at the user-facing layer.  String literal, structural
# (unchanged across calls); keeping it here in code matches PR7's
# "mathematical truths / structural constants in code" — the YAML
# disclosure lives in methodology.assumptions / citations /
# planned_extensions; this constant is the runtime user-facing echo.
_METHODOLOGY_NOTE: str = (
    "Source: macro_data.event_calendar (ADR 0004) populated by the "
    "economic-releases playbook per ADR 0008.  Surprise is the "
    "EXACT IDENTITY ``actual − consensus_median`` (in percentage "
    "points of YoY CPI) — the ingestion layer intentionally leaves "
    "event_calendar.surprise NULL (ADR 0008 §2) so the primitive "
    "layer carries this identity with the P12 disclosure on the "
    "wire (Bloomberg reports the same number; this primitive is "
    "materialising a desk-recognised identity at the right "
    "architectural layer, NOT recomputing what the vendor provides). "
    "Pre-release REVISIONS of prior surprises (``revised_prior`` on "
    "a later release re-stating the previous month's actual) are "
    "NOT in this series — those produce new ``revised_prior`` "
    "values on subsequent rows but do NOT retroactively adjust "
    "earlier ``surprise`` outputs (a revision-adjusted surprise "
    "primitive is a documented follow-up).  Scope: bounded by the "
    "ECO_RELEASE_DT_LIST release-date window (~1.5 years history + "
    "forward scheduled) per ADR 0008 §6 / TD #28b — releases "
    "pre-dating that window return honest absence."
)


# ============================================================================
# CONVENTION VALIDATION
# ============================================================================

def _validate_conventions(config: ToolConfig) -> None:
    """Refuse loudly when a convention is set to a value V1 does not
    yet support (PR14 + PR11 — categorical conventions whose accepted-
    value set is wire-frozen until a follow-up PR widens them).

    Raises NotImplementedError naming the offending value and pointing
    at ``methodology.planned_extensions`` in ``config.yaml``.
    """
    formula = config.convention_value("surprise_formula")
    if formula != _SUPPORTED_SURPRISE_FORMULA:
        raise NotImplementedError(
            f"surprise_formula={formula!r} is documented in this tool's "
            f"config.yaml as a future-supported value (see "
            f"methodology.planned_extensions) but is not yet "
            f"implemented.  V1 supports only "
            f"{_SUPPORTED_SURPRISE_FORMULA!r}.  Either restore the "
            f"value or implement the new branch in compute._compute_surprise."
        )


# ============================================================================
# PUBLIC API
# ============================================================================

def calculate_cpi_surprise(
    engine: Engine,
    params: CpiSurpriseInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """
    Compute the per-release CPI-surprise series and rolling release-
    window z-score for one country's headline CPI YoY release series.

    Parameters
    ----------
    engine : sqlalchemy.engine.Engine
        Live SQLAlchemy engine.
    params : CpiSurpriseInput
        Validated input.  ``country`` is the LLM-facing instrument
        selector (PR1); ``lookback_releases`` is the single LLM-
        controlled central methodology knob (PR8).
    config : ToolConfig, optional
        Bundled config.yaml is auto-loaded when None.  Tests pass a
        custom ToolConfig to exercise convention overrides.

    Returns
    -------
    dict
        Serialised ``CpiSurpriseOutput`` with current_metrics,
        time_series (bespoke), time_series_surprise /
        time_series_zscore (canonical), and methodology_note.

        On unsupported country (no ``cpi_event_type_for_<country>``
        convention) or empty result (no realised release in window)
        returns a controlled ``{"error": "..."}`` envelope.
    """

    if config is None:
        config = load_tool_config(CONFIG_PATH)

    _validate_conventions(config)

    # ------------------------------------------------------------------
    # Pull conventions.  Fail fast on missing keys.
    # ------------------------------------------------------------------
    z_window = int(config.convention_value("release_z_window"))
    z_min_periods = int(config.convention_value("release_z_min_periods"))
    z_ddof = int(config.convention_value("release_z_ddof"))
    buffer_days_per_release = int(
        config.convention_value("release_fetch_buffer_days_per_release")
    )
    surprise_round = int(config.convention_value("surprise_pct_round_decimals"))
    z_round = int(config.convention_value("release_z_round_decimals"))

    # ------------------------------------------------------------------
    # Resolve the country → event_type mapping via the
    # ``cpi_event_type_for_<country>`` convention.  Returns an error
    # envelope if the country is not declared in YAML (no silent
    # fall-through per P6).
    # ------------------------------------------------------------------
    convention_key = f"cpi_event_type_for_{params.country.lower()}"
    try:
        event_type = str(config.convention_value(convention_key))
    except KeyError:
        supported = sorted(
            k.removeprefix("cpi_event_type_for_").upper()
            for k in config.conventions
            if k.startswith("cpi_event_type_for_")
        )
        return {
            "error": (
                f"Unsupported country={params.country!r}: no "
                f"``{convention_key}`` convention in config.yaml.  "
                f"Supported countries: {supported}.  Extending requires "
                f"both economic_releases.yml to ingest the country's "
                f"CPI series AND a new ``{convention_key}`` convention "
                f"in cpi_surprise/config.yaml — see "
                f"methodology.planned_extensions."
            )
        }

    # ------------------------------------------------------------------
    # 1. Determine the fetch window
    # ------------------------------------------------------------------
    # Concepts:
    #   - lookback_releases:  number of realised releases to *display*
    #   - z_window:           rolling-z window in releases (NOT days)
    #   - calendar buffer:    enough calendar days to cover both
    #                         (lookback_releases + z_window) at the
    #                         configured per-release cadence
    today = date.today()
    fetch_releases = params.lookback_releases + z_window
    buffer_calendar_days = fetch_releases * buffer_days_per_release
    window_start = today - timedelta(days=buffer_calendar_days)
    window_end = today

    # ------------------------------------------------------------------
    # 2. Fetch via the shared analytics helper.  Returns event_calendar
    # rows (realised AND scheduled placeholders) sorted by release_date.
    # ------------------------------------------------------------------
    raw_df = fetch_economic_release_surprises(
        engine=engine,
        event_type=event_type,
        country=params.country,
        window_start=window_start,
        window_end=window_end,
    )

    if raw_df.empty:
        return {
            "error": (
                f"No event_calendar rows found for country='{params.country}', "
                f"event_type='{event_type}' since {window_start.isoformat()}.  "
                f"Either the event extractor has not yet ingested this slot "
                f"(ADR 0008 §6 / TD #28b — D-econ release-date window limit) "
                f"or the requested period pre-dates the playbook's coverage.  "
                f"Verify rates_agent/playbooks/economic_releases.yml covers "
                f"the country and that the extractor has run."
            )
        }

    # ------------------------------------------------------------------
    # 3. Drop scheduled placeholders (actual IS NULL).  Realised
    # releases are the rows the surprise series is built from.
    # ------------------------------------------------------------------
    df = raw_df.copy()
    df["release_date"] = pd.to_datetime(df["release_date"])
    df["actual"] = pd.to_numeric(df["actual"], errors="coerce")
    df["consensus_median"] = pd.to_numeric(df["consensus_median"], errors="coerce")
    df["prior"] = pd.to_numeric(df["prior"], errors="coerce")
    df = df.dropna(subset=["actual"])
    df = df.sort_values("release_date").reset_index(drop=True)

    if df.empty:
        return {
            "error": (
                f"No realised releases (actual NOT NULL) for "
                f"country='{params.country}', event_type='{event_type}' "
                f"in the lookback window — only scheduled placeholders "
                f"present.  This is expected pre-deployment for slots "
                f"the playbook covers but the extractor has not yet "
                f"observed (ADR 0008 §6 / TD #28b)."
            )
        }

    # ------------------------------------------------------------------
    # 4. Compute surprise = actual − consensus_median per row.  Rows
    # where consensus_median is NULL (no survey published — ADR 0008 §2)
    # emit surprise=None on the wire rather than a fabricated zero.
    # ------------------------------------------------------------------
    df["surprise_pct"] = (df["actual"] - df["consensus_median"]).round(surprise_round)

    # ------------------------------------------------------------------
    # 5. Rolling release-window z-score.  pandas' rolling.mean() /
    # rolling.std() honour NaN entries by excluding them from the
    # window, then the (current - mean) / std formula propagates NaN
    # naturally to rows whose surprise itself is NaN.  min_periods
    # gates the warmup.
    # ------------------------------------------------------------------
    spread = df["surprise_pct"]
    rolling = spread.rolling(window=z_window, min_periods=z_min_periods)
    rolling_mean = rolling.mean()
    rolling_std = rolling.std(ddof=z_ddof)
    df["z_score"] = ((spread - rolling_mean) / rolling_std).round(z_round)

    # ------------------------------------------------------------------
    # 6. Trim to the most-recent ``lookback_releases`` realised rows.
    # ------------------------------------------------------------------
    display_df = df.tail(params.lookback_releases).reset_index(drop=True)

    if display_df.empty:
        return {
            "error": (
                f"No realised releases within the requested "
                f"lookback_releases={params.lookback_releases} for "
                f"country='{params.country}'."
            )
        }

    # ------------------------------------------------------------------
    # 7. Build current_metrics from the latest realised row
    # ------------------------------------------------------------------
    latest = display_df.iloc[-1]
    surprise_label = (
        f"{params.country} CPI YoY surprise"
        if event_type == "cpi_yoy"
        else f"{params.country} HICP YoY surprise"
    )

    metrics = CpiSurpriseCurrentMetrics(
        release_date=_iso_date(latest["release_date"]),
        country=params.country,
        event_type=event_type,
        period=_str_or_none(latest.get("period")),
        surprise_label=surprise_label,
        current_surprise_pct=_safe_float(latest.get("surprise_pct")),
        current_z_score=_safe_float(latest.get("z_score")),
        release_z_window_releases=z_window,
        current_actual_pct=_safe_float(latest.get("actual")),
        current_consensus_median_pct=_safe_float(latest.get("consensus_median")),
        current_prior_pct=_safe_float(latest.get("prior")),
        observation_count=len(display_df),
    )

    # ------------------------------------------------------------------
    # 8. Build the bespoke + canonical time-series fields.  All three
    # are built from the same display_df rows so they cannot drift —
    # the parity test pins this.
    # ------------------------------------------------------------------
    ts_rows = [
        CpiSurpriseTimeSeriesRow(
            date=_iso_date(row.release_date),
            period=_str_or_none(getattr(row, "period", None)),
            surprise_pct=_safe_float(row.surprise_pct),
            z_score=_safe_float(row.z_score),
            actual_pct=_safe_float(row.actual),
            consensus_median_pct=_safe_float(row.consensus_median),
        )
        for row in display_df.itertuples()
    ]

    canonical_surprise = _build_canonical_surprise_series(
        display_df,
        country=params.country,
        surprise_round=surprise_round,
    )
    canonical_zscore = _build_canonical_zscore_series(
        display_df,
        country=params.country,
    )

    output = CpiSurpriseOutput(
        current_metrics=metrics,
        time_series=ts_rows,
        time_series_surprise=canonical_surprise,
        time_series_zscore=canonical_zscore,
        methodology_note=_METHODOLOGY_NOTE,
    )
    return output.model_dump()


# ============================================================================
# CANONICAL TIMESERIES BUILDERS — wire-format honesty (PR14)
# ============================================================================

def _build_canonical_surprise_series(
    display_df: pd.DataFrame,
    *,
    country: str,
    surprise_round: int,
) -> TimeSeries:
    """Convert the display DataFrame's surprise_pct column into the
    canonical ``TimeSeries`` shape (closed-enum PERCENT units).

    Naming convention: ``<country_lower>_cpi_surprise``.
    """
    series_name = f"{country.lower()}_cpi_surprise"
    rows = [
        TimeSeriesRow(
            date=_iso_date(row.release_date),
            value=(
                None
                if _is_nan(row.surprise_pct)
                else round(float(row.surprise_pct), surprise_round)
            ),
        )
        for row in display_df.itertuples()
    ]
    return TimeSeries(
        series_name=series_name,
        units=TimeSeriesUnits.PERCENT,
        description=(
            f"Per-release CPI surprise for {country} "
            f"(actual − consensus_median, in percentage points of YoY "
            f"CPI) over the displayed release window."
        ),
        rows=rows,
    )


def _build_canonical_zscore_series(
    display_df: pd.DataFrame,
    *,
    country: str,
) -> TimeSeries:
    """Convert the display DataFrame's z_score column into the canonical
    ``TimeSeries`` shape (closed-enum Z_SCORE units).

    Naming convention: ``<country_lower>_cpi_surprise_zscore``.  Rows
    where the rolling window has not warmed up are emitted as None so
    the canonical shape matches the bespoke time_series 1-to-1.
    """
    series_name = f"{country.lower()}_cpi_surprise_zscore"
    rows = [
        TimeSeriesRow(
            date=_iso_date(row.release_date),
            value=_safe_float(row.z_score),
        )
        for row in display_df.itertuples()
    ]
    return TimeSeries(
        series_name=series_name,
        units=TimeSeriesUnits.Z_SCORE,
        description=(
            f"Rolling release-window z-score of the {country} CPI "
            f"surprise series vs its own trailing window."
        ),
        rows=rows,
    )


# ============================================================================
# Small typed coercions — wire-format honesty (PR14)
# ============================================================================

def _iso_date(value: Any) -> str:
    """Coerce ``date`` / ``Timestamp`` / ISO string to canonical
    ``YYYY-MM-DD``.  Required value; raises TypeError on None (the
    SCD2 row's release_date is NOT NULL in schema, so None should
    never reach here — defence in depth)."""
    if value is None:
        raise TypeError(
            "release_date is NOT NULL in macro_data.event_calendar "
            "(ADR 0004 schema) — None should never reach this coercion."
        )
    if hasattr(value, "isoformat"):
        iso = value.isoformat()
        return iso[:10]
    return str(value)[:10]


def _str_or_none(value: Any) -> Optional[str]:
    """Coerce nullable string columns (period, etc.) to Optional[str].
    Empty strings and NaN coerce to None."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    s = str(value).strip()
    return s if s else None


def _safe_float(value: Any) -> Optional[float]:
    """Coerce a nullable numeric to ``Optional[float]``.  NaN → None."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _is_nan(value: Any) -> bool:
    """Test whether a scalar is NaN (without relying on ``pd.isna``
    which raises on some odd types)."""
    if value is None:
        return True
    try:
        return math.isnan(float(value))
    except (TypeError, ValueError):
        return False
