"""
compute.py — Config-driven sovereign butterfly (curvature) tool
================================================================

Refactor of the legacy ``rates_agent/sovereign_bonds/tools/butterfly.py``
into the per-tool-folder pattern.  Every methodology choice
(z-score window/min_periods/ddof, fill limit, daily-change offset,
trailing range window, bps/z-score rounding precisions, default field)
flows from the bundled ``config.yaml`` rather than from module-level
constants or hardcoded literals.

Backward compatibility
----------------------
The default convention values in ``config.yaml`` reproduce the legacy
hardcoded values bit-for-bit:

    z_score_window_days        = 252      (was Z_SCORE_WINDOW)
    z_score_min_periods        = 60       (was Z_SCORE_MIN_PERIODS)
    z_score_ddof               = 1        (was implicit pandas default)
    z_score_buffer_multiplier  = 1.5      (was hardcoded inline)
    daily_change_offset_rows   = 2        (was hardcoded iloc[-2])
    trailing_range_window_days = 252      (was Z_SCORE_WINDOW reused)
    ffill_limit_days           = 5        (was pivot_and_align default)
    bps_round_decimals         = 2        (was hardcoded .round(2))
    z_score_round_decimals     = 4        (was rolling_zscore default)
    default_field_name         = YLD_YTM_MID  (was Pydantic default)

So callers using the bundled config see byte-identical output to the
legacy tool.  The signature gains an optional ``config`` kwarg
(auto-loaded when None); ``field_name`` is now resolved against the
YAML when the caller passes None.

Design decision: trailing-range "252d" clipping bug preserved
-------------------------------------------------------------
Before computing the trailing high/low/percentile, this compute() trims
the series to ``lookback_days`` (line near the bottom of the function).
That means when ``lookback_days`` corresponds to fewer than 252 trading
days, the ``high_252d_bps`` / ``low_252d_bps`` / ``percentile_252d``
fields are computed against a window smaller than 252.  The SQL parity
fixture (tests/test_butterfly_sql_validation.py) encodes the same
behaviour, so flipping it here would silently re-target the parity
check.  This migration is a *pure refactor*; the bug is documented under
``methodology.planned_extensions`` in config.yaml and will be fixed in
a separate PR that also regenerates the SQL parity fixture.

Buffer sizing — defensive
-------------------------
The fetch buffer uses
``max(z_score_window_days, trailing_range_window_days) * z_score_buffer_multiplier``
even though the two windows are equal at 252 today.  When the bug above
is eventually fixed and the trailing window decoupled from the z-score
window, the fetch will already be sized correctly to populate both.

Honest placeholder
------------------
``trailing_range_window_days`` is locked at 252 in V1.  The output
schema's field names embed the number; changing the convention without
renaming the wire fields would silently lie about what the percentile
is computed against.  The compute path raises ``NotImplementedError``
if this is set to anything else; see ``methodology.planned_extensions``
in the YAML for the path to making it configurable.

Test seam
---------
``fetch_tenor_group`` and ``date`` are imported here at module level;
tests patch them via
``patch("rates_agent.sovereign_bonds.tools.butterfly.compute.X")``.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.sovereign_bonds.tools.butterfly.schemas import (
    ButterflyInput,
    ButterflyCurrentMetrics,
    ButterflyOutput,
    ButterflyTimeSeriesRow,
)
from shared.analytics.levels import (
    delta_bps,
    trailing_high_low_percentile,
)
from shared.analytics.rates_fetch import fetch_tenor_group, latest_trade_date
from shared.analytics.spreads import (
    compute_spread_bps,
    pivot_and_align_tenors,
    rolling_zscore,
    safe_float,
)
from shared.config import ToolConfig, load_tool_config
from shared.schemas import TimeSeries, TimeSeriesRow, TimeSeriesUnits


# Bundled config — public symbol so external callers (mcp_server,
# REST routes, tests) can build a ToolConfig from the same source the
# tool uses.
CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# Trailing-range window is wire-frozen at 252 in V1; see schemas.py
# and config.yaml's ``planned_extensions``.  This guard fires at the
# convention layer so an editor of config.yaml gets a clear error
# rather than producing percentiles labelled "252d" against a different
# window.
_FROZEN_TRAILING_WINDOW: int = 252


# ============================================================================
# CONFIG → KWARGS RESOLVER
# ============================================================================

def _conventions_from_config(config: ToolConfig) -> dict:
    """Pull the methodology kwargs the butterfly compute() needs from
    a ToolConfig.  Centralised so future callers (e.g. a batch surface
    that loads the same config.yaml) use identical resolution.

    Raises NotImplementedError if ``trailing_range_window_days`` is set
    to anything other than 252 — see the wire-freeze rationale in the
    module docstring.
    """
    trailing = config.convention_value("trailing_range_window_days")
    if trailing != _FROZEN_TRAILING_WINDOW:
        raise NotImplementedError(
            f"trailing_range_window_days={trailing!r} is documented in this "
            f"tool's config.yaml as a future-supported value (see "
            f"methodology.planned_extensions) but is not yet implemented.  "
            f"V1 supports only {_FROZEN_TRAILING_WINDOW} because the output "
            f"field names (high_252d_bps, low_252d_bps, percentile_252d) "
            f"embed that number on the wire.  Either restore the value to "
            f"{_FROZEN_TRAILING_WINDOW} or implement the schema rename + "
            f"frontend update documented in planned_extensions."
        )

    return {
        "z_window": config.convention_value("z_score_window_days"),
        "z_min_periods": config.convention_value("z_score_min_periods"),
        "z_ddof": config.convention_value("z_score_ddof"),
        "z_round_decimals": config.convention_value("z_score_round_decimals"),
        "buffer_multiplier": config.convention_value("z_score_buffer_multiplier"),
        "ffill_limit": config.convention_value("ffill_limit_days"),
        "daily_offset_rows": config.convention_value("daily_change_offset_rows"),
        "trailing_window": trailing,
        "bps_round_decimals": config.convention_value("bps_round_decimals"),
    }


# ============================================================================
# PUBLIC API
# ============================================================================

def calculate_butterfly(
    engine: Engine,
    params: ButterflyInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """
    Calculate the 3-point butterfly (curvature) on a sovereign curve.

    butterfly_bps = (2 × belly − short − long) × 100

    Returns the current butterfly with daily change, rolling z-score,
    trailing 252-day high/low/percentile, the two component wing
    spreads (belly−short, long−belly), and each leg's latest yield,
    plus the full butterfly+z_score time series for charting.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine connected to TimescaleDB.
    params : ButterflyInput
        Validated input.  ``field_name=None`` resolves against the
        YAML's ``default_field_name`` convention.
    config : ToolConfig, optional
        Bundled config.yaml is auto-loaded when None.  Tests pass a
        custom ToolConfig to exercise convention overrides.

    Returns
    -------
    dict
        Serialized ``ButterflyOutput``, or ``{"error": "..."}`` on
        recoverable failure.
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    # ------------------------------------------------------------------
    # Pull conventions
    # ------------------------------------------------------------------
    conv = _conventions_from_config(config)
    z_window = conv["z_window"]
    z_min_periods = conv["z_min_periods"]
    z_ddof = conv["z_ddof"]
    z_round_decimals = conv["z_round_decimals"]
    buffer_multiplier = conv["buffer_multiplier"]
    ffill_limit = conv["ffill_limit"]
    daily_offset_rows = conv["daily_offset_rows"]
    trailing_window = conv["trailing_window"]
    bps_round_decimals = conv["bps_round_decimals"]
    default_field_name = config.convention_value("default_field_name")

    # Resolve field_name: caller's explicit value wins; None falls
    # through to the YAML default.
    field_name_resolved = (
        params.field_name if params.field_name is not None else default_field_name
    )

    # ------------------------------------------------------------------
    # 1. Date window (defensive: buffer against the *larger* of the
    #    z-window and trailing-window so neither stat starves on the
    #    first displayed row, even if a future config decouples them)
    # ------------------------------------------------------------------
    buffer_calendar_days = int(max(z_window, trailing_window) * buffer_multiplier)
    # Anchor to the latest available trade_date (not date.today()) so the
    # window resolves to real data when ingestion lags; falls back to today
    # only when the curve has no rows (e.g. mocked engine=None in unit tests).
    anchor = (
        params.as_of_date
        or latest_trade_date(engine, curve_family=params.curve_family)
        or date.today()
    )
    start_date = anchor - timedelta(
        days=params.lookback_days + buffer_calendar_days
    )

    # ------------------------------------------------------------------
    # 2. Fetch (3 tenors on one curve)
    # ------------------------------------------------------------------
    raw_df = fetch_tenor_group(
        engine=engine,
        curve_family=params.curve_family,
        tenors=[params.short_tenor, params.belly_tenor, params.long_tenor],
        field_name=field_name_resolved,
        start_date=start_date,
        end_date=anchor,
    )

    if raw_df.empty:
        return {
            "error": (
                f"No data found for curve_family='{params.curve_family}', "
                f"tenors=['{params.short_tenor}', '{params.belly_tenor}', "
                f"'{params.long_tenor}'], field='{field_name_resolved}' "
                f"since {start_date.isoformat()}.  "
                "Please verify the curve family and tenors exist in the database."
            )
        }

    # ------------------------------------------------------------------
    # 3. Validate all three tenors are present
    # ------------------------------------------------------------------
    available_tenors = set(raw_df["tenor"].unique())
    required = {params.short_tenor, params.belly_tenor, params.long_tenor}
    missing = required - available_tenors
    if missing:
        return {
            "error": (
                f"Missing tenor data for {missing} in "
                f"curve_family='{params.curve_family}'.  "
                f"Available tenors in the query window: {sorted(available_tenors)}."
            )
        }

    # ------------------------------------------------------------------
    # 4. Pivot → wide format (date × tenor) and align holiday gaps
    # ------------------------------------------------------------------
    wide = pivot_and_align_tenors(
        raw_df,
        required_tenors=(params.short_tenor, params.belly_tenor, params.long_tenor),
        ffill_limit=ffill_limit,
    )

    if wide.empty:
        return {
            "error": (
                f"After aligning dates for '{params.short_tenor}', "
                f"'{params.belly_tenor}', and '{params.long_tenor}' on "
                f"'{params.curve_family}', no overlapping observations remain."
            )
        }

    # ------------------------------------------------------------------
    # 5. Compute butterfly + wing spreads + z-score
    # ------------------------------------------------------------------
    s = wide[params.short_tenor]
    b = wide[params.belly_tenor]
    l = wide[params.long_tenor]

    # Butterfly = 2 × belly − short − long (in bps).  Single expression;
    # extracting it would be over-engineering.
    wide["butterfly_bps"] = ((2 * b - s - l) * 100).round(bps_round_decimals)

    # Wing spreads reuse the shared 2-leg spread helper (rounded per YAML).
    wide["wing_short_bps"] = compute_spread_bps(
        wide,
        minuend_col=params.belly_tenor,
        subtrahend_col=params.short_tenor,
        round_decimals=bps_round_decimals,
    )
    wide["wing_long_bps"] = compute_spread_bps(
        wide,
        minuend_col=params.long_tenor,
        subtrahend_col=params.belly_tenor,
        round_decimals=bps_round_decimals,
    )

    # Rolling z-score on the butterfly series (already in bps).
    wide["z_score"] = rolling_zscore(
        wide["butterfly_bps"],
        window=z_window,
        min_periods=z_min_periods,
        ddof=z_ddof,
        round_decimals=z_round_decimals,
    )

    # ------------------------------------------------------------------
    # 6. Trim to requested display lookback
    # ------------------------------------------------------------------
    cutoff = pd.Timestamp(anchor - timedelta(days=params.lookback_days))
    display_df = wide.loc[wide.index >= cutoff].copy()

    if display_df.empty:
        return {
            "error": (
                f"No observations within the last {params.lookback_days} days "
                f"for '{params.curve_family}' {params.short_tenor}/"
                f"{params.belly_tenor}/{params.long_tenor} butterfly."
            )
        }

    # ------------------------------------------------------------------
    # 7. Build current_metrics
    # ------------------------------------------------------------------
    latest = display_df.iloc[-1]
    bfly_series = display_df["butterfly_bps"]

    current_butterfly = safe_float(
        latest["butterfly_bps"], decimals=bps_round_decimals,
    )

    # Daily change on the butterfly series (already bps → plain subtract).
    # `daily_offset_rows=2` means iloc[-1] vs iloc[-2] = 1 trading day back.
    daily_change = delta_bps(
        latest["butterfly_bps"],
        bfly_series.iloc[-daily_offset_rows] if len(bfly_series) >= daily_offset_rows else None,
        decimals=bps_round_decimals,
    )

    # Trailing high/low/percentile of the butterfly (bps scale).  Note:
    # see the docstring's "clipping" note — bfly_series is the trimmed
    # display series.  Preserving legacy parity in this PR.
    high, low, percentile = trailing_high_low_percentile(
        bfly_series, window=trailing_window, decimals=bps_round_decimals,
    )

    butterfly_label = (
        f"{params.short_tenor.replace('Y', '')}s"
        f"{params.belly_tenor.replace('Y', '')}s"
        f"{params.long_tenor.replace('Y', '')}s"
    )

    metrics = ButterflyCurrentMetrics(
        as_of_date=latest.name.strftime("%Y-%m-%d"),
        curve_family=params.curve_family,
        butterfly_label=butterfly_label,
        current_butterfly_bps=current_butterfly,
        daily_change_bps=daily_change,
        # Pass z_round_decimals so a YAML override above 4 isn't
        # silently truncated by safe_float's default of 4 — same
        # boundary-shadowing class of bug as the field_name fix.
        current_z_score=safe_float(latest.get("z_score"), decimals=z_round_decimals),
        rolling_window_days=z_window,
        high_252d_bps=high,
        low_252d_bps=low,
        percentile_252d=percentile,
        wing_short_bps=safe_float(
            latest.get("wing_short_bps"), decimals=bps_round_decimals,
        ),
        wing_long_bps=safe_float(
            latest.get("wing_long_bps"), decimals=bps_round_decimals,
        ),
        short_tenor_yield=safe_float(latest.get(params.short_tenor)),
        belly_tenor_yield=safe_float(latest.get(params.belly_tenor)),
        long_tenor_yield=safe_float(latest.get(params.long_tenor)),
    )

    # ------------------------------------------------------------------
    # 8. Build time_series (legacy bespoke shape — wire-frozen for the
    #    frontend) AND the two canonical TimeSeries
    #    (time_series_butterfly for the BPS history and
    #    time_series_zscore for the Z_SCORE history) for the upcoming
    #    primitive-to-operator bridge.  All three share the same
    #    display_df rows so they cannot drift.
    # ------------------------------------------------------------------
    ts_rows = [
        ButterflyTimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            butterfly_bps=round(row.butterfly_bps, bps_round_decimals),
            # Same boundary-rounding fix as current_z_score above.
            z_score=safe_float(row.z_score, decimals=z_round_decimals),
        )
        for row in display_df.itertuples()
    ]
    canonical_butterfly = _build_canonical_butterfly_series(
        display_df,
        curve_family=params.curve_family,
        short_tenor=params.short_tenor,
        belly_tenor=params.belly_tenor,
        long_tenor=params.long_tenor,
        bps_round_decimals=bps_round_decimals,
    )
    canonical_zscore = _build_canonical_butterfly_zscore_series(
        display_df,
        curve_family=params.curve_family,
        short_tenor=params.short_tenor,
        belly_tenor=params.belly_tenor,
        long_tenor=params.long_tenor,
        z_round_decimals=z_round_decimals,
    )

    output = ButterflyOutput(
        current_metrics=metrics,
        time_series=ts_rows,
        time_series_butterfly=canonical_butterfly,
        time_series_zscore=canonical_zscore,
    )
    return output.model_dump()


def _build_canonical_butterfly_series(
    display_df: pd.DataFrame,
    *,
    curve_family: str,
    short_tenor: str,
    belly_tenor: str,
    long_tenor: str,
    bps_round_decimals: int,
) -> TimeSeries:
    """Convert the display DataFrame's butterfly column into the
    canonical ``TimeSeries`` shape (closed-enum BPS units).

    Naming convention:
    ``<curve_family_lower>_<short>_<belly>_<long>_butterfly``.
    """
    series_name = (
        f"{curve_family.lower()}_"
        f"{short_tenor.lower()}_{belly_tenor.lower()}_"
        f"{long_tenor.lower()}_butterfly"
    )
    rows = [
        TimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            value=round(float(row.butterfly_bps), bps_round_decimals),
        )
        for row in display_df.itertuples()
    ]
    return TimeSeries(
        series_name=series_name,
        units=TimeSeriesUnits.BPS,
        description=(
            f"Butterfly ({long_tenor} − 2*{belly_tenor} + {short_tenor}) "
            f"on {curve_family} over the displayed window."
        ),
        rows=rows,
    )


def _build_canonical_butterfly_zscore_series(
    display_df: pd.DataFrame,
    *,
    curve_family: str,
    short_tenor: str,
    belly_tenor: str,
    long_tenor: str,
    z_round_decimals: int,
) -> TimeSeries:
    """Convert the display DataFrame's z_score column into the
    canonical ``TimeSeries`` shape (closed-enum Z_SCORE units).

    Naming convention:
    ``<curve_family_lower>_<short>_<belly>_<long>_zscore``.
    Values match ``time_series[i].z_score`` 1-to-1 (rounded via the
    same ``z_score_round_decimals`` convention applied to the bespoke
    field) so the two series cannot drift.
    """
    series_name = (
        f"{curve_family.lower()}_"
        f"{short_tenor.lower()}_{belly_tenor.lower()}_"
        f"{long_tenor.lower()}_zscore"
    )
    rows = [
        TimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            value=safe_float(row.z_score, decimals=z_round_decimals),
        )
        for row in display_df.itertuples()
    ]
    return TimeSeries(
        series_name=series_name,
        units=TimeSeriesUnits.Z_SCORE,
        description=(
            f"Rolling z-score of the {long_tenor}/{belly_tenor}/"
            f"{short_tenor} butterfly on {curve_family} vs its own "
            "trailing window."
        ),
        rows=rows,
    )
