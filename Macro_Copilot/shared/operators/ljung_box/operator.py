"""ljung_box — Ljung–Box autocorrelation test of a Series.

Finance-blind ``statistical_relationship`` operator (the A5
diagnostics family — the series' relationship with its own past;
``stationarity_adf`` set the family conventions).  Runs the Ljung–Box
Q-test (``statsmodels.stats.diagnostic.acorr_ljungbox``) on one Series
and emits the Q STATISTIC at the FULL horizon as a ``ScalarMetric`` in
RATIO units (the family's emit-the-statistic convention).  The Q
statistic at horizon L jointly tests autocorrelations 1..L — larger =
stronger evidence of autocorrelation; the p-value and the per-lag
trail ride in lineage.

THE COLLAPSE RULE (A5 family ruling): statsmodels returns one (stat,
p-value) row PER lag; the operator emits the LARGEST-horizon row (the
full-spectrum joint test) and records the whole per-lag trail in
lineage — one scalar per call (OPR2), no per-lag variants.

NaN POLICY (A5 family ruling): full-sample DESCRIPTIVE test — NaN rows
dropped; because the Q statistic is LAG-BASED, the interior-drop count
is recorded separately in lineage (``n_interior_dropped``) so a gappy
input is auditable, never silent (the stationarity_adf precedent).

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind classical test; zero finance
    vocabulary.
  - OPR2      : ONE output artifact type — always a ``ScalarMetric``.
  - OPR8      : ``params: Optional[...] = None``; ``lags=None``
    resolves to the classical ``min(10, n_obs // 5)`` rule at
    runtime (lineage-stamped, never silent).
  - OPR9      : one ``Series`` in, one ``ScalarMetric`` out.
  - OPR10     : one ``OperatorStep``; statistic + p-value + the
    per-lag trail + the resolved horizon + drop counts ride in params.
  - OPR11     : RATIO units (a χ²-type statistic is dimensionless);
    input units recorded in lineage.
  - OPR13     : typed ``LjungBoxError`` refusals: non-Series input;
    fewer than ``lags + 2`` finite observations (the family floor);
    zero variance (autocorrelation undefined); a non-finite
    statistic.
  - OPR14     : pure + rerun-deterministic; the RESOLVED lags value
    is what rides in lineage (params lags=None and an explicit equal
    value hash identically).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
from statsmodels.stats.diagnostic import acorr_ljungbox

from shared.artifacts.lineage import OperatorStep, sanitize_params_for_lineage
from shared.artifacts.types import ScalarMetric, Series
from shared.artifacts.units import TimeSeriesUnits
from shared.config.operator_config import (
    OperatorConfig,
    _check_config_identity,
    load_operator_config,
)
from shared.operators.ljung_box.schemas import LjungBoxParams


_OPERATOR_NAME = "ljung_box"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

# The classical auto-horizon rule (A5 family ruling), applied when
# lags is omitted: min(10, n//5), floored at 1.
_AUTO_LAG_CAP = 10
_AUTO_LAG_DIVISOR = 5


class LjungBoxError(ValueError):
    """Raised by ``ljung_box`` on a recoverable user-facing failure (a
    ``ValueError`` subclass, OPR13)."""


def ljung_box(
    series: Series,
    *,
    params: Optional[LjungBoxParams] = None,
    config: Optional[OperatorConfig] = None,
) -> ScalarMetric:
    """Ljung–Box Q statistic of ``series`` at the resolved horizon.

    Parameters
    ----------
    series :
        The input ``Series`` (any units; the statistic is
        dimensionless).
    params :
        Optional ``LjungBoxParams``.  When ``None`` (or when
        ``lags=None``) the horizon resolves to the classical
        ``min(10, n_obs // 5)`` rule.
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    ScalarMetric
        The Q statistic at the full horizon (larger = stronger
        evidence of autocorrelation) in RATIO units; the p-value, the
        per-lag trail, the resolved horizon and the NaN-drop audit
        ride in lineage.

    Raises
    ------
    LjungBoxError
        The input is not a ``Series``; fewer than ``lags + 2`` finite
        observations; zero variance; or a non-finite statistic.
    """
    # ------------------------------------------------------------------
    # 1. Load config + identity check — OPR12.
    # ------------------------------------------------------------------
    if config is None:
        config = load_operator_config(_CONFIG_PATH)
    _check_config_identity(config, _OPERATOR_NAME, _OPERATOR_VERSION)

    # ------------------------------------------------------------------
    # 2. Resolve params from config when omitted — OPR8.
    # ------------------------------------------------------------------
    if params is None:
        raw_lags = config.default_value("lags")
        params = LjungBoxParams(
            lags=int(raw_lags) if raw_lags is not None else None,
        )

    # ------------------------------------------------------------------
    # 3. Structural-input validation (OPR9 typed I/O; family floor).
    # ------------------------------------------------------------------
    if not isinstance(series, Series):
        raise LjungBoxError(
            "ljung_box: input must be a Series artifact; got "
            f"{type(series).__name__}."
        )

    payload = series.payload
    cleaned = payload.dropna()
    n_total = int(len(payload))
    n_obs = int(len(cleaned))
    n_dropped = n_total - n_obs

    # Resolve the horizon: explicit value, or the classical rule.
    # Only the RESOLVED value rides in lineage (OPR14 — the rolling
    # family's min_periods None→window convention): an auto-resolved
    # horizon and an explicit equal value produce byte-identical
    # outputs, so they must hash identically.
    if params.lags is not None:
        lags = int(params.lags)
    else:
        lags = max(1, min(_AUTO_LAG_CAP, n_obs // _AUTO_LAG_DIVISOR))

    if n_obs < lags + 2:
        raise LjungBoxError(
            f"ljung_box: needs at least lags + 2 = {lags + 2} finite "
            f"observations for a {lags}-lag horizon (got {n_obs}).  "
            "Lower lags or widen the input lookback."
        )

    # Interior-drop audit (A5 ruling): lag-based test — splicing
    # non-adjacent rows is disclosed, never silent.
    finite_mask = payload.notna().to_numpy()
    first = int(np.argmax(finite_mask))
    last = len(finite_mask) - int(np.argmax(finite_mask[::-1]))
    n_interior_dropped = int((~finite_mask[first:last]).sum())

    values = cleaned.to_numpy(dtype=float)
    if float(np.var(values)) == 0.0:
        raise LjungBoxError(
            "ljung_box: the input has zero variance — autocorrelation "
            "is undefined on a constant series."
        )

    # ------------------------------------------------------------------
    # 4. The test (statsmodels; per-lag trail kept for lineage).
    # ------------------------------------------------------------------
    table = acorr_ljungbox(values, lags=lags)
    stat = float(table["lb_stat"].iloc[-1])
    p_value = float(table["lb_pvalue"].iloc[-1])
    if not np.isfinite(stat):
        raise LjungBoxError(
            "ljung_box: the test produced a non-finite statistic "
            "(numerical failure)."
        )

    per_lag_stats = [float(v) for v in table["lb_stat"]]
    per_lag_p = [
        float(v) if np.isfinite(v) else None for v in table["lb_pvalue"]
    ]

    # ------------------------------------------------------------------
    # 5. Lineage — one OperatorStep (OPR10).  The RESOLVED horizon is
    #    what rides in params (OPR14: lags=None and an explicit equal
    #    value hash identically).
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "test_statistic": stat,
        "p_value": p_value if np.isfinite(p_value) else None,
        "lags": lags,
        "per_lag_stats": per_lag_stats,
        "per_lag_p_values": per_lag_p,
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
        metric_key=f"ljung_box__{series.series_key}",
        value=stat,
        units=TimeSeriesUnits.RATIO,
        lineage=series.lineage.append(step),
    )


__all__ = ["ljung_box", "LjungBoxError"]
