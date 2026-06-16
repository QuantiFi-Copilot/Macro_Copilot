"""Pydantic schemas for the linker real_yield_level tool.

Lives in the per-tool-folder pattern established by the tool-config
refactor pilot (see ``docs/architecture/tool_architecture.md``) and used
by every sovereign + OIS tool.  This is the FIRST tool in the
``inflation_indexed_bonds`` domain — the level-stat methodology mirrors
sovereign ``yield_levels`` / OIS ``rate_level`` because the canonical
desk math (z-score, period changes, trailing range) is the same; the
*concept* the tool owns (a linker curve point's real yield) is genuinely
new and distinct from a nominal sovereign yield.

Phase-1 pilot exposure surface
------------------------------
This tool is the first under the methodology-exposure standard
(``docs_revamped/03_standards/methodology_exposure.md``).  Per-
convention exposure decisions live in ``config.yaml``'s ``exposure:``
sub-blocks; the Pydantic ``Input`` surface below MIRRORS those
decisions.  Four conventions are exposed as Pydantic Input fields:

  - ``z_score_window_days``  (cohesive rolling-z-score model)
  - ``z_score_min_periods``  (cohesive rolling-z-score model)
  - ``z_score_ddof``         (cohesive rolling-z-score model)
  - ``default_field_name`` ← already exposed as ``field_name``
                             (Bloomberg-field identity)

The remaining nine conventions stay YAML-locked (display precision,
fetch-window math, wire-frozen field-name anchors, data-cleaning).
See each convention's ``exposure:`` block for the per-decision
Criterion-A / Criterion-B rationale.

Validation layering
-------------------
- ``field_name`` defaults to ``None`` — the sentinel that means "use
  the YAML's ``default_field_name`` convention".  Callers can still
  override per-query.  ``compute()`` is the one place that resolves
  the sentinel against the active config, so editing
  ``default_field_name`` in YAML actually changes runtime behaviour.
  Mirrors the empty-string / None-sentinel pattern established by the
  curve_move_classifier sovereign migration (commit 9f741ea) and
  tightened by commit b2605ee.

- ``z_score_window_days``, ``z_score_min_periods``, ``z_score_ddof``
  follow the SAME None-sentinel pattern: when None, ``compute()``
  resolves against the YAML default; when set, the explicit value
  overrides per call.  The three together specify the rolling-
  z-score model — cohesive multi-knob surface admissible per PR8.

- ``lookback_days`` controls the fetch window AND the
  ``observation_count`` cutoff window.  It does NOT control the
  rolling z-score window (now overridable via ``z_score_window_days``;
  default 252 from YAML) or the trailing range window (still locked
  at 252 in V1 via the ``trailing_range_window_days`` YAML convention
  and the ``NotImplementedError`` guard in compute._conventions_from_config).

Validators that encode invariants stay here in code; this tool has no
cross-field invariants beyond what Pydantic's basic Field constraints
already enforce.

Canonical TimeSeries output
---------------------------
This tool emits the canonical ``shared.schemas.time_series.TimeSeries``
shape via ``time_series: TimeSeries`` (singular, matching the v6
sovereign primitive convention used by ``yield_levels`` /
``zscore_custom`` / OIS ``rate_level``).  Each row is rounded with the
same ``yield_round_decimals`` convention the snapshot uses, so the
snapshot's ``real_yield_pct`` equals ``time_series.rows[-1].value``
exactly (not just within tolerance).
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field

from shared.schemas import TimeSeries


class RealYieldLevelInput(BaseModel):
    """Parameters the LLM must extract from the user to fetch a linker
    real-yield level.  Every field maps directly to a filter column on
    ``macro_data.v_market_data_daily_enriched``.
    """

    curve_family: str = Field(
        ...,
        description=(
            "Linker curve family identifier as stored in "
            "instrument_master.  Examples: 'USD_TIPS', 'GBP_LINKER', "
            "'EUR_FR_LINKER', 'CAD_RRB' (see "
            "rates_agent/playbooks/inflation_indexed_bonds.yml for the "
            "ingested universe)."
        ),
    )
    tenor: str = Field(
        ...,
        description=(
            "Tenor point on the linker curve.  Available tenors are "
            "curve-family specific: USD_TIPS exposes 5Y/10Y/20Y/30Y; "
            "GBP_LINKER exposes 1Y..50Y; EUR_FR_LINKER exposes "
            "2Y/5Y/7Y/10Y/15Y; CAD_RRB exposes 5Y..30Y.  See the "
            "ingestion playbook for the live list."
        ),
    )
    lookback_days: int = Field(
        default=365,
        ge=30,
        le=7300,
        description=(
            "Calendar days of history fetched from the DB and used to "
            "scope the observation_count window in the output.  Does "
            "NOT control the rolling z-score window (fixed by config "
            "convention z_score_window_days, currently 252) or the "
            "trailing range window (fixed by "
            "trailing_range_window_days, locked at 252 in V1)."
        ),
    )
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg observation field.  When None (default), the "
            "tool falls through to ``default_field_name`` from "
            "config.yaml (currently 'YLD_YTM_MID' — the linker "
            "real-yield-to-maturity mnemonic).  Pass an explicit field "
            "name to override per query (e.g. 'YLD_YTM_BID' / "
            "'YLD_YTM_ASK').  LLM/HTTP wrappers MUST translate their "
            "wire-level sentinel (empty string for MCP, missing param "
            "for FastAPI) to None before constructing this input — "
            "otherwise the YAML default is silently shadowed.  See the "
            "curve_move_classifier wrapper-shadowing fix (commit "
            "b2605ee) for the canonical pattern.  Exposure decision "
            "recorded in config.yaml:default_field_name.exposure."
        ),
    )

    # ------------------------------------------------------------------
    # Methodology surface — rolling-z-score model (cohesive multi-knob,
    # admissible per PR8 because the three fields together specify the
    # rolling-z-score "model").  All three follow the None-sentinel /
    # YAML-fallthrough pattern established by ``field_name`` above;
    # ``compute()`` is the one place that resolves the sentinels.
    # Exposure decisions recorded in config.yaml's per-convention
    # exposure: blocks; see docs_revamped/03_standards/methodology_exposure.md.
    # ------------------------------------------------------------------
    z_score_window_days: Optional[int] = Field(
        default=None,
        ge=60,
        le=1260,
        description=(
            "Trading-day window for the rolling z-score.  When None "
            "(default), the tool falls through to "
            "``z_score_window_days`` in config.yaml (currently 252 — "
            "1-year window matching every sovereign rates tool and OIS "
            "rate_level).  Override to 60d / 126d for tactical / "
            "short-horizon framing or 504d for structural-regime work "
            "(desk-doc 04_inflation_indexed_bonds.md §10 + §3).  "
            "Cross-config lint enforces DEFAULT alignment; per-call "
            "overrides do not violate the lint.  Exposure decision "
            "recorded in config.yaml:z_score_window_days.exposure."
        ),
    )
    z_score_min_periods: Optional[int] = Field(
        default=None,
        ge=20,
        le=252,
        description=(
            "Minimum number of observations before the rolling z-score "
            "is emitted (NaN otherwise).  When None (default), the "
            "tool falls through to ``z_score_min_periods`` in "
            "config.yaml (currently 60 — ~3 months of observations).  "
            "Scale together with ``z_score_window_days`` when "
            "overriding (e.g. window=60 → min_periods=20).  Exposure "
            "decision recorded in config.yaml:z_score_min_periods.exposure."
        ),
    )
    z_score_ddof: Optional[int] = Field(
        default=None,
        ge=0,
        le=1,
        description=(
            "Standard-deviation degrees of freedom for the rolling "
            "z-score.  When None (default), the tool falls through to "
            "``z_score_ddof`` in config.yaml (currently 1 — sample "
            "standard deviation, Bessel-corrected, matching pandas' "
            "default).  Practical values are {0 (population std), "
            "1 (sample std)}; constrained ge=0, le=1.  Cross-config "
            "lint enforces DEFAULT alignment.  Exposure decision "
            "recorded in config.yaml:z_score_ddof.exposure."
        ),
    )
    as_of_date: Optional[date] = Field(
        default=None,
        description=("Optional as-of date (YYYY-MM-DD): compute as of this trade "
                     "date instead of the latest available data.  None → latest "
                     "(live snapshot).  Supply a date for a historical, replayable view."),
    )


class RealYieldLevelMetrics(BaseModel):
    """Deterministic snapshot for a single linker real-yield curve point.

    Field names ``high_252d_pct``, ``low_252d_pct``, and
    ``percentile_252d`` embed the trailing-range window length (252) in
    their identifiers and are therefore wire-frozen in V1.  See
    config.yaml's ``planned_extensions`` for the path to making the
    trailing window configurable.
    """

    as_of_date: str = Field(..., description="Most recent trade date (YYYY-MM-DD).")
    curve_family: str
    tenor: str
    real_yield_pct: float = Field(
        ...,
        description=(
            "Current real yield in percent.  Distinct from the nominal "
            "sovereign ``current_yield_pct`` field — units agree "
            "(percent) but the underlying series is the linker's "
            "real-yield-to-maturity, not a nominal yield."
        ),
    )
    daily_change_bps: Optional[float] = Field(
        None, description="1-trading-day change in basis points."
    )
    weekly_change_bps: Optional[float] = Field(
        None, description="5-trading-day change in basis points."
    )
    monthly_change_bps: Optional[float] = Field(
        None, description="22-trading-day (~1 month) change in basis points."
    )
    z_score: Optional[float] = Field(
        None,
        description=(
            "Rolling 252-trading-day z-score of the real yield vs its "
            "own history."
        ),
    )
    high_252d_pct: Optional[float] = Field(
        None,
        description="Highest real yield over trailing 252 trading days (percent).",
    )
    low_252d_pct: Optional[float] = Field(
        None,
        description="Lowest real yield over trailing 252 trading days (percent).",
    )
    percentile_252d: Optional[float] = Field(
        None,
        description="Percentile rank within trailing 252-day range (0-100).",
    )
    observation_count: int = Field(
        ..., description="Number of trading days in the lookback_days window."
    )


class RealYieldLevelOutput(BaseModel):
    """Top-level response for the real_yield_level tool.

    ``current_metrics`` is the snapshot the desk consumes; ``time_series``
    carries the canonical closed-enum TimeSeries the
    primitive-to-operator bridge expects.  Singular ``TimeSeries`` field
    matches the v6 sovereign primitive convention (see
    ``yield_levels`` / ``zscore_custom`` / OIS ``rate_level``).
    """

    current_metrics: RealYieldLevelMetrics
    time_series: TimeSeries = Field(
        ...,
        description=(
            "Historical linker real-yield levels at the requested "
            "tenor over the display window (last ``lookback_days`` "
            "calendar days).  Closed-enum ``TimeSeriesUnits.PERCENT`` "
            "units; series_name = "
            "'<curve_family_lower>_<tenor_lower>_real_yield' (the "
            "``_real_yield`` suffix distinguishes from nominal "
            "sovereign yield series when both end up in the same "
            "operator panel downstream).  Each row is rounded with the "
            "same ``yield_round_decimals`` convention the snapshot "
            "uses, so the snapshot's ``real_yield_pct`` equals "
            "``time_series.rows[-1].value`` STRICTLY (not just within "
            "tolerance)."
        ),
    )


__all__ = [
    "RealYieldLevelInput",
    "RealYieldLevelMetrics",
    "RealYieldLevelOutput",
]
