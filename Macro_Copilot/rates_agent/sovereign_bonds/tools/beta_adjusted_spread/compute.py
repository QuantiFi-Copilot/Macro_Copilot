"""
compute.py — Bivariate beta-adjusted RV: rolling OLS of target on
regressor, residual converted to bps with its rolling z-score.

Third tool of the v6 sprint (after zscore_custom and rolling_regression).
beta_adjusted_spread is a flat-input tool that wraps the same
``shared.analytics.regression.rolling_ols`` primitive that
rolling_regression uses, so the bivariate hedge-ratio + residual
math has a single authoritative implementation.

Determinism boundary (A13)
--------------------------
The user-facing surface exposes ONLY the central knob
(`regression_window_days`) plus the structural target / regressor
identifiers.  Every other methodology decision (`min_periods`,
`add_constant`, `solver`, `condition`-threshold,
`regression_buffer_multiplier`, the residual-z-score conventions,
`ffill_limit_days`, all rounding decimals, default field) is
sourced from the bundled `config.yaml` and is NOT user-overridable
in V1.  See docs/architecture/tool_architecture.md.

Honest-placeholder guards
-------------------------
Two structural choices use the `NotImplementedError` honest-placeholder
pattern (mirrors rolling_regression):
  - `add_constant=False` → no-intercept regression is a sibling tool.
  - `regression_solver != 'numpy_lstsq_default'` → other solvers are
    sibling tools.

Cross-layer contract
--------------------
The schema accepts `regression_window_days >= 10`, but the YAML's
`regression_min_periods` (30 in V1) determines the smallest window
the rolling fit will actually accept.  When the user-supplied window
is below the YAML's min_periods, compute() returns a controlled
error envelope rather than letting the underlying primitive raise.
The phrase shape matches detail.py's user_input_phrases list, so the
FastAPI route surfaces this as HTTP 422 (per the helper's status
ladder).

Unit-conversion explicitness
----------------------------
The rolling OLS produces a residual in PERCENT space (because both
target and regressor are yield-percent series).  This compute()
explicitly converts the residual time series to BPS via *100 BEFORE:
  1. Emitting `current_residual_bps` (the wire-frozen field name).
  2. Computing the residual's rolling z-score.
This is the explicit fix that prevented the v6 plan's earlier
mistake of naming a percent-space residual `_bps`.

Defensive buffer sizing
-----------------------
Two windows operate inside this tool: the regression window
(user-supplied) and the residual z-score window (252 in V1).  The
fetch buffer uses
``max(regression_window_days * regression_buffer_multiplier,
     z_score_window_days  * z_score_buffer_multiplier)``
calendar days beyond `lookback_days`, so neither stat starves on the
first displayed trading day.

Boundary rounding discipline
----------------------------
Every `safe_float` / round() at metric assembly passes `decimals=`
explicitly so a YAML override above safe_float's default of 4 cannot
be silently truncated.  All four rounding knobs (beta, alpha, bps
residual, z-score, R²) reach BOTH the snapshot AND the time_series
rows.

Test seam
---------
``fetch_single_tenor`` and ``date`` are imported here at module level;
tests patch them via
``patch("rates_agent.sovereign_bonds.tools.beta_adjusted_spread.compute.X")``.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.sovereign_bonds.tools.beta_adjusted_spread.schemas import (
    BetaAdjustedSpreadInput,
    BetaAdjustedSpreadMetrics,
    BetaAdjustedSpreadOutput,
)
from shared.analytics.levels import clean_single_series
from shared.analytics.rates_fetch import fetch_single_tenor
from shared.analytics.regression import rolling_ols
from shared.analytics.spreads import rolling_zscore, safe_float
from shared.config import ToolConfig, load_tool_config
from shared.schemas import (
    TimeSeries,
    TimeSeriesRow,
    TimeSeriesUnits,
)


# Bundled config — public symbol so external callers (mcp_server,
# REST routes, tests) can build a ToolConfig from the same source the
# tool uses.
CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# Locked structural-choice values.  compute() raises
# NotImplementedError if YAML drifts from these.  Mirrors
# rolling_regression.
_LOCKED_ADD_CONSTANT: bool = True
_LOCKED_SOLVER: str = "numpy_lstsq_default"


# ============================================================================
# CONFIG → KWARGS RESOLVER
# ============================================================================

def _conventions_from_config(config: ToolConfig) -> dict:
    """Pull the methodology kwargs beta_adjusted_spread needs from a
    ToolConfig.  Mirrors rolling_regression's resolver shape and
    additionally pulls the residual-z-score conventions."""
    add_const = config.convention_value("add_constant")
    if add_const is not _LOCKED_ADD_CONSTANT:
        raise NotImplementedError(
            f"add_constant={add_const!r} is documented in this tool's "
            f"config.yaml as a future-supported value (see "
            f"methodology.planned_extensions) but is not yet implemented.  "
            f"V1 supports only add_constant=True.  No-intercept "
            f"regression is a sibling tool, not a parameter."
        )

    solver = config.convention_value("regression_solver")
    if solver != _LOCKED_SOLVER:
        raise NotImplementedError(
            f"regression_solver={solver!r} is documented in this tool's "
            f"config.yaml as a future-supported value (see "
            f"methodology.planned_extensions) but is not yet implemented.  "
            f"V1 supports only regression_solver='{_LOCKED_SOLVER}'."
        )

    return {
        "regression_min_periods": config.convention_value(
            "regression_min_periods"
        ),
        "add_constant": add_const,
        "condition_number_warning_threshold": config.convention_value(
            "condition_number_warning_threshold"
        ),
        "regression_buffer_multiplier": config.convention_value(
            "regression_buffer_multiplier"
        ),
        "z_score_window_days": config.convention_value("z_score_window_days"),
        "z_score_min_periods": config.convention_value("z_score_min_periods"),
        "z_score_ddof": config.convention_value("z_score_ddof"),
        "z_score_buffer_multiplier": config.convention_value(
            "z_score_buffer_multiplier"
        ),
        "ffill_limit": config.convention_value("ffill_limit_days"),
        "bps_round_decimals": config.convention_value("bps_round_decimals"),
        "z_score_round_decimals": config.convention_value(
            "z_score_round_decimals"
        ),
        "beta_round_decimals": config.convention_value("beta_round_decimals"),
        "alpha_round_decimals": config.convention_value(
            "alpha_round_decimals"
        ),
        "r_squared_round_decimals": config.convention_value(
            "r_squared_round_decimals"
        ),
        "default_field_name": config.convention_value("default_field_name"),
    }


# ============================================================================
# HELPERS
# ============================================================================

def _round_or_none(value: Any, decimals: int) -> Optional[float]:
    """Round a value to `decimals` places, returning None on None or
    NaN.  Used at the snapshot-assembly + per-row TimeSeries-build
    surfaces so YAML rounding is authoritative end-to-end."""
    if value is None:
        return None
    try:
        f = float(value)
        if math.isnan(f):
            return None
        return round(f, decimals)
    except (TypeError, ValueError):
        return None


def _resolve_field_name(
    explicit: Optional[str], default: str,
) -> str:
    """Sentinel resolution: caller's explicit value wins; None falls
    through to the YAML default."""
    return explicit if explicit is not None else default


def _fetch_one(
    *, engine: Engine, curve_family: str, tenor: str, field_name: str,
    ffill_limit: int, start_date: date,
) -> pd.Series:
    """Fetch + clean one yield series.  Raises ValueError on missing
    data — caller turns it into the controlled error envelope."""
    raw = fetch_single_tenor(
        engine=engine,
        curve_family=curve_family,
        tenor=tenor,
        field_name=field_name,
        start_date=start_date,
    )
    if raw.empty:
        raise ValueError(
            f"No data found for curve_family='{curve_family}', "
            f"tenor='{tenor}', field='{field_name}' since "
            f"{start_date.isoformat()}.  Verify the curve family + "
            "tenor exist in the database."
        )
    cleaned = clean_single_series(raw, ffill_limit=ffill_limit)
    if cleaned.empty:
        raise ValueError(
            f"All values were null after cleaning for "
            f"'{curve_family}' {tenor} (field={field_name})."
        )
    return cleaned["field_value"]


# ============================================================================
# PUBLIC API
# ============================================================================

def calculate_beta_adjusted_spread(
    engine: Engine,
    params: BetaAdjustedSpreadInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Compute the bivariate beta-adjusted spread.

    Returns the hedge ratio (β), intercept (α, yield-percent), residual
    `target − β·regressor − α` converted to bps, and the bps
    residual's rolling z-score.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine connected to TimescaleDB.
    params : BetaAdjustedSpreadInput
        Validated input.  ``field_name=None`` resolves against the
        YAML's ``default_field_name``.
    config : ToolConfig, optional
        Bundled config.yaml is auto-loaded when None.  Tests pass a
        custom ToolConfig to exercise convention overrides.

    Returns
    -------
    dict
        Serialised ``BetaAdjustedSpreadOutput``, or
        ``{"error": "..."}`` on recoverable failure.
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    conv = _conventions_from_config(config)
    regression_window_days = params.regression_window_days
    regression_min_periods = conv["regression_min_periods"]
    add_constant = conv["add_constant"]
    cond_threshold = conv["condition_number_warning_threshold"]
    regression_buffer_multiplier = conv["regression_buffer_multiplier"]
    z_window = conv["z_score_window_days"]
    z_min_periods = conv["z_score_min_periods"]
    z_ddof = conv["z_score_ddof"]
    z_buffer_multiplier = conv["z_score_buffer_multiplier"]
    ffill_limit = conv["ffill_limit"]
    bps_dec = conv["bps_round_decimals"]
    z_dec = conv["z_score_round_decimals"]
    beta_dec = conv["beta_round_decimals"]
    alpha_dec = conv["alpha_round_decimals"]
    r2_dec = conv["r_squared_round_decimals"]
    default_field = conv["default_field_name"]

    # ------------------------------------------------------------------
    # Cross-layer contract: regression_window_days < regression_min_periods
    # would make every rolling fit emit NaN.  Surface as a controlled
    # error envelope (FastAPI maps this phrase shape to 422).
    # ------------------------------------------------------------------
    if regression_window_days < regression_min_periods:
        return {
            "error": (
                f"regression_window_days={regression_window_days} is "
                f"smaller than the YAML's "
                f"regression_min_periods={regression_min_periods}.  "
                f"With this combination every rolling fit emits NaN.  "
                f"Either request a larger regression_window_days "
                f"(>= {regression_min_periods}), or edit "
                f"regression_min_periods in beta_adjusted_spread/"
                f"config.yaml if the desk has decided a smaller "
                f"min_periods is acceptable."
            )
        }

    # ------------------------------------------------------------------
    # Resolve field_name (sentinel pattern: caller wins; None falls
    # through to YAML default — applies to BOTH legs).
    # ------------------------------------------------------------------
    field_name_resolved = _resolve_field_name(params.field_name, default_field)

    # ------------------------------------------------------------------
    # 1. Date window — defensive: buffer against the LARGER of the
    #    regression-window-buffer and the z-score-window-buffer so
    #    neither stat starves on the first displayed trading day.
    # ------------------------------------------------------------------
    regression_buffer = int(regression_window_days * regression_buffer_multiplier)
    z_score_buffer = int(z_window * z_buffer_multiplier)
    buffer_calendar_days = max(regression_buffer, z_score_buffer)
    start_date = date.today() - timedelta(
        days=params.lookback_days + buffer_calendar_days
    )

    # ------------------------------------------------------------------
    # 2. Fetch
    # ------------------------------------------------------------------
    try:
        target_clean = _fetch_one(
            engine=engine,
            curve_family=params.target_curve_family,
            tenor=params.target_tenor,
            field_name=field_name_resolved,
            ffill_limit=ffill_limit,
            start_date=start_date,
        )
        regressor_clean = _fetch_one(
            engine=engine,
            curve_family=params.regressor_curve_family,
            tenor=params.regressor_tenor,
            field_name=field_name_resolved,
            ffill_limit=ffill_limit,
            start_date=start_date,
        )
    except ValueError as exc:
        return {"error": str(exc)}

    target_label = (
        f"{params.target_curve_family}_{params.target_tenor}"
    )
    regressor_label = (
        f"{params.regressor_curve_family}_{params.regressor_tenor}"
    )

    # ------------------------------------------------------------------
    # 3. Inner-join on the date index
    # ------------------------------------------------------------------
    panel = pd.concat(
        [
            target_clean.rename(target_label),
            regressor_clean.rename(regressor_label),
        ],
        axis=1,
    ).dropna()
    if panel.empty:
        return {
            "error": (
                f"After aligning target='{target_label}' with regressor "
                f"='{regressor_label}', no overlapping observations "
                "remain."
            )
        }

    y = panel[target_label]
    X = panel[[regressor_label]]

    # ------------------------------------------------------------------
    # 4. Rolling OLS (bivariate) via the shared primitive
    # ------------------------------------------------------------------
    fit = rolling_ols(
        y=y,
        X=X,
        window=regression_window_days,
        min_periods=regression_min_periods,
        add_constant=add_constant,
        condition_number_warning_threshold=cond_threshold,
    )

    # ------------------------------------------------------------------
    # 5. Convert residual percent → bps via *100
    # ------------------------------------------------------------------
    # The shared primitive's residual is in y's units (yield-percent
    # for sovereign yields).  beta_adjusted_spread emits the residual
    # in BPS, so multiply by 100 BEFORE the snapshot value, the
    # time_series_residual rows, AND the rolling z-score that's
    # computed off this series.
    residual_bps = fit.residual * 100.0

    # ------------------------------------------------------------------
    # 6. Rolling z-score on the BPS residual
    # ------------------------------------------------------------------
    residual_z = rolling_zscore(
        residual_bps,
        window=z_window,
        min_periods=z_min_periods,
        ddof=z_ddof,
        round_decimals=z_dec,
    )

    # ------------------------------------------------------------------
    # 7. Trim to the requested display lookback (wall-clock anchored)
    # ------------------------------------------------------------------
    cutoff = pd.Timestamp(date.today() - timedelta(days=params.lookback_days))
    display_idx = panel.index[panel.index >= cutoff]
    if len(display_idx) == 0:
        return {
            "error": (
                f"No observations within the last {params.lookback_days} "
                f"days for target='{target_label}' regressor="
                f"'{regressor_label}'."
            )
        }

    display_beta = fit.betas.loc[display_idx, regressor_label]
    display_alpha = fit.alpha.loc[display_idx]
    display_residual_bps = residual_bps.loc[display_idx]
    display_residual_z = residual_z.loc[display_idx]
    display_r2 = fit.r_squared.loc[display_idx]
    display_flag = fit.condition_flag.loc[display_idx]

    # ------------------------------------------------------------------
    # 8. Build current_metrics
    # ------------------------------------------------------------------
    last = display_idx[-1]
    current_beta_raw = display_beta.loc[last]
    current_alpha_raw = display_alpha.loc[last]
    current_residual_bps_raw = display_residual_bps.loc[last]
    current_residual_z_raw = display_residual_z.loc[last]
    current_r2_raw = display_r2.loc[last]
    current_flag_raw = int(display_flag.loc[last])

    spread_label = (
        f"{params.target_curve_family}-{params.regressor_curve_family} "
        f"{params.target_tenor}/{params.regressor_tenor} "
        f"(beta-adjusted, {regression_window_days}d)"
    )

    metrics = BetaAdjustedSpreadMetrics(
        as_of_date=last.strftime("%Y-%m-%d"),
        target_curve_family=params.target_curve_family,
        target_tenor=params.target_tenor,
        regressor_curve_family=params.regressor_curve_family,
        regressor_tenor=params.regressor_tenor,
        spread_label=spread_label,
        current_beta=_round_or_none(current_beta_raw, beta_dec),
        current_alpha_pct=_round_or_none(current_alpha_raw, alpha_dec),
        current_residual_bps=_round_or_none(current_residual_bps_raw, bps_dec),
        current_residual_z_score=_round_or_none(current_residual_z_raw, z_dec),
        current_r_squared=_round_or_none(current_r2_raw, r2_dec),
        current_condition_flag=current_flag_raw,
        regression_window_days_used=regression_window_days,
        regression_min_periods_used=regression_min_periods,
        z_score_window_days_used=z_window,
        add_constant_used=add_constant,
        observation_count=int(len(display_idx)),
    )

    # ------------------------------------------------------------------
    # 9. Build TimeSeries payloads (decimals applied per-row from YAML)
    # ------------------------------------------------------------------
    def _rows(series: pd.Series, decimals: int) -> List[TimeSeriesRow]:
        return [
            TimeSeriesRow(
                date=ts_idx.strftime("%Y-%m-%d"),
                value=_round_or_none(val, decimals),
            )
            for ts_idx, val in series.items()
        ]

    time_series_beta = TimeSeries(
        series_name=f"{target_label}_on_{regressor_label}_beta_"
                    f"{regression_window_days}d",
        units=TimeSeriesUnits.RATIO,
        description=(
            f"Rolling OLS hedge ratio of {target_label} on "
            f"{regressor_label} (window={regression_window_days}d, "
            f"min_periods={regression_min_periods})."
        ),
        rows=_rows(display_beta, beta_dec),
    )

    time_series_residual = TimeSeries(
        series_name=f"{target_label}_minus_{regressor_label}_"
                    f"beta_adjusted_residual_bps_{regression_window_days}d",
        units=TimeSeriesUnits.BPS,
        description=(
            f"Beta-adjusted spread residual in bps: "
            f"({target_label} − β·{regressor_label} − α) × 100, where "
            f"β and α are estimated by rolling OLS over "
            f"{regression_window_days}d."
        ),
        rows=_rows(display_residual_bps, bps_dec),
    )

    time_series_residual_z_score = TimeSeries(
        series_name=f"{target_label}_minus_{regressor_label}_"
                    f"beta_adjusted_residual_zscore_{z_window}d",
        units=TimeSeriesUnits.Z_SCORE,
        description=(
            f"Rolling {z_window}d z-score of the beta-adjusted spread "
            f"residual (bps).  ddof={z_ddof}, "
            f"min_periods={z_min_periods}."
        ),
        rows=_rows(display_residual_z, z_dec),
    )

    output = BetaAdjustedSpreadOutput(
        current_metrics=metrics,
        time_series_beta=time_series_beta,
        time_series_residual=time_series_residual,
        time_series_residual_z_score=time_series_residual_z_score,
    )
    return output.model_dump()
