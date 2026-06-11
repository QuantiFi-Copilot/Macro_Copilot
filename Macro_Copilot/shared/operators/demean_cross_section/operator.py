"""demean_cross_section — per-date demeaning of a SeriesSet's members.

Finance-blind ``cross_sectional`` operator.  At every date, subtracts
the cross-sectional mean of the non-NaN members from each member —
emitting a ``SeriesSet`` with the same keys whose payloads are the
"vs peers" relative values (member − peer-group average), in the
members' own unit.

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind structural method (centering); zero
    finance vocabulary or math.
  - OPR2      : ONE output artifact type — always a ``SeriesSet``
    (every input key present; units passthrough on every member —
    BPS − BPS = BPS).  Mean centering only; robust (median) centering
    is a declared planned extension.
  - OPR8      : ``params: Optional[DemeanCrossSectionParams] = None``;
    defaults resolve from ``config.yaml``.
  - OPR9      : one ``SeriesSet`` in, one ``SeriesSet`` out.
  - OPR10     : one ``OperatorStep``; the set-level chain EXTENDS the
    input's; per-key upstreams store the input's COMPOSED chains so
    ``get_series`` appends this step — the ``cross_sectional_rank``
    inductive precedent.
  - OPR11     : centering subtracts a cross-member mean, so all
    members MUST share one unit — mixed units refused outright with
    the ``convert_units`` remedy (no opt-out).  Output units =
    passthrough of that common unit; set-level frequency passes
    through.
  - OPR13     : typed ``DemeanCrossSectionError`` refusals: non-Set
    input, fewer than 2 members, mixed units, an overflowing (±Inf)
    demeaned value (subtraction of extreme finite values is not
    closed over the float range — the cross_sectional_statistic
    precedent), or an all-NaN output.  A date with fewer than
    ``min_members`` non-NaN members emits NaN for every member.
    NOTE (deliberate divergence from the zscore sibling): an
    all-equal date demeans to a legitimate all-0.0 row — there is NO
    dispersion barrier here, and masking it to NaN would be
    dishonest.
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
from shared.artifacts.types import SeriesSet
from shared.artifacts.units import TimeSeriesUnits
from shared.config.operator_config import (
    OperatorConfig,
    _check_config_identity,
    load_operator_config,
)
from shared.operators.demean_cross_section.schemas import (
    DemeanCrossSectionParams,
)


_OPERATOR_NAME = "demean_cross_section"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


class DemeanCrossSectionError(ValueError):
    """Raised by ``demean_cross_section`` on a recoverable user-facing
    failure (a ``ValueError`` subclass, OPR13)."""


def demean_cross_section(
    series_set: SeriesSet,
    *,
    params: Optional[DemeanCrossSectionParams] = None,
    config: Optional[OperatorConfig] = None,
) -> SeriesSet:
    """Subtract the per-date cross-sectional mean from each member.

    Parameters
    ----------
    series_set :
        An aligned ``SeriesSet`` (canonically from ``align_series``)
        whose members all share one unit.
    params :
        Optional ``DemeanCrossSectionParams``.  When ``None`` every
        field is resolved from the bundled ``config.yaml`` (OPR8).
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    SeriesSet
        Same keys and common index; each member's payload is
        member − cross-mean per date, in the members' common unit
        (passthrough).  Missingness is fresh (``RawNoCleaning``);
        frequency passes through; the set-level lineage extends the
        input's chain.

    Raises
    ------
    DemeanCrossSectionError
        The input is not a ``SeriesSet``; it has fewer than 2 members;
        its members carry mixed units; a demeaned value overflows the
        float range; or every date's values are NaN.
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
        params = DemeanCrossSectionParams(
            min_members=int(config.default_value("min_members")),
        )

    # ------------------------------------------------------------------
    # 3. Structural-input validation (OPR9 typed I/O; OPR11 units).
    # ------------------------------------------------------------------
    if not isinstance(series_set, SeriesSet):
        raise DemeanCrossSectionError(
            "demean_cross_section: input must be a SeriesSet artifact; "
            f"got {type(series_set).__name__}.  Build one upstream with "
            "align_series."
        )

    keys = series_set.keys()
    if len(keys) < 2:
        raise DemeanCrossSectionError(
            f"demean_cross_section: the set has {len(keys)} member(s); "
            "demeaning against peers needs at least 2 members (a "
            "1-member set demeans identically to zero)."
        )

    distinct_units = {u.value for u in series_set.units_by_key.values()}
    if len(distinct_units) > 1:
        raise DemeanCrossSectionError(
            "demean_cross_section: the set's members carry mixed units "
            f"({sorted(distinct_units)}).  Subtracting a cross-member "
            "mean compares VALUES, so mixed units have no honest "
            "interpretation — insert convert_units on the offending "
            "members upstream (the sole unit-transition site)."
        )
    common_unit = TimeSeriesUnits(next(iter(distinct_units)))

    # ------------------------------------------------------------------
    # 4. Per-date demeaning across the members.  NOTE: no dispersion
    #    barrier — an all-equal date demeans to a legitimate 0.0 row.
    # ------------------------------------------------------------------
    frame = pd.DataFrame(
        {k: series_set.series_by_key[k] for k in keys},
        index=series_set.common_index,
    )
    n_valid = frame.notna().sum(axis=1)
    demeaned = frame.sub(frame.mean(axis=1), axis=0)
    demeaned[n_valid < params.min_members] = np.nan

    # Overflow honesty (the cross_sectional_statistic precedent):
    # member − cross-mean of extreme finite values CAN overflow to ±Inf
    # (subtraction is not closed over the float range); ±Inf is
    # forbidden in any payload (ART11) and must be a typed refusal,
    # never a raw constructor crash or mislabelled missingness.
    if np.isinf(demeaned.to_numpy(dtype=float)).any():
        raise DemeanCrossSectionError(
            "demean_cross_section: a demeaned value overflows the float "
            "range (numerical failure, not missingness)."
        )

    n_finite = int(np.isfinite(demeaned.to_numpy(dtype=float)).sum())
    if n_finite == 0:
        raise DemeanCrossSectionError(
            f"demean_cross_section: produced an all-NaN output "
            f"({len(frame)} dates × {len(keys)} members, "
            f"min_members={params.min_members}).  No date had enough "
            "non-NaN members."
        )

    # ------------------------------------------------------------------
    # 5. Lineage — the cross_sectional_rank inductive precedent (OPR10).
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "min_members": params.min_members,
        "n_members": len(keys),
        "n_dates": int(len(frame)),
        "member_keys": sorted(keys),
        "member_units": sorted(distinct_units),
    }
    demean_step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params=sanitize_params_for_lineage(step_params),
        input_hashes=(series_set.lineage.head_hash,),
    )
    out_set_lineage = series_set.lineage.append(demean_step)

    input_head_step = series_set.lineage.steps[-1]
    out_upstream_by_key = {
        k: series_set.upstream_lineage_by_key[k].append(input_head_step)
        for k in keys
    }

    return SeriesSet(
        series_by_key={k: demeaned[k] for k in keys},
        units_by_key={k: common_unit for k in keys},
        missingness_by_key={k: RawNoCleaning() for k in keys},
        upstream_lineage_by_key=out_upstream_by_key,
        common_index=series_set.common_index,
        frequency=series_set.frequency,
        lineage=out_set_lineage,
    )


__all__ = ["demean_cross_section", "DemeanCrossSectionError"]
