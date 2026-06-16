"""
compute.py — Config-driven linker real-yield-level tool
========================================================

First tool in the ``inflation_indexed_bonds`` domain.  Every methodology
choice (z-score window/min_periods/ddof, fill limit, period offsets,
trailing range window, default field, rounding) flows from the bundled
``config.yaml`` rather than from module-level constants or hardcoded
literals.

Same single primitive used by sovereign yield_levels / OIS rate_level
---------------------------------------------------------------------
The actual metrics computation (z-score / period changes / trailing
range) is delegated to ``shared.analytics.levels.compute_level_metrics``
— the same primitive the sovereign and OIS level tools use.  Single
source of truth for the rolling math; the linker YAML, sovereign YAML,
and OIS YAML all feed into it via identical kwarg names.

Concept honesty
---------------
This tool owns the *concept* of a linker curve point's real-yield
level.  The desk distinction between this and the sovereign
``yield_levels`` tool is the underlying instrument family
(``inflation_linker`` vs ``sovereign_benchmark``) and the resulting
series semantics (real yield vs nominal yield) — NOT the level-stat
math, which is invariant to the underlying.  Wire surfaces use linker
terminology end-to-end (``real_yield_pct`` field, ``_real_yield``
series suffix) so downstream operator panels cannot silently mix real
and nominal series.

Phase-1 methodology-exposure surface
------------------------------------
Per ``docs_revamped/03_standards/methodology_exposure.md`` (Phase 1
of the ``revamp`` branch), three rolling-z-score conventions are
exposed as Pydantic Input fields with per-call overrides:

    z_score_window_days   z_score_min_periods   z_score_ddof

Plus the existing ``field_name`` (overrides ``default_field_name``).
All four follow the same None-sentinel / YAML-fallthrough pattern:
when the caller supplies None (the schema default), ``compute()``
resolves against the YAML default; when set, the explicit value
overrides per call.  ``_conventions_from_config(config, params)`` is
the one place where the resolution happens.

The remaining nine conventions stay YAML-locked.  See each
convention's ``exposure:`` block in ``config.yaml`` for per-decision
Criterion-A / Criterion-B rationale.

Honest placeholder
------------------
``trailing_range_window_days`` is locked at 252 in V1.  The output
schema's field names (``high_252d_pct``, ``low_252d_pct``,
``percentile_252d``) embed the number; changing the convention without
renaming the wire fields would silently lie about what the percentile
is computed against.  The compute path raises ``NotImplementedError`` if
this is set to anything else; see ``methodology.planned_extensions`` in
the YAML for the path to making it configurable.  This guard fires at
the convention layer (YAML-side); the corresponding Input field is
NOT exposed.

Observation-count anchoring
---------------------------
The cutoff is anchored to the data's latest observation date (matching
OIS ``rate_level``), NOT to ``date.today()``.  Linker daily series can
lag wall-clock by several business days; anchoring to date.today()
would silently shrink the displayed window when data is stale.  The
sovereign yield_levels tool currently anchors to date.today(); the
inconsistency is documented in both tools' planned_extensions.

Test seam
---------
``fetch_single_tenor`` and ``date`` are imported here at module level;
tests patch them via
``patch("rates_agent.inflation_indexed_bonds.tools.real_yield_level.compute.X")``.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.inflation_indexed_bonds.tools.real_yield_level.schemas import (
    RealYieldLevelInput,
    RealYieldLevelMetrics,
    RealYieldLevelOutput,
)
from shared.analytics.levels import (
    clean_single_series,
    compute_level_metrics,
)
from shared.analytics.rates_fetch import fetch_single_tenor, latest_trade_date
from shared.config import ToolConfig, load_tool_config
from shared.schemas import TimeSeries, TimeSeriesRow, TimeSeriesUnits


# Bundled config — public symbol so external callers (mcp_server,
# tests, future REST routes) can build a ToolConfig from the same
# source the tool uses.
CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# Trailing-range window is wire-frozen at 252 in V1; see schemas.py
# and config.yaml's ``planned_extensions``.  This guard fires at the
# convention layer so an editor of config.yaml gets a clear error
# rather than producing percentiles labelled "252d" against a
# different window.  Mirrors the same guard in sovereign yield_levels
# and OIS rate_level.
_FROZEN_TRAILING_WINDOW: int = 252


# instrument_type filter passed to fetch_single_tenor.  Code-level
# invariant — the whole point of this primitive owning a separate
# concept from sovereign yield_levels is that it operates on
# inflation-linker rows, not nominal sovereign rows.  Putting this in
# YAML would let an edit to config.yaml silently switch the tool over
# to nominal data; per DESIGN_PRINCIPLES.md §5, structural identity
# stays in code.
_LINKER_INSTRUMENT_TYPE: str = "inflation_linker"


# ============================================================================
# CONFIG → KWARGS RESOLVER
# ============================================================================

def _conventions_from_config(
    config: ToolConfig,
    params: Optional[RealYieldLevelInput] = None,
) -> dict:
    """Pull the methodology kwargs ``compute_level_metrics`` needs from
    a ToolConfig, applying any caller overrides from ``params``.

    Override semantics
    ------------------
    For each Pydantic Input field whose corresponding YAML convention
    has ``exposure.expose: true`` (see
    ``docs_revamped/03_standards/methodology_exposure.md``):

      - When the Input field is ``None`` (the schema default) the YAML
        value is used.
      - When the Input field carries an explicit value, that value
        overrides the YAML for this call.

    The three rolling-z-score conventions are the currently-exposed
    methodology surface:
      - ``z_score_window_days``
      - ``z_score_min_periods``
      - ``z_score_ddof``

    The nine YAML-locked conventions (period offsets, fetch buffer,
    rounding, ffill limit, trailing range) are read straight from the
    config regardless of ``params`` — no override path.

    Raises NotImplementedError if ``trailing_range_window_days`` is set
    to anything other than 252 — see the wire-freeze rationale in the
    module docstring.

    Backward compatibility
    ----------------------
    ``params`` is optional (default None) so legacy callers that
    invoked this helper without an Input continue to work — the
    no-params path returns the pure-YAML resolution that pre-dated
    the Phase-1 exposure work.
    """
    trailing = config.convention_value("trailing_range_window_days")
    if trailing != _FROZEN_TRAILING_WINDOW:
        raise NotImplementedError(
            f"trailing_range_window_days={trailing!r} is documented in this "
            f"tool's config.yaml as a future-supported value (see "
            f"methodology.planned_extensions) but is not yet implemented.  "
            f"V1 supports only {_FROZEN_TRAILING_WINDOW} because the output "
            f"field names (high_252d_pct, low_252d_pct, percentile_252d) "
            f"embed that number on the wire.  Either restore the value to "
            f"{_FROZEN_TRAILING_WINDOW} or implement the schema rename + "
            f"frontend update documented in planned_extensions."
        )

    def _override(convention_name: str) -> Any:
        """Return the caller's Input value when non-None, else the YAML value.

        Mirrors the field_name sentinel pattern: caller's explicit
        value wins; None falls through to YAML.  Defensive against
        future schema changes via ``getattr(..., default=None)``.
        """
        if params is not None:
            input_value = getattr(params, convention_name, None)
            if input_value is not None:
                return input_value
        return config.convention_value(convention_name)

    return {
        # Exposed methodology surface — Input overrides accepted.
        "z_window": _override("z_score_window_days"),
        "z_min_periods": _override("z_score_min_periods"),
        "z_ddof": _override("z_score_ddof"),
        # YAML-locked conventions — read straight from config.
        "period_offsets": {
            "daily": config.convention_value("daily_change_offset_rows"),
            "weekly": config.convention_value("weekly_change_offset_rows"),
            "monthly": config.convention_value("monthly_change_offset_rows"),
        },
        "trailing_window": trailing,
        "yield_round_decimals": config.convention_value("yield_round_decimals"),
        "z_score_round_decimals": config.convention_value("z_score_round_decimals"),
        "high_low_round_decimals": config.convention_value("high_low_round_decimals"),
    }


# ============================================================================
# PUBLIC API
# ============================================================================

def get_real_yield_level(
    engine: Engine,
    params: RealYieldLevelInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Return current linker real-yield level + period changes +
    z-score + trailing high/low/percentile + observation_count for a
    single linker curve point.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine.
    params : RealYieldLevelInput
        Validated input.  ``field_name=None`` resolves against the
        YAML's ``default_field_name`` convention.
    config : ToolConfig, optional
        Bundled config.yaml is auto-loaded when None.  Tests pass a
        custom ToolConfig to exercise convention overrides.

    Returns
    -------
    dict
        Serialised ``RealYieldLevelOutput``, or ``{"error": "..."}`` on
        recoverable failure.
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    # ------------------------------------------------------------------
    # Pull conventions.  ``metrics_kwargs`` applies any per-call
    # Input overrides for the exposed rolling-z-score surface
    # (z_score_window_days / z_score_min_periods / z_score_ddof);
    # see _conventions_from_config + config.yaml exposure blocks.
    # ``z_window`` below is used ONLY for fetch-window math (it
    # determines how far back to query the DB so the rolling z-score
    # is fully populated from the first displayed trading day) and
    # therefore reads the SAME effective value the metrics layer will
    # use — pull it from metrics_kwargs to preserve the override.
    # ------------------------------------------------------------------
    metrics_kwargs = _conventions_from_config(config, params)
    z_window = metrics_kwargs["z_window"]
    buffer_mult = config.convention_value("z_score_buffer_multiplier")
    ffill_limit = config.convention_value("ffill_limit_days")
    default_field_name = config.convention_value("default_field_name")

    # Resolve field_name: caller's explicit value wins; None falls
    # through to the YAML default.
    field_name_resolved = (
        params.field_name if params.field_name is not None else default_field_name
    )

    # ------------------------------------------------------------------
    # 1. Date window
    # ------------------------------------------------------------------
    buffer_calendar_days = int(z_window * buffer_mult)
    # Anchor the fetch window to the latest available trade_date (not
    # date.today()) so a short lookback still resolves to real data when
    # the linker feed lags wall-clock; falls back to today only when the
    # filtered universe is empty.  The display cutoff below is separately
    # anchored to the data's last observation (see step 5).
    anchor = (
        params.as_of_date
        or latest_trade_date(
            engine,
            curve_family=params.curve_family,
            tenor=params.tenor,
            field_name=field_name_resolved,
            instrument_type=_LINKER_INSTRUMENT_TYPE,
        )
        or date.today()
    )
    start_date = anchor - timedelta(
        days=params.lookback_days + buffer_calendar_days
    )

    # ------------------------------------------------------------------
    # 2. Fetch — instrument_type='inflation_linker' is mandatory.
    # Without this filter, calling the tool with curve_family='UST'
    # would silently return nominal sovereign rows and present them
    # under a real_yield_pct label.  The instrument_type guard is the
    # load-bearing guarantee that this primitive cannot proxy nominal
    # data — see DESIGN_PRINCIPLES.md §1, §3 and STANDARD_TOOL_AND_YAML_RULES.md §J.
    # ------------------------------------------------------------------
    raw_df = fetch_single_tenor(
        engine=engine,
        curve_family=params.curve_family,
        tenor=params.tenor,
        field_name=field_name_resolved,
        start_date=start_date,
        instrument_type=_LINKER_INSTRUMENT_TYPE,
        end_date=anchor,
    )

    if raw_df.empty:
        return {
            "error": (
                f"No linker real-yield data found for "
                f"curve_family='{params.curve_family}', tenor='{params.tenor}', "
                f"field='{field_name_resolved}' since {start_date.isoformat()} "
                f"(instrument_type='{_LINKER_INSTRUMENT_TYPE}').  "
                "If you passed a nominal sovereign curve_family (e.g. 'UST', "
                "'DE_BUND'), use the sovereign_bonds get_yield_levels_tool "
                "instead — this tool only returns inflation-linker rows.  "
                "Otherwise verify the linker curve family and tenor exist "
                "in the database (see "
                "rates_agent/playbooks/inflation_indexed_bonds.yml for the "
                "ingested universe)."
            )
        }

    # ------------------------------------------------------------------
    # 3. Clean
    # ------------------------------------------------------------------
    clean_df = clean_single_series(raw_df, ffill_limit=ffill_limit)
    if clean_df.empty:
        return {
            "error": (
                f"All values were null after cleaning for linker "
                f"'{params.curve_family}' {params.tenor}."
            )
        }
    real_yields = clean_df["field_value"]

    # ------------------------------------------------------------------
    # 4. Metrics — single canonical primitive shared with sovereign + OIS
    # ------------------------------------------------------------------
    metrics_dict = compute_level_metrics(real_yields, **metrics_kwargs)

    # ------------------------------------------------------------------
    # 5. Observation count is the LOOKBACK_DAYS-window count, NOT the
    #    full series length.  Anchor the cutoff to the data's latest
    #    observation date, NOT date.today() — linker daily feeds can
    #    lag wall-clock by several business days, so anchoring to
    #    date.today() would silently shrink the displayed window when
    #    data is stale.  This matches OIS rate_level's anchoring;
    #    sovereign yield_levels currently anchors to date.today() —
    #    that inconsistency is documented in both tools'
    #    planned_extensions.
    # ------------------------------------------------------------------
    as_of_date = real_yields.index[-1].date()
    cutoff = pd.Timestamp(as_of_date - timedelta(days=params.lookback_days))
    display_real_yields = real_yields.loc[real_yields.index >= cutoff]
    obs_count = len(display_real_yields)

    if obs_count == 0:
        return {
            "error": (
                f"No observations within the last {params.lookback_days} days "
                f"for linker '{params.curve_family}' {params.tenor}."
            )
        }

    # ------------------------------------------------------------------
    # 6. Build output (snapshot)
    # ------------------------------------------------------------------
    metrics = RealYieldLevelMetrics(
        as_of_date=as_of_date.strftime("%Y-%m-%d"),
        curve_family=params.curve_family,
        tenor=params.tenor,
        real_yield_pct=metrics_dict["current_value"],
        daily_change_bps=metrics_dict["period_changes"]["daily"],
        weekly_change_bps=metrics_dict["period_changes"]["weekly"],
        monthly_change_bps=metrics_dict["period_changes"]["monthly"],
        z_score=metrics_dict["z_score"],
        high_252d_pct=metrics_dict["high"],
        low_252d_pct=metrics_dict["low"],
        percentile_252d=metrics_dict["percentile"],
        observation_count=obs_count,
    )

    # ------------------------------------------------------------------
    # 7. Canonical TimeSeries.  Built from the SAME ``display_real_yields``
    #    slice the snapshot was computed against, rounded with the SAME
    #    ``yield_round_decimals`` convention the snapshot used, so the
    #    snapshot's ``real_yield_pct`` equals
    #    ``time_series.rows[-1].value`` STRICTLY (not just within
    #    tolerance).  Same pattern as sovereign yield_levels and OIS
    #    rate_level.
    # ------------------------------------------------------------------
    yield_round_decimals = metrics_kwargs["yield_round_decimals"]
    canonical_series = _build_canonical_real_yield_time_series(
        display_real_yields,
        curve_family=params.curve_family,
        tenor=params.tenor,
        yield_round_decimals=yield_round_decimals,
    )

    output = RealYieldLevelOutput(
        current_metrics=metrics,
        time_series=canonical_series,
    )
    return output.model_dump()


# ============================================================================
# CANONICAL TIME-SERIES BUILDER
# ============================================================================

def _build_canonical_real_yield_time_series(
    display_real_yields: pd.Series,
    *,
    curve_family: str,
    tenor: str,
    yield_round_decimals: int,
) -> TimeSeries:
    """Convert the cleaned, in-window linker real-yield series into the
    canonical ``TimeSeries`` shape with closed-enum units (PERCENT).

    Each row is rounded with ``yield_round_decimals`` so the canonical
    series and the snapshot's ``real_yield_pct`` agree byte-for-byte
    at the latest row (the snapshot uses the SAME convention via
    ``compute_level_metrics(yield_round_decimals=...)``).

    Naming convention: ``<curve_family_lower>_<tenor_lower>_real_yield``
    — the ``_real_yield`` suffix distinguishes this from nominal
    sovereign yield series when both end up in the same operator panel
    downstream.
    """
    series_name = f"{curve_family.lower()}_{tenor.lower()}_real_yield"
    rows = [
        TimeSeriesRow(
            date=ts.strftime("%Y-%m-%d"),
            value=(
                round(float(v), yield_round_decimals)
                if pd.notna(v) else None
            ),
        )
        for ts, v in display_real_yields.items()
    ]
    return TimeSeries(
        series_name=series_name,
        units=TimeSeriesUnits.PERCENT,
        description=(
            f"Historical linker real-yield levels for {curve_family} "
            f"{tenor} over the display window (cleaned, ffill'd; "
            f"rounded to {yield_round_decimals} decimals to match "
            "current_metrics.real_yield_pct exactly at the latest row)."
        ),
        rows=rows,
    )
