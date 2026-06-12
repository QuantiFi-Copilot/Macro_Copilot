"""stationarity_adf — augmented Dickey–Fuller unit-root test.

Finance-blind ``statistical_relationship`` operator (the A5
diagnostics family — the series' relationship with its own past;
granger_causality/cointegration are the two-series siblings).
Runs the augmented Dickey–Fuller test
(``statsmodels.tsa.stattools.adfuller``) on one Series and emits the
ADF TEST STATISTIC as a ``ScalarMetric`` in RATIO units (the A5
family's emit-the-statistic convention, set by granger_causality's
F-stat and cointegration's ADF stat).  More negative = stronger
evidence against a unit root; the p-value, the lag actually used, and
the 1/5/10% critical values ride in lineage for interpretation.

DESIGN LOCKS (OPR7, lineage-stamped): ``regression='c'`` (intercept
only — the canonical specification for level series; 'ct'/'n' are
declared planned extensions) and ``autolag='AIC'`` (automatic lag
selection).  One canonical ADF flavour — variants would fracture
replay comparability for no analytical gain at this surface.

NaN POLICY (A5 family ruling): full-sample DESCRIPTIVE test — NaN rows
are dropped and the test runs on the remaining observations.  Because
the ADF regression is LAG-BASED, dropped INTERIOR rows splice
non-adjacent observations together; the interior-drop count is
recorded separately in lineage (``n_interior_dropped``) so a gappy
input is auditable, never silent.

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind classical test; zero finance
    vocabulary.
  - OPR2      : ONE output artifact type — always a ``ScalarMetric``.
  - OPR8      : ``params: Optional[...] = None`` (zero knobs; locks
    documented + stamped).
  - OPR9      : one ``Series`` in, one ``ScalarMetric`` out.
  - OPR10     : one ``OperatorStep``; statistic + p-value + lags +
    critical values + drop counts ride in params.
  - OPR11     : RATIO units (a t-type test statistic is
    dimensionless); input units recorded in lineage.
  - OPR13     : typed ``StationarityAdfError`` refusals: non-Series
    input; fewer than 12 finite observations (the family floor — the
    auxiliary regression needs headroom over the auto-selected lag);
    zero variance or zero FIRST-DIFFERENCE variance (constant series
    / perfect ramp — either way the regression is degenerate); a
    non-finite statistic.
  - OPR14     : pure + rerun-deterministic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
from statsmodels.tsa.stattools import adfuller

from shared.artifacts.lineage import OperatorStep, sanitize_params_for_lineage
from shared.artifacts.types import ScalarMetric, Series
from shared.artifacts.units import TimeSeriesUnits
from shared.config.operator_config import (
    OperatorConfig,
    _check_config_identity,
    load_operator_config,
)
from shared.operators.stationarity_adf.schemas import StationarityAdfParams


_OPERATOR_NAME = "stationarity_adf"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

# DESIGN-LOCKED test specification (OPR7), recorded in lineage.
_REGRESSION = "c"     # intercept only — the canonical level-series spec
_AUTOLAG = "AIC"      # automatic lag selection

# A5 family floor: the auxiliary regression needs headroom over the
# auto-selected lag; statsmodels accepts shorter inputs but the
# resulting statistic is degenerate.
_MIN_OBS = 12


class StationarityAdfError(ValueError):
    """Raised by ``stationarity_adf`` on a recoverable user-facing
    failure (a ``ValueError`` subclass, OPR13)."""


def stationarity_adf(
    series: Series,
    *,
    params: Optional[StationarityAdfParams] = None,
    config: Optional[OperatorConfig] = None,
) -> ScalarMetric:
    """Augmented Dickey–Fuller statistic of ``series``.

    Parameters
    ----------
    series :
        The input ``Series`` (any units; the test statistic is
        dimensionless).
    params :
        Optional ``StationarityAdfParams`` (no fields in v1).
        ``None`` is equivalent.
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    ScalarMetric
        The ADF test statistic (more negative = stronger evidence
        against a unit root) in RATIO units; p-value, used lag, the
        critical values and the NaN-drop audit ride in lineage.

    Raises
    ------
    StationarityAdfError
        The input is not a ``Series``; fewer than 12 finite
        observations; zero variance (constant series) or zero
        first-difference variance (perfect ramp — the regression is
        degenerate); or a non-finite statistic.
    """
    # ------------------------------------------------------------------
    # 1. Load config + identity check — OPR12.
    # ------------------------------------------------------------------
    if config is None:
        config = load_operator_config(_CONFIG_PATH)
    _check_config_identity(config, _OPERATOR_NAME, _OPERATOR_VERSION)

    # ------------------------------------------------------------------
    # 2. Resolve params — OPR8 (no fields in v1).
    # ------------------------------------------------------------------
    if params is None:
        params = StationarityAdfParams()

    # ------------------------------------------------------------------
    # 3. Structural-input validation (OPR9 typed I/O; family floor).
    # ------------------------------------------------------------------
    if not isinstance(series, Series):
        raise StationarityAdfError(
            "stationarity_adf: input must be a Series artifact; got "
            f"{type(series).__name__}."
        )

    payload = series.payload
    cleaned = payload.dropna()
    n_total = int(len(payload))
    n_obs = int(len(cleaned))
    n_dropped = n_total - n_obs
    if n_obs < _MIN_OBS:
        raise StationarityAdfError(
            f"stationarity_adf: needs at least {_MIN_OBS} finite "
            f"observations (got {n_obs}) — the auxiliary regression "
            "is degenerate below that."
        )
    # Interior-drop audit (A5 ruling): lag-based test — splicing
    # non-adjacent rows is disclosed, never silent.
    finite_mask = payload.notna().to_numpy()
    first = int(np.argmax(finite_mask))
    last = len(finite_mask) - int(np.argmax(finite_mask[::-1]))
    n_interior_dropped = int((~finite_mask[first:last]).sum())

    values = cleaned.to_numpy(dtype=float)
    if float(np.var(values)) == 0.0:
        raise StationarityAdfError(
            "stationarity_adf: the input has zero variance — the ADF "
            "regression is degenerate on a constant series."
        )
    # Degenerate-regression invariant (critic finding, OPR14 — the
    # granger_causality perfect-fit precedent): constant first
    # differences (a perfect ramp, e.g. cumulative over a constant)
    # make the ADF regressand zero-variance, so the t-statistic is
    # pure float noise — probed: a 120-row ramp emits −8.77/p≈0, a
    # confidently WRONG 'stationary' verdict on a pure trend.  Refuse.
    if float(np.var(np.diff(values))) == 0.0:
        raise StationarityAdfError(
            "stationarity_adf: the input's first differences have "
            "zero variance (a perfect linear ramp) — the ADF "
            "regression is degenerate and its statistic would be "
            "float noise.  Deterministic-trend questions await the "
            "regression='ct' extension."
        )

    # ------------------------------------------------------------------
    # 4. The test (statsmodels; design-locked spec).
    # ------------------------------------------------------------------
    stat, p_value, used_lag, n_reg_obs, critical_values, _icbest = adfuller(
        values, regression=_REGRESSION, autolag=_AUTOLAG,
    )
    stat = float(stat)
    if not np.isfinite(stat):
        raise StationarityAdfError(
            "stationarity_adf: the test produced a non-finite "
            "statistic (numerical failure)."
        )

    # ------------------------------------------------------------------
    # 5. Lineage — one OperatorStep (OPR10); the full test result
    #    rides in params.
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "test_statistic": stat,
        "p_value": float(p_value) if np.isfinite(p_value) else None,
        "regression": _REGRESSION,   # design-locked (OPR7)
        "autolag": _AUTOLAG,         # design-locked (OPR7)
        "lags_used": int(used_lag),
        "n_regression_obs": int(n_reg_obs),
        "critical_values": {
            k: float(v) for k, v in critical_values.items()
        },
        "test_scope": "full_sample",  # descriptive disclosure (P5)
        "n_obs": n_obs,
        "n_dropped": n_dropped,
        "n_interior_dropped": n_interior_dropped,
        "input_units": series.units.value,
        "input_series_key": series.series_key,
    }
    step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params=sanitize_params_for_lineage(step_params),
        input_hashes=(series.lineage.head_hash,),
    )

    return ScalarMetric(
        metric_key=f"stationarity_adf__{series.series_key}",
        value=stat,
        units=TimeSeriesUnits.RATIO,
        lineage=series.lineage.append(step),
    )


__all__ = ["stationarity_adf", "StationarityAdfError"]
