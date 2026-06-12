"""detrend — remove a deterministic trend from a Series.

Finance-blind ``single_series_transform`` operator.  Subtracts a
full-sample deterministic trend — the mean (method='demean') or an
OLS straight line fitted on the 0..n−1 row positions
(method='linear') — emitting the residual Series in the input's
units.

LOOK-AHEAD DISCLOSURE (P5, load-bearing — the winsorize pattern): both
methods fit over the WHOLE sample, so the residual at every position
uses data from the entire series (the trend "knows" the end of the
sample).  That is the standard descriptive detrending semantics;
disclosed here, in the YAML, in the card, in the registry text, and in
lineage (``trend_scope='full_sample'``).  Do NOT feed the output into
point-in-time compositions; chaining another full-sample fit
downstream compounds the look-ahead.

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind structural method (deterministic-
    trend removal); the time axis is ROW POSITIONS (0..n−1), not
    calendar time — pure structure, no day-count or calendar math.
  - OPR2      : ONE output artifact type — always a ``Series``; units
    passthrough for both methods (a residual of BPS is BPS).
  - OPR8      : ``params: Optional[DetrendParams] = None``; defaults
    resolve from ``config.yaml``.
  - OPR9      : one ``Series`` in, one ``Series`` out.
  - OPR10     : one ``OperatorStep``; the FITTED trend parameters
    (mean, or slope+intercept per row) ride in lineage so the removed
    trend is fully auditable.
  - OPR13     : typed ``DetrendError`` refusals: non-Series input;
    fewer than 2 finite observations (a 1-point "trend" is the
    identity for demean and undefined for linear — uniform floor for
    coherent semantics); a degenerate linear fit; an overflowing
    (±Inf) residual (subtraction of extremes — the
    demean_cross_section precedent).  NaN positions stay NaN.
  - OPR12/14  : config name+version identity checked; pure + rerun-
    deterministic (numpy lstsq on a 2-column design is deterministic).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from shared.artifacts.lineage import OperatorStep, sanitize_params_for_lineage
from shared.artifacts.types import Series
from shared.config.operator_config import (
    OperatorConfig,
    _check_config_identity,
    load_operator_config,
)
from shared.operators.detrend.schemas import DetrendParams


_OPERATOR_NAME = "detrend"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


class DetrendError(ValueError):
    """Raised by ``detrend`` on a recoverable user-facing failure (a
    ``ValueError`` subclass, OPR13)."""


def detrend(
    series: Series,
    *,
    params: Optional[DetrendParams] = None,
    config: Optional[OperatorConfig] = None,
) -> Series:
    """Subtract a full-sample deterministic trend from ``series``.

    Parameters
    ----------
    series :
        The input ``Series``.
    params :
        Optional ``DetrendParams``.  When ``None`` every field is
        resolved from the bundled ``config.yaml`` (OPR8).
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    Series
        The residual (input − fitted trend) on the input index (NaN
        positions stay NaN), in the input's units; frequency and
        missingness policy pass through; lineage records the method,
        the FITTED parameters and ``trend_scope='full_sample'``.

    Raises
    ------
    DetrendError
        The input is not a ``Series``; it has fewer than 2 finite
        observations; the linear fit is degenerate; or a residual
        overflows the float range.
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
        params = DetrendParams(method=config.default_value("method"))

    # ------------------------------------------------------------------
    # 3. Structural-input validation (OPR9 typed I/O).
    # ------------------------------------------------------------------
    if not isinstance(series, Series):
        raise DetrendError(
            "detrend: input must be a Series artifact; got "
            f"{type(series).__name__}."
        )

    values = series.payload.to_numpy(dtype=float)
    finite_mask = np.isfinite(values)
    n_finite = int(finite_mask.sum())
    if n_finite < 2:
        raise DetrendError(
            f"detrend: the input has {n_finite} finite observation(s); "
            "trend removal needs at least 2 (a 1-point demean is the "
            "identity-to-zero and a 1-point line is undefined)."
        )

    # ------------------------------------------------------------------
    # 4. Fit the full-sample trend on the finite positions and subtract.
    # ------------------------------------------------------------------
    positions = np.arange(len(values), dtype=float)
    residual = np.full_like(values, np.nan)

    fitted: Dict[str, Any]
    if params.method == "demean":
        mean = float(values[finite_mask].mean())
        residual[finite_mask] = values[finite_mask] - mean
        fitted = {"mean": mean}
    elif params.method == "linear":
        # OLS y = intercept + slope·t on the finite row positions —
        # the regression_residual numpy-lstsq core.
        design = np.column_stack([
            np.ones(n_finite), positions[finite_mask],
        ])
        y = values[finite_mask]
        coef, _, rank, _ = np.linalg.lstsq(design, y, rcond=None)
        if rank < 2:
            raise DetrendError(
                "detrend: the linear fit is degenerate (rank-deficient "
                "design — fewer than 2 distinct positions)."
            )
        intercept, slope = float(coef[0]), float(coef[1])
        residual[finite_mask] = y - (
            intercept + slope * positions[finite_mask]
        )
        ss_res = float(np.sum(residual[finite_mask] ** 2))
        ss_tot = float(np.sum((y - y.mean()) ** 2))
        r_squared = (
            float(1.0 - ss_res / ss_tot) if ss_tot > 0.0 else None
        )
        fitted = {
            "intercept": intercept,
            "slope": slope,  # per ROW position, not per calendar day
            "r_squared": r_squared,
        }
    else:
        # Defensive (the schema's Literal closes the set) — OPR8.
        raise NotImplementedError(
            f"detrend: method={params.method!r} is declared but not "
            "implemented; see config.yaml methodology.planned_extensions."
        )

    # Overflow honesty (the demean_cross_section precedent):
    # subtraction of extreme finite values is not closed over the
    # float range — typed refusal, never a raw constructor crash.
    if np.isinf(residual[finite_mask]).any():
        raise DetrendError(
            "detrend: a residual overflows the float range (numerical "
            "failure, not missingness)."
        )

    result = pd.Series(
        residual, index=series.payload.index, dtype=float,
    )
    result.name = f"detrended_{params.method}__{series.series_key}"

    # ------------------------------------------------------------------
    # 5. Lineage — one OperatorStep (OPR10); the fitted trend and the
    #    look-ahead scope ride in params.
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "method": params.method,
        "trend_scope": "full_sample",  # LOOK-AHEAD disclosure (P5)
        "fitted": fitted,
        "n_obs": int(len(values)),
        "n_finite": n_finite,
        "input_units": series.units.value,
        "input_series_key": series.series_key,
    }
    step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params=sanitize_params_for_lineage(step_params),
        input_hashes=(series.lineage.head_hash,),
    )
    out_lineage = series.lineage.append(step)

    return Series(
        series_key=f"detrended_{params.method}__{series.series_key}",
        payload=result,
        units=series.units,
        frequency=series.frequency,
        missingness_policy=series.missingness_policy,
        lineage=out_lineage,
    )


__all__ = ["detrend", "DetrendError"]
