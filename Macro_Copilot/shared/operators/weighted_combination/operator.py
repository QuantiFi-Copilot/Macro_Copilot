"""weighted_combination — weighted sum of named SeriesSet members.

Finance-blind ``arithmetic`` operator — the N-leg combination the
toolbox lacked (only 2-leg ``series_arithmetic`` existed).  Computes
``out_t = Σ_k w_k · member_k,t`` over the members NAMED by the weights
mapping, emitting ONE ``Series`` — custom baskets, synthetic series,
and multi-leg structures (e.g. a 1/−2/1 three-leg) in a single node.

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind structural method (a weighted sum);
    the weights are caller-supplied structural coefficients — the
    operator never knows what they mean.
  - OPR2      : ONE output artifact type — always a ``Series``.
  - OPR4      : not composable — chaining 2-leg series_arithmetic
    N−1 times loses the basket as one auditable recipe and explodes
    the DAG; a 1-key "combination" IS plain scaling, so fewer than 2
    named members is refused with the series_arithmetic remedy.
  - OPR8      : ``params: Optional[...] = None`` — but ``weights`` is
    the basket definition with NO meaningful default (the
    ``select_from_series_set.series_key`` precedent): ``params=None``
    refuses cleanly naming the required field; the YAML ``defaults:``
    block is explicitly empty.
  - OPR9      : one ``SeriesSet`` in, one ``Series`` out.
  - OPR10/ART10: one ``OperatorStep`` extending the input set's chain
    (the set-producing step already folds every member's head hash);
    the WEIGHTS MAPPING — the content-defining recipe — is recorded
    in step params, so different weights yield different identities.
  - OPR11     : a weighted SUM requires one unit across the NAMED
    members — mixed units refused outright with the ``convert_units``
    remedy (no opt-out); unnamed members' units are irrelevant.
    Output = that common unit (weights are dimensionless).
  - OPR13     : typed ``WeightedCombinationError`` refusals: missing
    params/weights, fewer than 2 named members, a named key absent
    from the set, a non-finite or ZERO weight (to exclude a member,
    omit its key), mixed units across named members, an overflowing
    (±Inf) sum, or an all-NaN output.  The combination is NaN exactly
    where ANY named member is NaN (a partial weighted sum silently
    changes the basket — dishonest); unnamed members' NaNs are inert.
  - OPR12/14  : config name+version identity checked; pure + rerun-
    deterministic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from shared.artifacts.lineage import OperatorStep, sanitize_params_for_lineage
from shared.artifacts.missingness import RawNoCleaning
from shared.artifacts.types import Series, SeriesSet
from shared.artifacts.units import TimeSeriesUnits
from shared.config.operator_config import (
    OperatorConfig,
    _check_config_identity,
    load_operator_config,
)
from shared.operators.weighted_combination.schemas import (
    WeightedCombinationParams,
)


_OPERATOR_NAME = "weighted_combination"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


class WeightedCombinationError(ValueError):
    """Raised by ``weighted_combination`` on a recoverable user-facing
    failure (a ``ValueError`` subclass, OPR13)."""


def weighted_combination(
    series_set: SeriesSet,
    *,
    params: Optional[WeightedCombinationParams] = None,
    config: Optional[OperatorConfig] = None,
) -> Series:
    """Compute the weighted sum of the members named by the weights.

    Parameters
    ----------
    series_set :
        An aligned ``SeriesSet`` (canonically from ``align_series``)
        containing every member the weights mapping names; named
        members must share one unit.
    params :
        ``WeightedCombinationParams`` carrying the basket definition
        (``weights: member_key → float``).  REQUIRED in substance:
        when ``None`` the operator refuses with a typed error (the
        basket has no meaningful default).
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    Series
        ``Σ_k w_k · member_k`` on the shared index, NaN exactly where
        any NAMED member is NaN, in the named members' common unit;
        fresh ``RawNoCleaning`` missingness; frequency passthrough;
        lineage = the input set's chain extended by this step with the
        weights recorded.

    Raises
    ------
    WeightedCombinationError
        ``params``/weights missing; fewer than 2 named members; a
        named key absent from the set; a non-finite or zero weight;
        mixed units across the named members; an overflowing sum; or
        an all-NaN output.
    """
    # ------------------------------------------------------------------
    # 1. Load config + identity check — OPR12.
    # ------------------------------------------------------------------
    if config is None:
        config = load_operator_config(_CONFIG_PATH)
    _check_config_identity(config, _OPERATOR_NAME, _OPERATOR_VERSION)

    # ------------------------------------------------------------------
    # 2. Params — the basket definition has no meaningful default
    #    (OPR8 honest refusal; the select_from_series_set precedent).
    # ------------------------------------------------------------------
    if params is None:
        raise WeightedCombinationError(
            "weighted_combination requires explicit params with a "
            "weights mapping (member_key → float) — the basket "
            "definition has no meaningful default."
        )

    # ------------------------------------------------------------------
    # 3. Structural-input validation (OPR9 typed I/O; OPR11 units).
    # ------------------------------------------------------------------
    if not isinstance(series_set, SeriesSet):
        raise WeightedCombinationError(
            "weighted_combination: input must be a SeriesSet artifact; "
            f"got {type(series_set).__name__}.  Build one upstream with "
            "align_series."
        )

    weights = dict(params.weights)
    named = sorted(weights)
    if len(named) < 2:
        raise WeightedCombinationError(
            f"weighted_combination: {len(named)} member(s) named; a "
            "combination needs at least 2 (scaling one series is "
            "series_arithmetic op='multiply' with a scalar literal)."
        )

    available = set(series_set.keys())
    missing = [k for k in named if k not in available]
    if missing:
        raise WeightedCombinationError(
            f"weighted_combination: named member(s) {missing} not in "
            f"the set.  Available keys: {sorted(available)}."
        )

    for k in named:
        w = weights[k]
        if not np.isfinite(w):
            raise WeightedCombinationError(
                f"weighted_combination: weight for {k!r} is "
                f"non-finite ({w!r}); weights must be finite."
            )
        if w == 0.0:
            raise WeightedCombinationError(
                f"weighted_combination: weight for {k!r} is zero — to "
                "exclude a member, omit its key from the weights "
                "mapping."
            )

    named_units = {series_set.units_by_key[k].value for k in named}
    if len(named_units) > 1:
        raise WeightedCombinationError(
            "weighted_combination: the named members carry mixed units "
            f"({sorted(named_units)}).  A weighted sum requires one "
            "unit — insert convert_units on the offending members "
            "upstream (the sole unit-transition site)."
        )
    common_unit = TimeSeriesUnits(next(iter(named_units)))

    # ------------------------------------------------------------------
    # 4. The weighted sum over the NAMED members.  pandas addition
    #    propagates NaN, so the combination is NaN exactly where any
    #    named member is NaN; unnamed members never enter.
    # ------------------------------------------------------------------
    combo = None
    for k in named:
        term = series_set.series_by_key[k] * weights[k]
        combo = term if combo is None else combo + term

    if np.isinf(combo.to_numpy(dtype=float)).any():
        raise WeightedCombinationError(
            "weighted_combination: the weighted sum overflows the "
            "float range (numerical failure, not missingness) — check "
            "the weights' and members' magnitudes."
        )

    if int(np.isfinite(combo.to_numpy(dtype=float)).sum()) == 0:
        raise WeightedCombinationError(
            f"weighted_combination: produced an all-NaN output "
            f"({len(combo)} dates × {len(named)} named members).  No "
            "date had all named members present."
        )

    combo = combo.astype(float)
    combo.name = f"weighted_combo__{len(named)}_legs"

    # ------------------------------------------------------------------
    # 5. Lineage — one OperatorStep extending the input set's chain;
    #    the weights mapping IS the recipe (ART10) and rides in params.
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "weights": {k: float(weights[k]) for k in named},
        "named_members": named,
        "n_legs": len(named),
        "n_dates": int(len(combo)),
        "member_units": sorted(named_units),
    }
    combo_step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params=sanitize_params_for_lineage(step_params),
        input_hashes=(series_set.lineage.head_hash,),
    )
    out_lineage = series_set.lineage.append(combo_step)

    return Series(
        series_key=f"weighted_combo__{len(named)}_legs",
        payload=combo,
        units=common_unit,
        frequency=series_set.frequency,
        missingness_policy=RawNoCleaning(),
        lineage=out_lineage,
    )


__all__ = ["weighted_combination", "WeightedCombinationError"]
