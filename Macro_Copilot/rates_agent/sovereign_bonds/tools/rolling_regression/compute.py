"""
compute.py — Rolling-OLS regression of one sovereign yield series on
one or more regressor yield series.

Second tool of the v6 sprint (after zscore_custom).  First tool with
nested Pydantic input shapes (`SeriesSpec`, `List[SeriesSpec]`),
proving the FastMCP nested-wrapper pattern that subsequent sprint
tools (beta_adjusted_spread, half_life, pca_yield_curve,
yield_change_attribution_pca, yield_change_decomposition_simple) can
inherit.

Determinism boundary (A13)
--------------------------
The user-facing surface exposes ONLY the central knob
(`regression_window_days`) plus the structural specs
(`target_spec`, `regressor_specs`, `lookback_days`).  Every other
methodology decision (`min_periods`, `add_constant`, `solver`,
`condition_number_warning_threshold`, `ffill_limit_days`, rounding,
default field) is sourced from the bundled `config.yaml` and is NOT
user-overridable in V1.  See docs/architecture/tool_architecture.md.

Honest-placeholder guards
-------------------------
Two structural choices use the `NotImplementedError` honest-placeholder
pattern (per ``MethodologyMeta.planned_extensions`` doc):
  - `add_constant=False` → no-intercept regression is a sibling tool,
    not a parameter.  compute() raises NotImplementedError naming the
    sibling-tool path.
  - `regression_solver != 'numpy_lstsq_default'` → other solvers are
    sibling tools.  compute() raises NotImplementedError.

Cross-layer contract
--------------------
The schema accepts `regression_window_days >= 10`, but the YAML's
`regression_min_periods` (30 in V1) determines the smallest window
the rolling fit will actually accept.  When the user-supplied
window is below the YAML's min_periods, compute() returns a
controlled error envelope rather than letting the underlying
primitive raise.  Mirrors the zscore_custom small-window guard;
the FastAPI helper at `_tool_result_or_raise` maps the resulting
error string to HTTP 422 (per the user_input_phrases list).

Output shape
------------
Snapshot follows the unit-honest naming convention
(`current_alpha_pct`, `current_residual_pct`, `current_betas` as a
dict-of-floats, `current_r_squared` as a unitless ratio,
`current_condition_flag` as a 0/1 int).  Time-series payloads use
the shared `TimeSeries` schema (units enum: percent / ratio /
count).  Existing 5 migrated tools keep their bespoke wire formats;
the retro-fit is deferred.

Boundary rounding discipline
----------------------------
Every `safe_float` call at metric assembly passes `decimals=`
explicitly so a YAML override above safe_float's default of 4
cannot be silently truncated — same boundary-shadowing class as
the field_name fix from b2605ee and the z_score_round_decimals fix
from the butterfly migration's Codex review.

Test seam
---------
`fetch_single_tenor` and `date` are imported here at module level;
tests patch them via
`patch("rates_agent.sovereign_bonds.tools.rolling_regression.compute.X")`.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.sovereign_bonds.tools.rolling_regression.schemas import (
    RollingRegressionInput,
    RollingRegressionMetrics,
    RollingRegressionOutput,
)
from shared.analytics.levels import clean_single_series
from shared.analytics.rates_fetch import fetch_single_tenor
from shared.analytics.regression import rolling_ols
from shared.analytics.spreads import safe_float
from shared.config import ToolConfig, load_tool_config
from shared.schemas import (
    SeriesSpec,
    TimeSeries,
    TimeSeriesRow,
    TimeSeriesUnits,
)


# Bundled config — public symbol so external callers can build a
# ToolConfig from the same source the tool uses.
CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# Locked structural-choice values.  compute() raises
# NotImplementedError if YAML drifts from these.
_LOCKED_ADD_CONSTANT: bool = True
_LOCKED_SOLVER: str = "numpy_lstsq_default"


# ============================================================================
# CONFIG → KWARGS RESOLVER
# ============================================================================

def _conventions_from_config(config: ToolConfig) -> dict:
    """Pull the methodology kwargs rolling_regression needs from a
    ToolConfig.  Centralised so the same lookups run identically
    across compute() and any future caller (e.g. beta_adjusted_spread,
    which wraps this primitive)."""
    add_const = config.convention_value("add_constant")
    if add_const is not _LOCKED_ADD_CONSTANT:
        raise NotImplementedError(
            f"add_constant={add_const!r} is documented in this tool's "
            f"config.yaml as a future-supported value (see "
            f"methodology.planned_extensions) but is not yet implemented.  "
            f"V1 supports only add_constant=True.  No-intercept "
            f"regression is a sibling tool, not a parameter — different "
            f"statistical model, different output schema (alpha is "
            f"forced to zero rather than estimated).  Either restore "
            f"add_constant: true or implement the sibling tool path "
            f"documented in planned_extensions."
        )

    solver = config.convention_value("regression_solver")
    if solver != _LOCKED_SOLVER:
        raise NotImplementedError(
            f"regression_solver={solver!r} is documented in this tool's "
            f"config.yaml as a future-supported value (see "
            f"methodology.planned_extensions) but is not yet implemented.  "
            f"V1 supports only regression_solver='{_LOCKED_SOLVER}' "
            f"(numpy.linalg.lstsq with rcond=None — SVD-based, "
            f"numerically stable on near-rank-deficient X).  Other "
            f"solvers are sibling tools, not configurable in V1."
        )

    return {
        "regression_min_periods": config.convention_value(
            "regression_min_periods"
        ),
        "add_constant": add_const,
        "condition_number_warning_threshold": config.convention_value(
            "condition_number_warning_threshold"
        ),
        "buffer_multiplier": config.convention_value(
            "regression_buffer_multiplier"
        ),
        "ffill_limit": config.convention_value("ffill_limit_days"),
        "beta_round_decimals": config.convention_value("beta_round_decimals"),
        "alpha_round_decimals": config.convention_value(
            "alpha_round_decimals"
        ),
        "residual_round_decimals": config.convention_value(
            "residual_round_decimals"
        ),
        "r_squared_round_decimals": config.convention_value(
            "r_squared_round_decimals"
        ),
        "default_field_name": config.convention_value("default_field_name"),
    }


# ============================================================================
# HELPERS
# ============================================================================

def _series_label(spec: SeriesSpec) -> str:
    """Canonical label for a SeriesSpec — used as the key in
    ``current_betas`` and as the basis of each beta time series'
    series_name."""
    return f"{spec.curve_family}_{spec.tenor}"


def _resolve_field_name(
    spec_field: Optional[str], default_field: str,
) -> str:
    """Sentinel resolution: caller's explicit value wins; None falls
    through to the YAML default."""
    return spec_field if spec_field is not None else default_field


def _fetch_one_series(
    *,
    engine: Engine,
    spec: SeriesSpec,
    default_field: str,
    ffill_limit: int,
    start_date: date,
) -> Tuple[pd.Series, str]:
    """Fetch + clean one series; return (clean Series, resolved field
    name).  Raises a ValueError on missing data — caller turns it into
    the controlled error envelope."""
    field = _resolve_field_name(spec.field_name, default_field)
    raw = fetch_single_tenor(
        engine=engine,
        curve_family=spec.curve_family,
        tenor=spec.tenor,
        field_name=field,
        start_date=start_date,
    )
    if raw.empty:
        raise ValueError(
            f"No data found for curve_family='{spec.curve_family}', "
            f"tenor='{spec.tenor}', field='{field}' since "
            f"{start_date.isoformat()}.  Verify the curve family + "
            "tenor exist in the database."
        )
    cleaned = clean_single_series(raw, ffill_limit=ffill_limit)
    if cleaned.empty:
        raise ValueError(
            f"All values were null after cleaning for "
            f"'{spec.curve_family}' {spec.tenor} (field={field})."
        )
    return cleaned["field_value"], field


def _build_panel(
    target_clean: pd.Series,
    target_label: str,
    regressor_cleans: List[pd.Series],
    regressor_labels: List[str],
) -> pd.DataFrame:
    """Inner-join target + regressors on the date index and return a
    wide DataFrame indexed by date with columns
    [target_label, *regressor_labels].

    Inner join (dropna across all columns) is the standard choice for
    a regression panel — any row missing a regressor or target value
    is unusable for that t.
    """
    panel = pd.concat(
        [target_clean.rename(target_label)]
        + [r.rename(label) for r, label in zip(regressor_cleans, regressor_labels)],
        axis=1,
    )
    return panel.dropna()


def _round_or_none(value: Optional[float], decimals: int) -> Optional[float]:
    """Round a value to `decimals` decimal places, returning None on
    None or NaN."""
    if value is None:
        return None
    try:
        f = float(value)
        if math.isnan(f):
            return None
        return round(f, decimals)
    except (TypeError, ValueError):
        return None


# ============================================================================
# PUBLIC API
# ============================================================================

def calculate_rolling_regression(
    engine: Engine,
    params: RollingRegressionInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Run a rolling OLS regression of params.target_spec on
    params.regressor_specs over a user-supplied window.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine connected to TimescaleDB.
    params : RollingRegressionInput
        Validated input.  Each spec's ``field_name=None`` resolves to
        the YAML's ``default_field_name``.
    config : ToolConfig, optional
        Bundled config.yaml is auto-loaded when None.  Tests pass a
        custom ToolConfig to exercise convention overrides.

    Returns
    -------
    dict
        Serialised ``RollingRegressionOutput``, or ``{"error": "..."}``
        on recoverable failure.
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    conv = _conventions_from_config(config)
    regression_window_days = params.regression_window_days
    regression_min_periods = conv["regression_min_periods"]
    add_constant = conv["add_constant"]
    cond_threshold = conv["condition_number_warning_threshold"]
    buffer_multiplier = conv["buffer_multiplier"]
    ffill_limit = conv["ffill_limit"]
    beta_dec = conv["beta_round_decimals"]
    alpha_dec = conv["alpha_round_decimals"]
    residual_dec = conv["residual_round_decimals"]
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
                f"regression_min_periods in rolling_regression/config.yaml "
                f"if the desk has decided a smaller min_periods is "
                f"acceptable."
            )
        }

    # ------------------------------------------------------------------
    # 1. Fetch each series
    # ------------------------------------------------------------------
    # Fetch buffer = regression_window_days * regression_buffer_multiplier
    # calendar days beyond lookback_days, so the rolling fit is fully
    # populated from the first displayed trading day.  Multiplier is
    # YAML-driven (calibration, not structural identity) — see
    # `regression_buffer_multiplier` in config.yaml.
    buffer_calendar_days = int(regression_window_days * buffer_multiplier)
    start_date = date.today() - timedelta(
        days=params.lookback_days + buffer_calendar_days
    )

    target_label = _series_label(params.target_spec)
    regressor_labels = [_series_label(r) for r in params.regressor_specs]

    try:
        target_clean, _target_field = _fetch_one_series(
            engine=engine,
            spec=params.target_spec,
            default_field=default_field,
            ffill_limit=ffill_limit,
            start_date=start_date,
        )
        regressor_cleans: List[pd.Series] = []
        for spec in params.regressor_specs:
            r_clean, _r_field = _fetch_one_series(
                engine=engine,
                spec=spec,
                default_field=default_field,
                ffill_limit=ffill_limit,
                start_date=start_date,
            )
            regressor_cleans.append(r_clean)
    except ValueError as exc:
        return {"error": str(exc)}

    # ------------------------------------------------------------------
    # 2. Align panel (inner-join on shared date index)
    # ------------------------------------------------------------------
    panel = _build_panel(
        target_clean=target_clean,
        target_label=target_label,
        regressor_cleans=regressor_cleans,
        regressor_labels=regressor_labels,
    )
    if panel.empty:
        return {
            "error": (
                f"After aligning target='{target_label}' with regressors "
                f"{regressor_labels}, no overlapping observations remain."
            )
        }

    y = panel[target_label]
    X = panel[regressor_labels]

    # ------------------------------------------------------------------
    # 3. Rolling OLS
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
    # 4. Trim to the requested display lookback (wall-clock anchored —
    #    matches sovereign convention; OIS-anchoring divergence is
    #    documented under planned_extensions in other sovereign tools'
    #    configs and deferred to a separate cross-tool PR)
    # ------------------------------------------------------------------
    cutoff = pd.Timestamp(date.today() - timedelta(days=params.lookback_days))
    display_idx = panel.index[panel.index >= cutoff]
    if len(display_idx) == 0:
        return {
            "error": (
                f"No observations within the last {params.lookback_days} "
                f"days for target='{target_label}' with regressors "
                f"{regressor_labels}."
            )
        }

    display_alpha = fit.alpha.loc[display_idx]
    display_betas = fit.betas.loc[display_idx]
    display_residual = fit.residual.loc[display_idx]
    display_r2 = fit.r_squared.loc[display_idx]
    display_flag = fit.condition_flag.loc[display_idx]

    # ------------------------------------------------------------------
    # 5. Build current_metrics.  Latest row is the last entry of the
    #    display index.
    # ------------------------------------------------------------------
    last = display_idx[-1]
    current_alpha_raw = display_alpha.loc[last]
    current_betas_raw = display_betas.loc[last]
    current_residual_raw = display_residual.loc[last]
    current_r2_raw = display_r2.loc[last]
    current_flag_raw = int(display_flag.loc[last])

    current_betas: Dict[str, Optional[float]] = {
        label: _round_or_none(current_betas_raw[label], beta_dec)
        for label in regressor_labels
    }

    metrics = RollingRegressionMetrics(
        as_of_date=last.strftime("%Y-%m-%d"),
        target_label=target_label,
        regressor_labels=list(regressor_labels),
        current_alpha_pct=_round_or_none(current_alpha_raw, alpha_dec),
        current_betas=current_betas,
        current_residual_pct=_round_or_none(current_residual_raw, residual_dec),
        current_r_squared=_round_or_none(current_r2_raw, r2_dec),
        current_condition_flag=current_flag_raw,
        regression_window_days_used=regression_window_days,
        regression_min_periods_used=regression_min_periods,
        add_constant_used=add_constant,
        observation_count=int(len(display_idx)),
    )

    # ------------------------------------------------------------------
    # 6. Build TimeSeries payloads
    # ------------------------------------------------------------------
    def _to_rows(series: pd.Series, decimals: int) -> List[TimeSeriesRow]:
        return [
            TimeSeriesRow(
                date=ts_idx.strftime("%Y-%m-%d"),
                value=_round_or_none(val, decimals),
            )
            for ts_idx, val in series.items()
        ]

    def _to_int_rows(series: pd.Series) -> List[TimeSeriesRow]:
        return [
            TimeSeriesRow(date=ts_idx.strftime("%Y-%m-%d"), value=float(int(val)))
            for ts_idx, val in series.items()
        ]

    time_series_betas: List[TimeSeries] = []
    for label in regressor_labels:
        beta_series = display_betas[label]
        time_series_betas.append(
            TimeSeries(
                series_name=f"{target_label}_on_{label}_beta_"
                            f"{regression_window_days}d",
                units=TimeSeriesUnits.RATIO,
                description=(
                    f"Rolling OLS beta of {target_label} on {label} "
                    f"(window={regression_window_days}d, "
                    f"min_periods={regression_min_periods}, "
                    f"add_constant={add_constant})."
                ),
                rows=_to_rows(beta_series, beta_dec),
            )
        )

    time_series_alpha = TimeSeries(
        series_name=f"{target_label}_alpha_{regression_window_days}d",
        units=TimeSeriesUnits.PERCENT,
        description=(
            f"Rolling OLS intercept (yield-percent) for {target_label} "
            f"on {regressor_labels}."
        ),
        rows=_to_rows(display_alpha, alpha_dec),
    )

    time_series_residual = TimeSeries(
        series_name=f"{target_label}_residual_{regression_window_days}d",
        units=TimeSeriesUnits.PERCENT,
        description=(
            f"Rolling OLS residual (yield-percent, NOT bps) for "
            f"{target_label} on {regressor_labels}."
        ),
        rows=_to_rows(display_residual, residual_dec),
    )

    time_series_r_squared = TimeSeries(
        series_name=f"{target_label}_r_squared_{regression_window_days}d",
        units=TimeSeriesUnits.RATIO,
        description=(
            f"In-window R² for the rolling OLS fit "
            f"(window={regression_window_days}d)."
        ),
        rows=_to_rows(display_r2, r2_dec),
    )

    time_series_condition_flag = TimeSeries(
        series_name=f"{target_label}_condition_flag_"
                    f"{regression_window_days}d",
        units=TimeSeriesUnits.COUNT,
        description=(
            f"Per-row condition-number quality flag (0 = OK, 1 = design "
            f"matrix near-singular, coefficients suppressed for that t)."
        ),
        rows=_to_int_rows(display_flag),
    )

    output = RollingRegressionOutput(
        current_metrics=metrics,
        time_series_betas=time_series_betas,
        time_series_alpha=time_series_alpha,
        time_series_residual=time_series_residual,
        time_series_r_squared=time_series_r_squared,
        time_series_condition_flag=time_series_condition_flag,
    )
    return output.model_dump()
