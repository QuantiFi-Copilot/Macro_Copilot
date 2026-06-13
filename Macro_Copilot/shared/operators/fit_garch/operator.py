"""fit_garch — GARCH(1,1) in-sample conditional volatility of a Series.

Finance-blind ``single_series_transform`` operator (the hp_filter
shape: one Series in, one transformed Series out) and the THIRD A4
model-fit engine.  Fits a GARCH(1,1) model (the Gaussian-MLE math lives
in ``shared/quant/garch.py``, the fifth module there — no library) and
emits the fitted conditional-volatility PATH σ_t as a Series in the
input's units.

DESCRIPTIVE, NOT A FORECAST (the scope boundary — the thesis's "we
describe, we do not forecast"; plan §4 names GARCH as the exemplar of
the allowed *current vol state* read).  The output covers the input's
OWN dates only — σ_t for t over the OBSERVED history (how volatility
clustered).  There is NO σ_{T+1}: the output index EQUALS the input
index; no projection beyond the last observation.

OPERATES ON THE SERIES AS GIVEN — it does NOT difference internally
(stamped ``differenced=False``; the hurst precedent).  GARCH is
conventionally fit to RETURNS; for a price/level series difference it
first (``series_arithmetic(op='diff') -> fit_garch``).

DESIGN LOCKS (OPR7, lineage-stamped): GARCH(1,1)
(``order='(1,1)'``), Gaussian innovations
(``distribution='gaussian'``), a constant mean μ = the sample mean
(``mean_model='constant'``) and the Gaussian-MLE estimator
(``estimation_method='gaussian_mle'``).  Higher orders / Student-t /
asymmetry are declared planned extensions.

SCOPE DISCLOSURE (P5): the σ_t recursion is causal GIVEN the
parameters, BUT the parameters are estimated by MLE over the WHOLE
sample, so the path is FULL-SAMPLE-FITTED — disclosed as
``fit_scope='full_sample'``.  Never feed it into point-in-time
compositions.

NaN POLICY (two-tier, the hp_filter precedent): contiguous
LEADING/TRAILING NaN is TOLERATED (fit on the interior finite block;
edges pass through as NaN).  INTERIOR gaps are REFUSED: the GARCH
recursion is SEQUENTIAL over CONTIGUOUS rows, so dropping an interior
NaN would feed ε²_{t-1} from the wrong t-1 and silently corrupt every
downstream σ.  The refusal names the actionable remedy (``align_series``
ffill).

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind model fit (conditional std path);
    zero finance vocabulary; NO annualization.
  - OPR2      : ONE output artifact type — always a ``Series`` (the
    vol path; the fitted params ride in lineage, not a second output).
  - OPR8      : ``params: Optional[...] = None`` (zero knobs; the
    locks documented + stamped).
  - OPR9      : one ``Series`` in, one ``Series`` out.
  - OPR10     : one ``OperatorStep``; ω/α/β/persistence/μ/loglik + the
    locks + the scope ride in lineage.
  - OPR11     : units passthrough (a vol of the input's increments is
    in the input's units; NO derived/squared unit).
  - OPR13     : typed ``FitGarchError`` refusals: non-Series input;
    fewer than 12 finite observations; zero variance; MLE
    NON-CONVERGENCE; a degenerate/near-integrated fit (α+β at the
    stationarity boundary); an INTERIOR NaN; a non-finite path.
  - OPR14     : pure + rerun-deterministic (a fixed optimiser start,
    no random restarts).
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
from shared.operators.fit_garch.schemas import FitGarchParams
from shared.quant.garch import fit_garch_11


_OPERATOR_NAME = "fit_garch"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

# A4 family floor (mirrors fit_ou / changepoint_detection).
_FAMILY_FLOOR = 12
# Below this the GARCH fit is fragile — disclosed, NOT refused.
_LOW_SAMPLE = 100
# At/above this persistence the fit is essentially integrated (the
# stationarity constraint clamped) — the unconditional variance
# explodes; refuse rather than emit an unreliable path.
_PERSISTENCE_REFUSE = 1.0 - 1e-5
# Below the refuse threshold but still high — emit WITH disclosure.
_NEAR_INTEGRATED = 0.99


class FitGarchError(ValueError):
    """Raised by ``fit_garch`` on a recoverable user-facing failure (a
    ``ValueError`` subclass, OPR13)."""


def fit_garch(
    series: Series,
    *,
    params: Optional[FitGarchParams] = None,
    config: Optional[OperatorConfig] = None,
) -> Series:
    """GARCH(1,1) in-sample conditional-volatility path of ``series``.

    Parameters
    ----------
    series :
        The input ``Series`` — modelled AS GIVEN (difference a level to
        returns upstream); interior-gap-free (contiguous leading/
        trailing warmup NaN passes through).
    params :
        Optional ``FitGarchParams`` (no fields in v1).  ``None`` is
        equivalent.
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    Series
        The fitted conditional-volatility path σ_t ON THE INPUT INDEX
        (in-sample — no σ_{T+1}), in the input's units; contiguous edge
        NaN passes through; ω/α/β/persistence/μ/loglik and the locks
        ride in lineage.

    Raises
    ------
    FitGarchError
        The input is not a ``Series``; fewer than 12 finite
        observations; zero variance; the MLE did not converge; a
        near-integrated/degenerate fit; an INTERIOR NaN; or a
        non-finite path.
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
        params = FitGarchParams()

    # ------------------------------------------------------------------
    # 3. Structural-input validation (OPR9 typed I/O; two-tier NaN).
    # ------------------------------------------------------------------
    if not isinstance(series, Series):
        raise FitGarchError(
            "fit_garch: input must be a Series artifact; got "
            f"{type(series).__name__}."
        )

    payload = series.payload
    values_all = payload.to_numpy(dtype=float)
    n_total = int(len(values_all))
    finite_mask = np.isfinite(values_all)
    n_finite = int(finite_mask.sum())
    if n_finite == 0:
        raise FitGarchError(
            "fit_garch: the input has no finite observations."
        )

    # Two-tier NaN: contiguous leading/trailing tolerated; the fit runs
    # on the interior finite block.  INTERIOR gaps refused (the GARCH
    # recursion is sequential over contiguous rows).
    first = int(np.argmax(finite_mask))
    last = n_total - int(np.argmax(finite_mask[::-1]))  # exclusive
    core = values_all[first:last]
    n_interior_nan = int(np.isnan(core).sum())
    if n_interior_nan > 0:
        raise FitGarchError(
            f"fit_garch: the input has {n_interior_nan} INTERIOR NaN "
            "value(s).  The GARCH recursion is SEQUENTIAL over "
            "ADJACENT rows; dropping interior gaps would feed the "
            "wrong lagged residual and corrupt every downstream "
            "volatility.  Fill the gaps upstream (align_series ffill) "
            "and re-run.  (Contiguous leading/trailing warmup NaN is "
            "tolerated and passes through.)"
        )

    n_core = int(len(core))
    if n_core < _FAMILY_FLOOR:
        raise FitGarchError(
            f"fit_garch: needs at least {_FAMILY_FLOOR} finite "
            f"observations (got {n_core}) — the GARCH likelihood is "
            "degenerate below that."
        )
    if float(np.var(core)) == 0.0:
        raise FitGarchError(
            "fit_garch: the input has zero variance — the GARCH "
            "likelihood is degenerate on a constant series."
        )

    # ------------------------------------------------------------------
    # 4. The fit (shared/quant; design-locked GARCH(1,1) Gaussian MLE).
    # ------------------------------------------------------------------
    result = fit_garch_11(core)
    if not result.converged:
        raise FitGarchError(
            "fit_garch: the GARCH(1,1) maximum-likelihood fit did not "
            "converge — the conditional-volatility path would be "
            "unreliable.  (The series may be too short or not exhibit "
            "GARCH-type volatility clustering.)"
        )
    persistence = float(result.persistence)
    if (
        persistence >= _PERSISTENCE_REFUSE
        or result.omega <= 0.0
        or result.alpha < 0.0
        or result.beta < 0.0
    ):
        raise FitGarchError(
            "fit_garch: the fit is degenerate / essentially integrated "
            f"(persistence alpha+beta = {persistence:.6g} at the "
            "stationarity boundary, or a non-positive omega) — the "
            "unconditional variance is undefined and the path is "
            "unreliable."
        )
    cond_vol = np.asarray(result.conditional_vol, dtype=float)
    if not np.isfinite(cond_vol).all():
        raise FitGarchError(
            "fit_garch: the fit produced a non-finite volatility path "
            "(numerical failure)."
        )

    near_integrated = persistence >= _NEAR_INTEGRATED
    low_sample = n_core < _LOW_SAMPLE

    # ------------------------------------------------------------------
    # 5. Build the output Series — σ_t ON THE INPUT INDEX (in-sample;
    #    edge NaN passes through; NO projection beyond the last obs).
    # ------------------------------------------------------------------
    out_values = np.full(n_total, np.nan, dtype=float)
    out_values[first:last] = cond_vol
    out_payload = pd.Series(
        out_values, index=payload.index, dtype=float,
    )
    out_payload.name = f"garch_vol__{series.series_key}"

    # ------------------------------------------------------------------
    # 6. Lineage — one OperatorStep (OPR10); the fit + locks + scope.
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "omega": float(result.omega),
        "alpha": float(result.alpha),
        "beta": float(result.beta),
        "persistence": persistence,
        "mu": float(result.mu),
        "loglik": float(result.loglik),
        "converged": True,
        "near_integrated": near_integrated,
        "low_sample_warning": low_sample,
        "order": "(1,1)",                    # design-locked (OPR7)
        "distribution": "gaussian",          # design-locked (OPR7)
        "mean_model": "constant",            # design-locked (OPR7)
        "estimation_method": "gaussian_mle",  # design-locked (OPR7)
        "differenced": False,                # operates on the series as given
        "fit_scope": "full_sample",          # LOOK-AHEAD (P5)
        "n_obs": n_total,
        "n_finite": n_finite,
        "n_leading_nan": first,
        "n_trailing_nan": n_total - last,
        "input_units": series.units.value,
        "input_series_key": series.series_key,
    }
    step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params=sanitize_params_for_lineage(step_params),
        input_hashes=(series.lineage.head_hash,),
    )

    return Series(
        series_key=f"garch_vol__{series.series_key}",
        payload=out_payload,
        units=series.units,
        frequency=series.frequency,
        missingness_policy=series.missingness_policy,
        lineage=series.lineage.append(step),
    )


__all__ = ["fit_garch", "FitGarchError"]
