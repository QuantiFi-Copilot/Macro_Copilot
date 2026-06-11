"""cross_sectional_statistic — per-date summary of a SeriesSet's members.

Finance-blind ``cross_sectional`` operator.  At every date, reduces the
cross-section of an aligned ``SeriesSet``'s non-NaN members to ONE
number — mean | median | std | min | max | sum — emitting a single
``Series`` ("the universe's average / dispersion / extreme per date").
Consolidates the plan's cross_sectional_mean/median and
cross_sectional_dispersion rows as ONE operator with a ``statistic``
variant (the ``rolling_statistic`` OPR2 precedent: one cohesive
reduction family, constant Series output, units passthrough).

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind reduction family; zero finance
    vocabulary or math.
  - OPR2      : ONE output artifact type — always a ``Series``.
  - OPR8      : ``params: Optional[...] = None``; defaults resolve
    from ``config.yaml``.
  - OPR9      : one ``SeriesSet`` in, one ``Series`` out.
  - OPR10     : one ``OperatorStep``; the output Series' lineage is
    the input set's chain EXTENDED by this step — a connected chain
    (ART9).  The reduction collapses N members to one number per
    date, so per-member upstream chains do not propagate beyond the
    set chain (which already folds every member's head hash via the
    set-producing step).
  - OPR11     : a cross-member summary compares VALUES, so all members
    MUST share one unit — mixed units refused outright with the
    ``convert_units`` remedy (no opt-out).  Output units = the
    members' common unit (passthrough for every statistic).
  - OPR13     : typed ``CrossSectionalStatisticError`` refusals:
    non-Set input, fewer than 2 members, mixed units, an overflowing
    (±Inf) summary, or an all-NaN output.  A date with fewer than
    ``min_members`` non-NaN members emits NaN (legitimate per-date
    missingness); ``std`` of an all-equal date is a legitimate 0.0.
  - OPR12/14  : config name+version identity checked; pure + rerun-
    deterministic; ``ddof`` is nulled in lineage params for
    non-``std`` statistics (meaningless-param normalisation — the
    ``rolling_statistic`` precedent).
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
from shared.operators.cross_sectional_statistic.schemas import (
    CrossSectionalStatisticParams,
)


_OPERATOR_NAME = "cross_sectional_statistic"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


class CrossSectionalStatisticError(ValueError):
    """Raised by ``cross_sectional_statistic`` on a recoverable
    user-facing failure (a ``ValueError`` subclass, OPR13)."""


def cross_sectional_statistic(
    series_set: SeriesSet,
    *,
    params: Optional[CrossSectionalStatisticParams] = None,
    config: Optional[OperatorConfig] = None,
) -> Series:
    """Reduce ``series_set``'s cross-section to one number per date.

    Parameters
    ----------
    series_set :
        An aligned ``SeriesSet`` (canonically from ``align_series``)
        whose members all share one unit.
    params :
        Optional ``CrossSectionalStatisticParams``.  When ``None``
        every field is resolved from the bundled ``config.yaml``.
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    Series
        One value per date — the chosen statistic across the non-NaN
        members — in the members' common unit.  Missingness is fresh
        (``RawNoCleaning``); frequency passes through; lineage is the
        input set's chain extended by this step.

    Raises
    ------
    CrossSectionalStatisticError
        The input is not a ``SeriesSet``; it has fewer than 2 members;
        its members carry mixed units; a summary overflows the float
        range; or every date's summary is NaN.
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
        params = CrossSectionalStatisticParams(
            statistic=config.default_value("statistic"),
            ddof=int(config.default_value("ddof")),
            min_members=int(config.default_value("min_members")),
        )

    # ------------------------------------------------------------------
    # 3. Structural-input validation (OPR9 typed I/O; OPR11 units).
    # ------------------------------------------------------------------
    if not isinstance(series_set, SeriesSet):
        raise CrossSectionalStatisticError(
            "cross_sectional_statistic: input must be a SeriesSet "
            f"artifact; got {type(series_set).__name__}.  Build one "
            "upstream with align_series."
        )

    keys = series_set.keys()
    if len(keys) < 2:
        raise CrossSectionalStatisticError(
            f"cross_sectional_statistic: the set has {len(keys)} "
            "member(s); a cross-sectional summary needs at least 2 "
            "members (a 1-member summary is the identity, not a "
            "reduction)."
        )

    distinct_units = {u.value for u in series_set.units_by_key.values()}
    if len(distinct_units) > 1:
        raise CrossSectionalStatisticError(
            "cross_sectional_statistic: the set's members carry mixed "
            f"units ({sorted(distinct_units)}).  A cross-member summary "
            "compares VALUES, so mixed units have no honest "
            "interpretation — insert convert_units on the offending "
            "members upstream (the sole unit-transition site)."
        )
    common_unit = TimeSeriesUnits(next(iter(distinct_units)))

    # ------------------------------------------------------------------
    # 4. Per-date reduction across the members.
    # ------------------------------------------------------------------
    frame = pd.DataFrame(
        {k: series_set.series_by_key[k] for k in keys},
        index=series_set.common_index,
    )
    n_valid = frame.notna().sum(axis=1)

    if params.statistic == "mean":
        result = frame.mean(axis=1)
    elif params.statistic == "median":
        result = frame.median(axis=1)
    elif params.statistic == "std":
        result = frame.std(axis=1, ddof=params.ddof)
    elif params.statistic == "min":
        result = frame.min(axis=1)
    elif params.statistic == "max":
        result = frame.max(axis=1)
    elif params.statistic == "sum":
        # Note pandas emits 0.0 for all-NaN rows; the min_members mask
        # below corrects that to honest NaN.
        result = frame.sum(axis=1)
    else:
        # Defensive (the schema's Literal already closes the set): a
        # future Literal expansion landing without a dispatch branch
        # must refuse, never silently compute another statistic (OPR8).
        raise NotImplementedError(
            f"cross_sectional_statistic: statistic="
            f"{params.statistic!r} is declared but not implemented; "
            "see config.yaml methodology.planned_extensions."
        )

    result[n_valid < params.min_members] = np.nan

    # Overflow honesty (the covariance-family precedent): a sum/mean of
    # extreme values CAN overflow on legal finite inputs; ±Inf is
    # forbidden in any payload (ART11) and a silent scrub would
    # mislabel numerical failure as missingness.
    if np.isinf(result.to_numpy(dtype=float)).any():
        raise CrossSectionalStatisticError(
            "cross_sectional_statistic: a per-date summary overflows "
            "the float range (numerical failure, not missingness)."
        )

    n_finite = int(np.isfinite(result.to_numpy(dtype=float)).sum())
    if n_finite == 0:
        raise CrossSectionalStatisticError(
            f"cross_sectional_statistic: produced an all-NaN output "
            f"({len(frame)} dates × {len(keys)} members, "
            f"statistic={params.statistic!r}, "
            f"min_members={params.min_members}).  No date had enough "
            "non-NaN members."
        )

    result = result.astype(float)
    result.name = f"cs_{params.statistic}__{len(keys)}_members"

    # ------------------------------------------------------------------
    # 5. Lineage — one OperatorStep extending the input set's chain
    #    (OPR10/ART9: a connected chain; the set-producing step already
    #    folds every member's head hash).  ddof is nulled for non-std
    #    statistics (OPR14 meaningless-param normalisation).
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "statistic": params.statistic,
        "ddof": params.ddof if params.statistic == "std" else None,
        "min_members": params.min_members,
        "n_members": len(keys),
        "n_dates": int(len(frame)),
        "member_keys": sorted(keys),
        "member_units": sorted(distinct_units),
    }
    stat_step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params=sanitize_params_for_lineage(step_params),
        input_hashes=(series_set.lineage.head_hash,),
    )
    out_lineage = series_set.lineage.append(stat_step)

    return Series(
        series_key=f"cs_{params.statistic}__{len(keys)}_members",
        payload=result,
        units=common_unit,
        frequency=series_set.frequency,
        missingness_policy=RawNoCleaning(),
        lineage=out_lineage,
    )


__all__ = ["cross_sectional_statistic", "CrossSectionalStatisticError"]
