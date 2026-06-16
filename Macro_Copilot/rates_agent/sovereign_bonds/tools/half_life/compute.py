"""
compute.py — OU / AR(1) half-life of mean reversion.

Fourth tool of the v6 sprint (after zscore_custom, rolling_regression,
and beta_adjusted_spread).  Built on the
``shared.analytics.stats.ou_half_life`` primitive — the OU fit math
has a single authoritative implementation that future tools (Vasicek
extension, exact-MLE sibling, regime-switching OU) can also share or
extend.

Determinism boundary (A13)
--------------------------
The user-facing surface exposes ONE union input
(series_spec / pair_spec / pasted_series) plus `lookback_days` for
the DB-backed paths.  Every methodology decision (`min_observations`,
`confidence_level`, `min_abs_beta_for_half_life`, `ffill_limit_days`,
all rounding decimals, default field) is sourced from the bundled
`config.yaml` and is NOT user-overridable in V1.  See
docs/architecture/tool_architecture.md.

Cross-layer contract — controlled error envelope
------------------------------------------------
The OU primitive raises ``ValueError`` when the cleaned series has
fewer than `min_observations` rows.  compute() catches and turns
into the standard error envelope, with the phrase shape
("series has N non-NaN observations after dropna; the OU primitive
requires at least M") that maps to HTTP 422 via
detail.py's user_input_phrases list — same client-error class as
zscore_custom and rolling_regression's small-window guards.

Unit-honest output
------------------
The snapshot uses the ``_native`` suffix on values whose units come
from the input (yield-percent for series_spec; bps for pair_spec;
caller-declared for pasted_series).  ``series_units`` declares what
those native units are.  Same boundary-rounding discipline as the
rest of the v6 sprint: rounding decimals chosen by series_units (4
for percent, 2 for bps, etc.), and applied via _round_or_none() so
a YAML override above 4 cannot be silently truncated by safe_float's
default.

Test seam
---------
``fetch_single_tenor`` and ``date`` are imported here at module
level; tests patch them via
``patch("rates_agent.sovereign_bonds.tools.half_life.compute.X")``.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.sovereign_bonds.tools.half_life.schemas import (
    HalfLifeInput,
    HalfLifeMetrics,
    HalfLifeOutput,
)
from shared.analytics.levels import clean_single_series
from shared.analytics.rates_fetch import fetch_single_tenor, latest_trade_date
from shared.analytics.stats import ou_half_life
from shared.config import ToolConfig, load_tool_config
from shared.schemas import (
    PairSpec,
    PastedTimeSeries,
    SeriesSpec,
    TimeSeriesUnits,
)


# Bundled config — public symbol so external callers can build a
# ToolConfig from the same source the tool uses.
CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# ============================================================================
# CONFIG → KWARGS RESOLVER
# ============================================================================

def _conventions_from_config(config: ToolConfig) -> dict:
    """Pull the methodology kwargs half_life needs from a ToolConfig."""
    return {
        "min_observations": config.convention_value("min_observations"),
        "confidence_level": config.convention_value("confidence_level"),
        "min_abs_beta_for_half_life": config.convention_value(
            "min_abs_beta_for_half_life"
        ),
        "ffill_limit": config.convention_value("ffill_limit_days"),
        "half_life_round_decimals": config.convention_value(
            "half_life_round_decimals"
        ),
        "ou_beta_round_decimals": config.convention_value(
            "ou_beta_round_decimals"
        ),
        "r_squared_round_decimals": config.convention_value(
            "r_squared_round_decimals"
        ),
        "yield_round_decimals": config.convention_value("yield_round_decimals"),
        "bps_round_decimals": config.convention_value("bps_round_decimals"),
        "default_field_name": config.convention_value("default_field_name"),
    }


# ============================================================================
# HELPERS
# ============================================================================

def _round_or_none(value: Any, decimals: int) -> Optional[float]:
    """Round a value to `decimals` places, returning None on None/NaN."""
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
    return explicit if explicit is not None else default


def _native_round_decimals(units: TimeSeriesUnits, conv: dict) -> int:
    """Map the input series's units to the right rounding decimals
    knob.  PERCENT → yield_round_decimals; BPS → bps_round_decimals;
    everything else → 4 (a sensible default for ratio / z_score).
    Future units can extend this map without touching compute()."""
    if units == TimeSeriesUnits.PERCENT:
        return conv["yield_round_decimals"]
    if units == TimeSeriesUnits.BPS:
        return conv["bps_round_decimals"]
    # Z_SCORE / RATIO / PCT_RANK / FACTOR_LEVEL / COUNT — display 4d.
    return 4


def _build_series_from_series_spec(
    *, engine: Engine, spec: SeriesSpec, default_field: str,
    ffill_limit: int, start_date: date, end_date: Optional[date] = None,
) -> tuple[pd.Series, str, TimeSeriesUnits]:
    """Fetch + clean a single sovereign yield series.  Returns
    (clean Series, label, units)."""
    field = _resolve_field_name(spec.field_name, default_field)
    raw = fetch_single_tenor(
        engine=engine,
        curve_family=spec.curve_family,
        tenor=spec.tenor,
        field_name=field,
        start_date=start_date,
        end_date=end_date,
    )
    if raw.empty:
        raise ValueError(
            f"No data found for curve_family='{spec.curve_family}', "
            f"tenor='{spec.tenor}', field='{field}' since "
            f"{start_date.isoformat()}."
        )
    cleaned = clean_single_series(raw, ffill_limit=ffill_limit)
    if cleaned.empty:
        raise ValueError(
            f"All values were null after cleaning for "
            f"'{spec.curve_family}' {spec.tenor} (field={field})."
        )
    label = f"{spec.curve_family}_{spec.tenor}"
    return cleaned["field_value"], label, TimeSeriesUnits.PERCENT


def _build_series_from_pair_spec(
    *, engine: Engine, spec: PairSpec, default_field: str,
    ffill_limit: int, start_date: date, end_date: Optional[date] = None,
) -> tuple[pd.Series, str, TimeSeriesUnits]:
    """Fetch both legs and produce ``(cf1 − cf2) * 100`` in bps —
    matching cross_market_spread's direction convention.  Returns
    (spread Series, label, units=BPS)."""
    field = _resolve_field_name(spec.field_name, default_field)

    def fetch_leg(cf: str) -> pd.Series:
        raw = fetch_single_tenor(
            engine=engine, curve_family=cf, tenor=spec.tenor,
            field_name=field, start_date=start_date, end_date=end_date,
        )
        if raw.empty:
            raise ValueError(
                f"No data found for curve_family='{cf}', "
                f"tenor='{spec.tenor}', field='{field}' since "
                f"{start_date.isoformat()}."
            )
        cleaned = clean_single_series(raw, ffill_limit=ffill_limit)
        if cleaned.empty:
            raise ValueError(
                f"All values were null after cleaning for '{cf}' "
                f"{spec.tenor} (field={field})."
            )
        return cleaned["field_value"]

    leg1 = fetch_leg(spec.cf1)
    leg2 = fetch_leg(spec.cf2)
    # Inner-join on date and compute spread in bps.
    panel = pd.concat([leg1.rename("cf1"), leg2.rename("cf2")], axis=1).dropna()
    if panel.empty:
        raise ValueError(
            f"After aligning '{spec.cf1}' and '{spec.cf2}' at "
            f"tenor='{spec.tenor}', no overlapping observations remain."
        )
    spread = (panel["cf1"] - panel["cf2"]) * 100.0
    label = f"{spec.cf1}-{spec.cf2}_{spec.tenor}"
    return spread, label, TimeSeriesUnits.BPS


def _build_series_from_pasted(
    spec: PastedTimeSeries,
) -> tuple[pd.Series, str, TimeSeriesUnits]:
    """Convert a PastedTimeSeries to a date-indexed pandas Series."""
    if not spec.rows:
        raise ValueError("pasted_series has no rows")
    dates = [pd.Timestamp(r.date) for r in spec.rows]
    values = [r.value for r in spec.rows]
    s = pd.Series(values, index=pd.DatetimeIndex(dates), dtype=float)
    return s, spec.series_name, spec.units


# ============================================================================
# PUBLIC API
# ============================================================================

def calculate_half_life(
    engine: Engine,
    params: HalfLifeInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Fit OU/AR(1) on the input series and return the half-life
    snapshot.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine.  Unused when params.pasted_series is
        provided (caller already supplied the data).
    params : HalfLifeInput
        Validated input.  Exactly one of series_spec / pair_spec /
        pasted_series is set (enforced by HalfLifeInput's
        model_validator).
    config : ToolConfig, optional
        Bundled config.yaml is auto-loaded when None.

    Returns
    -------
    dict
        Serialised ``HalfLifeOutput``, or ``{"error": "..."}`` on
        recoverable failure.
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    conv = _conventions_from_config(config)
    min_obs = conv["min_observations"]
    confidence_level = conv["confidence_level"]
    min_abs_beta = conv["min_abs_beta_for_half_life"]
    ffill_limit = conv["ffill_limit"]
    half_life_dec = conv["half_life_round_decimals"]
    beta_dec = conv["ou_beta_round_decimals"]
    r2_dec = conv["r_squared_round_decimals"]
    default_field = conv["default_field_name"]

    # ------------------------------------------------------------------
    # Build the series from whichever input variant is set
    # ------------------------------------------------------------------
    # Only the DB-backed paths (series_spec / pair_spec) fetch by trade_date,
    # so the date-window anchor is computed INSIDE those branches, filtered to
    # the leg(s) about to be fetched.  Anchoring to the latest available
    # trade_date (not date.today()) keeps the window resolving to real data
    # when ingestion lags (weekend / holiday / stale snapshot); it falls back
    # to today only when the leg has no rows.  The pasted_series path does NOT
    # fetch — it carries the caller's own dates — so it is left untouched.
    try:
        if params.series_spec is not None:
            anchor = (
                params.as_of_date
                or latest_trade_date(
                    engine,
                    curve_family=params.series_spec.curve_family,
                    tenor=params.series_spec.tenor,
                    field_name=_resolve_field_name(
                        params.series_spec.field_name, default_field
                    ),
                )
                or date.today()
            )
            start_date = anchor - timedelta(days=params.lookback_days)
            series, label, units = _build_series_from_series_spec(
                engine=engine, spec=params.series_spec,
                default_field=default_field,
                ffill_limit=ffill_limit, start_date=start_date,
                end_date=anchor,
            )
        elif params.pair_spec is not None:
            # Two legs (cf1, cf2) inner-joined at the same tenor.  Anchor on
            # cf1 — both legs share the tenor and field, so cf1's latest
            # trade_date is a sound window start for the pair.
            anchor = (
                params.as_of_date
                or latest_trade_date(
                    engine,
                    curve_family=params.pair_spec.cf1,
                    tenor=params.pair_spec.tenor,
                    field_name=_resolve_field_name(
                        params.pair_spec.field_name, default_field
                    ),
                )
                or date.today()
            )
            start_date = anchor - timedelta(days=params.lookback_days)
            series, label, units = _build_series_from_pair_spec(
                engine=engine, spec=params.pair_spec,
                default_field=default_field,
                ffill_limit=ffill_limit, start_date=start_date,
                end_date=anchor,
            )
        else:
            # pasted_series — input model_validator ensures it's set.  No
            # fetch: the caller's pasted dates are used as-is (no anchoring).
            assert params.pasted_series is not None
            series, label, units = _build_series_from_pasted(
                params.pasted_series
            )
    except ValueError as exc:
        return {"error": str(exc)}

    # ------------------------------------------------------------------
    # Fit OU; the primitive raises ValueError on min_observations
    # violation — turn into the controlled error envelope so the
    # FastAPI helper maps to HTTP 422 via the user_input_phrases list.
    # ------------------------------------------------------------------
    try:
        fit = ou_half_life(
            series,
            min_observations=min_obs,
            confidence_level=confidence_level,
            min_abs_beta_for_half_life=min_abs_beta,
        )
    except ValueError as exc:
        msg = str(exc)
        # Reword the primitive's message into the
        # "is smaller than the YAML's" phrase shape so detail.py's
        # user_input_phrases captures it as 422.
        if "requires at least" in msg:
            return {
                "error": (
                    f"series length is smaller than the YAML's "
                    f"min_observations={min_obs}.  Either supply a "
                    f"longer series (lookback_days, or a longer "
                    f"pasted_series), or edit min_observations in "
                    f"half_life/config.yaml if the desk has decided a "
                    f"smaller floor is acceptable.  Underlying detail: "
                    f"{msg}"
                )
            }
        return {"error": msg}

    # ------------------------------------------------------------------
    # Map units → native rounding decimals at the output boundary
    # ------------------------------------------------------------------
    native_dec = _native_round_decimals(units, conv)

    metrics = HalfLifeMetrics(
        as_of_date=series.dropna().index[-1].strftime("%Y-%m-%d"),
        series_label=label,
        series_units=units,
        point_estimate_mean_reverting=fit.point_estimate_mean_reverting,
        unit_root_rejected=fit.unit_root_rejected,
        unit_root_pvalue=_round_or_none(fit.unit_root_pvalue, r2_dec),
        half_life_days=_round_or_none(fit.half_life, half_life_dec),
        half_life_ci_lower_days=_round_or_none(
            fit.half_life_ci_lower, half_life_dec
        ),
        half_life_ci_upper_days=_round_or_none(
            fit.half_life_ci_upper, half_life_dec
        ),
        long_run_mean_native=_round_or_none(fit.long_run_mean, native_dec),
        current_value_native=float(_round_or_none(fit.current_value, native_dec)),
        current_deviation_native=_round_or_none(
            fit.current_deviation, native_dec
        ),
        beta=float(_round_or_none(fit.beta, beta_dec)),
        beta_ci_lower=_round_or_none(fit.beta_ci_lower, beta_dec),
        beta_ci_upper=_round_or_none(fit.beta_ci_upper, beta_dec),
        r_squared=_round_or_none(fit.r_squared, r2_dec),
        observation_count=int(fit.observation_count),
        confidence_level_used=fit.confidence_level_used,
    )

    output = HalfLifeOutput(current_metrics=metrics)
    return output.model_dump()
