"""compute.py — GARCH conditional-vol regime (Bucket-2 model-state).

Fits a GARCH(1,1) to one sovereign tenor's yield CHANGES (via the
finance-blind fit_garch operator) and surfaces the CURRENT conditional-vol
state: the latest conditional vol, its regime label (calm / normal /
elevated by historical percentile), and the fitted GARCH model state
(persistence α+β, ω/α/β, near-integrated) read back from the operator's
lineage.  The persistence is the GARCH-specific read — "is high vol sticky
or transient" — that no diff→fit_garch→percentile chain can surface (the
model state lives in lineage; no operator hoists it).

THE FINANCE this primitive owns: the series choice + differencing, naming
the vol regime, surfacing the model state.  ZERO statistical math — GARCH
is the operator (shared/quant/garch.py).

DESCRIBE, NOT FORECAST: the CURRENT in-sample conditional vol
(fit_scope='full_sample', look-ahead — disclosed).  NEVER σ_{t+1}.

Test seam: ``fetch_instrument_panel`` is imported at module level for
monkeypatching.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.sovereign_bonds.tools.rates_vol_regime.schemas import (
    RatesVolRegimeCurrentMetrics,
    RatesVolRegimeInput,
    RatesVolRegimeOutput,
    RatesVolRegimeTimeSeriesRow,
)
from shared.analytics.panel_assembly import fetch_instrument_panel
from shared.analytics.rates_fetch import latest_trade_date
from shared.artifacts.lineage import Lineage, PrimitiveStep
from shared.artifacts.missingness import RawNoCleaning
from shared.artifacts.types import Series
from shared.artifacts.units import TimeSeriesUnits as ArtifactUnits
from shared.config import ToolConfig, load_tool_config
from shared.config.operator_config import load_operator_config
from shared.operators.fit_garch import (
    CONFIG_PATH as GARCH_CONFIG_PATH,
    FitGarchError,
    FitGarchParams,
    fit_garch,
)
from shared.schemas import TimeSeries, TimeSeriesRow
from shared.schemas import TimeSeriesUnits as SchemaUnits


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

_TOOL_NAME = "calculate_rates_vol_regime_tool"
_TOOL_VERSION = "1.0.0"
_BPS_PER_PCT = 100.0  # percent → basis points (a mathematical truth)


def _round(value: Optional[float], decimals: int) -> Optional[float]:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return None
    return round(float(value), decimals)


def _round_sig(value: Optional[float], sig: int) -> Optional[float]:
    """Round to ``sig`` SIGNIFICANT figures (not decimal places).

    GARCH ω is a variance-scale parameter — on daily-percent yield
    changes it is ~1e-5..1e-6, so a fixed-decimal round (4 dp) collapses
    a genuinely-positive ω to a dishonest 0.0 (P5).  Significant-figure
    rounding keeps it scale-honest while staying deterministic.
    """
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return None
    v = float(value)
    if v == 0.0:
        return 0.0
    digits = sig - 1 - int(math.floor(math.log10(abs(v))))
    return round(v, digits)


def calculate_rates_vol_regime(
    engine: Engine,
    params: RatesVolRegimeInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """GARCH conditional-vol regime of one sovereign tenor.

    Returns ``output.model_dump()`` on success or ``{"error": "..."}`` on a
    recoverable failure — never raises.
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    default_field = config.convention_value("default_field_name")
    ffill_limit = int(config.convention_value("ffill_limit_days"))
    return_method = config.convention_value("return_method")
    ann_days = int(config.convention_value("vol_annualization_days"))
    calm_max = float(config.convention_value("vol_calm_percentile_max"))
    elevated_min = float(config.convention_value("vol_elevated_percentile_min"))
    label_calm = config.convention_value("regime_label_calm")
    label_normal = config.convention_value("regime_label_normal")
    label_elevated = config.convention_value("regime_label_elevated")
    round_dec = int(config.convention_value("vol_round_decimals"))

    field = params.field_name or default_field
    col = f"{params.curve_family}_{params.tenor}"

    # ------------------------------------------------------------------
    # 1. Fetch the tenor's yields.
    # ------------------------------------------------------------------
    # Anchor to the latest available trade_date (not date.today()) so the
    # window resolves to real data when ingestion lags; falls back to today
    # only when the curve has no rows (e.g. mocked engine=None in unit tests).
    anchor = (
        latest_trade_date(engine, curve_family=params.curve_family)
        or date.today()
    )
    start_date = anchor - timedelta(days=int(params.lookback_days))
    raw = fetch_instrument_panel(
        engine=engine,
        leg_specs=[(params.curve_family, params.tenor, field)],
        start_date=start_date,
        end_date=None,
        ffill_limit_days=ffill_limit,
    )
    if raw.empty or col not in raw.columns or raw[col].isna().all():
        return {
            "error": (
                f"No observations for {params.curve_family} {params.tenor} "
                f"(field {field}) since {start_date.isoformat()}."
            )
        }

    # ------------------------------------------------------------------
    # 2. Difference into a yield-change Series (the operator runs on the
    #    series as given — differenced=False — so we difference first).
    # ------------------------------------------------------------------
    levels = raw[col].astype(float)
    returns = levels.diff() if return_method == "diff" else levels.pct_change()
    returns.name = f"{col}_dyield"
    # DISCLOSURE (P5): ``raw`` came from fetch_instrument_panel with
    # ffill_limit_days=5, so the LEVELS were forward-filled before this
    # difference.  The change series below is stamped RawNoCleaning because
    # the DIFFERENCING adds no further imputation — the level ffill is
    # disclosed in config.yaml::ffill_limit_days.  (The strictly-honest
    # platform label CleanSingleSeriesV1(ffill_limit=5) is a deferred
    # cross-tool convention fix — see tmp/fable_build_defer_log.md.)
    as_of_iso = returns.index[-1].strftime("%Y-%m-%d")
    returns_series = Series(
        series_key=f"{col}_dyield",
        payload=returns,
        units=ArtifactUnits.PERCENT,
        frequency=None,
        missingness_policy=RawNoCleaning(),
        lineage=Lineage.from_steps([
            PrimitiveStep.build(
                name=_TOOL_NAME, version=_TOOL_VERSION,
                params={
                    "curve_family": params.curve_family,
                    "tenor": params.tenor, "field_name": field,
                    "return_method": return_method,
                    "n_observations": int(len(returns)),
                },
                tool_config_hash=config.conventions_hash(),
                output_field="dyield", as_of_date=as_of_iso,
                tool_config_path=str(CONFIG_PATH),
            )
        ]),
    )

    # ------------------------------------------------------------------
    # 3. Fit the GARCH (explicit operator config — strict PR9).
    # ------------------------------------------------------------------
    garch_config = load_operator_config(GARCH_CONFIG_PATH)
    try:
        vol_series = fit_garch(
            returns_series, params=FitGarchParams(), config=garch_config,
        )
    except FitGarchError as exc:
        return {"error": f"rates_vol_regime: GARCH fit failed — {exc}"}

    cond_vol_pct = vol_series.payload           # daily vol in percent (NaN edges)
    model = vol_series.lineage.steps[-1].params
    persistence = float(model["persistence"])
    # The fit reached a genuine interior MLE (the operator refuses a
    # start-pinned / non-convergent fit before we get here, so this is
    # always False on the success path) — surfaced for P5 completeness so
    # the model state read from lineage is honest about fit quality.
    boundary_stuck = bool(model.get("boundary_stuck", False))

    # ------------------------------------------------------------------
    # 4. The FINANCE: current vol (bps), historical percentile, regime
    #    label.  Read the GARCH model state from the operator's lineage.
    # ------------------------------------------------------------------
    finite = cond_vol_pct.dropna()
    if finite.empty:
        return {"error": "rates_vol_regime: the GARCH fit produced no "
                "conditional-vol values."}
    latest_pct = float(finite.iloc[-1])
    latest_daily_bps = latest_pct * _BPS_PER_PCT
    latest_annual_bps = latest_daily_bps * math.sqrt(ann_days)
    # Percentile of today's vol within its own history (0–100).
    vol_percentile = float((finite <= latest_pct).mean() * 100.0)
    if vol_percentile <= calm_max:
        regime_label = label_calm
    elif vol_percentile >= elevated_min:
        regime_label = label_elevated
    else:
        regime_label = label_normal

    cond_vol_bps = cond_vol_pct * _BPS_PER_PCT
    ts_rows: List[TimeSeriesRow] = []
    bespoke: List[RatesVolRegimeTimeSeriesRow] = []
    for dt, val in cond_vol_bps.items():
        ds = dt.strftime("%Y-%m-%d")
        v = None if pd.isna(val) else round(float(val), round_dec)
        ts_rows.append(TimeSeriesRow(date=ds, value=v))
        bespoke.append(RatesVolRegimeTimeSeriesRow(date=ds, conditional_vol_bps=v))

    time_series_conditional_vol = TimeSeries(
        series_name=f"{col}_garch_conditional_vol",
        units=SchemaUnits.BPS,
        description=(
            f"GARCH(1,1) in-sample conditional volatility of {col} yield "
            "changes, in daily bps; full-sample fit; descriptive (NOT a "
            "forecast)."
        ),
        rows=ts_rows,
    )

    current_metrics = RatesVolRegimeCurrentMetrics(
        as_of_date=as_of_iso,
        curve_family=params.curve_family,
        tenor=params.tenor,
        current_conditional_vol_daily_bps=_round(latest_daily_bps, round_dec),
        current_conditional_vol_annualized_bps=_round(latest_annual_bps, round_dec),
        vol_percentile=_round(vol_percentile, round_dec),
        regime_label=regime_label,
        persistence=_round(persistence, round_dec),
        # ω is variance-scale (≪ α, β): round to significant figures so a
        # genuinely-positive ~1e-5..1e-6 value is never shown as 0.0 (P5).
        omega=_round_sig(float(model["omega"]), max(round_dec, 4)),
        alpha=_round(float(model["alpha"]), round_dec),
        beta=_round(float(model["beta"]), round_dec),
        mu=_round(float(model["mu"]), round_dec),
        loglik=_round(float(model["loglik"]), round_dec),
        near_integrated=bool(model["near_integrated"]),
        is_vol_mean_reverting=bool(persistence < 1.0),
        fit_scope=str(model["fit_scope"]),
        n_core_rows=int(model.get("n_core_rows", len(finite))),
        methodology_label=config.methodology.what_it_does.strip(),
    )

    disclosures = [
        f"fit_scope={model['fit_scope']} — full-sample in-sample conditional "
        "vol; describes the CURRENT vol state, NOT a forecast of σ(t+1).",
        f"Regime by the historical percentile of the conditional vol "
        f"(<= {calm_max} calm, >= {elevated_min} elevated) — a description, "
        "not a trade signal.",
        f"persistence α+β = {_round(persistence, 4)} "
        f"({'near-integrated / sticky' if model['near_integrated'] else 'mean-reverting'} "
        "vol); GARCH math is the fit_garch operator.",
        "GARCH MLE = interior optimum (the fit_garch operator standardises "
        "the residuals + runs a fixed deterministic multi-start and refuses "
        "a start-pinned / non-convergent fit, so the persistence above is "
        "the maximum-likelihood estimate, not the optimiser start).",
    ]
    if boundary_stuck:  # defensive — the operator refuses before here
        disclosures.append(
            "WARNING: the GARCH optimiser ended pinned at its start point "
            "(degenerate likelihood); the model state is NOT a reliable MLE."
        )

    output = RatesVolRegimeOutput(
        current_metrics=current_metrics,
        time_series=bespoke,
        time_series_conditional_vol=time_series_conditional_vol,
        methodology_disclosures=disclosures,
    )
    return output.model_dump()


__all__ = ["CONFIG_PATH", "calculate_rates_vol_regime"]
