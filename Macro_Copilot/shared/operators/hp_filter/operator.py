"""hp_filter — Hodrick–Prescott trend/cycle decomposition of a Series.

Finance-blind ``single_series_transform`` operator.  Decomposes the
input into a smooth long-run trend and a cyclical residual via the
standard two-sided Hodrick–Prescott filter
(``statsmodels.tsa.filters.hp_filter.hpfilter`` — battle-tested sparse
solve; the math is deliberately NOT re-implemented), emitting ONE
chosen component as a Series in the input's units.  trend + cycle
reconstruct the input exactly.

λ IS REQUIRED (no default — the weighted_combination weights
precedent): the smoothing strength is frequency-dependent METHODOLOGY
(quarterly convention 1600; monthly ≈ 14400; daily ≈ 1e6–1e7) and a
fixed default applied at the wrong frequency silently yields a garbage
trend.  ``params=None`` refuses cleanly naming the field.

LOOK-AHEAD DISCLOSURE (P5, load-bearing): the filter is TWO-SIDED —
the trend at every position is fitted against the WHOLE sample (and is
least stable near the boundaries, where one side of the context is
missing).  Disclosed here, in the YAML, the card, the registry text,
and in lineage (``filter_scope='full_sample_two_sided'``).  Never feed
the output into point-in-time compositions.

NaN POLICY (two-tier): contiguous LEADING/TRAILING NaN — the warmup
head every rolling/ewm/lag upstream legitimately emits — is TOLERATED:
the solve runs on the interior finite block and the edges pass through
as NaN (the family's legitimate-missingness convention; trimming a
contiguous edge leaves all remaining rows genuinely adjacent, so the
penalty is undistorted).  INTERIOR gaps are REFUSED: the HP penalty is
a second difference over ADJACENT rows; statsmodels silently
propagates NaN through the solve (probed), and dropping interior NaNs
would collapse gaps and pretend non-adjacent observations are adjacent
— quiet methodology distortion.  The refusal names the actionable
remedy (``align_series`` ffill genuinely fixes interior gaps).

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind structural method (penalised
    smoothing); zero finance vocabulary; row adjacency, no calendar
    math.
  - OPR2      : ONE output artifact type — always a ``Series``
    (component chooses WHICH series, never the type).
  - OPR8      : params required (λ has no honest default); the
    refusal names the field.
  - OPR9      : one ``Series`` in, one ``Series`` out.
  - OPR10     : one ``OperatorStep``; λ, the component, the scope and
    the variance split ride in lineage.
  - OPR11     : units passthrough for BOTH components (the filter is
    linear; a trend/cycle of BPS is BPS).
  - OPR13     : typed ``HpFilterError`` refusals: non-Series input;
    missing params/λ; INTERIOR NaN (gap distortion — edge warmup NaN
    passes through); fewer than 3 interior observations (the
    second-difference penalty needs >= 3); a non-finite output.
  - OPR12/14  : config name+version identity checked; pure +
    rerun-deterministic (a deterministic sparse linear solve).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
from statsmodels.tsa.filters.hp_filter import hpfilter

from shared.artifacts.lineage import OperatorStep, sanitize_params_for_lineage
from shared.artifacts.types import Series
from shared.config.operator_config import (
    OperatorConfig,
    _check_config_identity,
    load_operator_config,
)
from shared.operators.hp_filter.schemas import HpFilterParams


_OPERATOR_NAME = "hp_filter"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


class HpFilterError(ValueError):
    """Raised by ``hp_filter`` on a recoverable user-facing failure (a
    ``ValueError`` subclass, OPR13)."""


def hp_filter(
    series: Series,
    *,
    params: Optional[HpFilterParams] = None,
    config: Optional[OperatorConfig] = None,
) -> Series:
    """Hodrick–Prescott trend or cycle of ``series``.

    Parameters
    ----------
    series :
        The input ``Series`` — interior-gap-free (contiguous
        leading/trailing warmup NaN is tolerated and passes through;
        interior NaN must be cleaned upstream).
    params :
        ``HpFilterParams`` — REQUIRED (λ has no honest default).
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    Series
        The chosen component (trend or cycle) on the input index —
        contiguous edge NaN passes through; the solve covers the
        interior finite block — in the input's units; frequency and
        missingness policy pass through; lineage records λ, the
        component, the two-sided full-sample scope, the edge-NaN
        counts and the variance split.

    Raises
    ------
    HpFilterError
        Missing params (λ is required); a non-Series input; an
        INTERIOR NaN; fewer than 3 interior observations; or a
        non-finite output.
    """
    # ------------------------------------------------------------------
    # 1. Load config + identity check — OPR12.
    # ------------------------------------------------------------------
    if config is None:
        config = load_operator_config(_CONFIG_PATH)
    _check_config_identity(config, _OPERATOR_NAME, _OPERATOR_VERSION)

    # ------------------------------------------------------------------
    # 2. λ is methodology — params=None refuses naming the field
    #    (the weighted_combination precedent; OPR8 honest refusal).
    # ------------------------------------------------------------------
    if params is None:
        raise HpFilterError(
            "hp_filter requires explicit params with lamb (the HP "
            "smoothing strength) — it is frequency-dependent "
            "methodology with no honest default (~1600 quarterly, "
            "~14400 monthly, ~1e6-1e7 daily)."
        )

    # ------------------------------------------------------------------
    # 3. Structural-input validation (OPR9 typed I/O; two-tier NaN).
    # ------------------------------------------------------------------
    if not isinstance(series, Series):
        raise HpFilterError(
            "hp_filter: input must be a Series artifact; got "
            f"{type(series).__name__}."
        )

    values = series.payload.to_numpy(dtype=float)
    n_obs = int(len(values))
    finite_mask = np.isfinite(values)
    n_finite = int(finite_mask.sum())
    if n_finite < 3:
        raise HpFilterError(
            f"hp_filter: needs at least 3 finite observations (got "
            f"{n_finite}) — the second-difference penalty is undefined "
            "below that."
        )

    # Two-tier NaN policy: contiguous LEADING/TRAILING NaN (the warmup
    # head every rolling/ewm/lag upstream emits) is tolerated — the
    # solve runs on the interior finite block and the edges pass
    # through as NaN (trimming a contiguous edge leaves the remaining
    # rows genuinely adjacent).  INTERIOR gaps are refused: dropping
    # them would collapse the second-difference penalty across
    # non-adjacent observations.
    first = int(np.argmax(finite_mask))
    last = n_obs - int(np.argmax(finite_mask[::-1]))  # exclusive
    core = values[first:last]
    n_interior_nan = int(np.isnan(core).sum())
    if n_interior_nan > 0:
        raise HpFilterError(
            f"hp_filter: the input has {n_interior_nan} INTERIOR NaN "
            "value(s).  The HP penalty runs over ADJACENT rows; "
            "dropping interior gaps would silently pretend "
            "non-adjacent observations are adjacent.  Fill the gaps "
            "upstream (align_series ffill) and re-run.  (Contiguous "
            "leading/trailing warmup NaN is tolerated and passes "
            "through.)"
        )

    # ------------------------------------------------------------------
    # 4. The decomposition (statsmodels two-sided HP; deterministic)
    #    on the interior finite block; edge NaN passes through.
    # ------------------------------------------------------------------
    lamb = float(params.lamb)
    core_payload = series.payload.iloc[first:last]
    cycle_core, trend_core = hpfilter(core_payload, lamb=lamb)
    chosen_core = (
        trend_core if params.component == "trend" else cycle_core
    ).astype(float)

    if not np.isfinite(chosen_core.to_numpy(dtype=float)).all():
        raise HpFilterError(
            "hp_filter: the solve produced a non-finite output "
            "(numerical failure)."
        )

    result = pd.Series(
        np.nan, index=series.payload.index, dtype=float,
    )
    result.iloc[first:last] = chosen_core.to_numpy(dtype=float)
    result.name = f"hp_{params.component}__{series.series_key}"

    # Variance split for the audit trail: how much of the interior
    # block's variance the cycle carries (data-derived diagnostic, the
    # detrend fitted-params precedent).
    var_input = float(np.var(core))
    var_cycle = float(np.var(cycle_core.to_numpy(dtype=float)))
    cycle_variance_share = (
        float(var_cycle / var_input) if var_input > 0.0 else None
    )

    # ------------------------------------------------------------------
    # 5. Lineage — one OperatorStep (OPR10); λ + scope disclosed.
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "component": params.component,
        "lamb": lamb,
        "filter_scope": "full_sample_two_sided",  # LOOK-AHEAD (P5)
        "cycle_variance_share": cycle_variance_share,
        "n_obs": n_obs,
        "n_leading_nan": first,
        "n_trailing_nan": n_obs - last,
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
        series_key=f"hp_{params.component}__{series.series_key}",
        payload=result,
        units=series.units,
        frequency=series.frequency,
        missingness_policy=series.missingness_policy,
        lineage=out_lineage,
    )


__all__ = ["hp_filter", "HpFilterError"]
