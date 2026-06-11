"""cross_sectional_zscore — per-date z-score of each member vs the set.

Finance-blind ``cross_sectional`` operator.  At every date,
standardises each member of an aligned ``SeriesSet`` against the
cross-section of all members — z = (member − cross_mean) / cross_std —
emitting a ``SeriesSet`` with the same keys whose payloads are the
per-date z-scores ("how far is each member from its peers today").

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind statistical method; zero finance
    vocabulary or math.
  - OPR2      : ONE output artifact type — always a ``SeriesSet``
    (every input key present; Z_SCORE units on every member).
  - OPR8      : ``params: Optional[CrossSectionalZscoreParams] = None``;
    defaults resolve from ``config.yaml``.
  - OPR9      : one ``SeriesSet`` in, one ``SeriesSet`` out.
  - OPR10     : one ``OperatorStep``; the set-level chain EXTENDS the
    input's; per-key upstreams store the input's COMPOSED chains so
    ``get_series`` appends this step to complete provenance — the
    ``cross_sectional_rank`` inductive precedent.
  - OPR11     : standardisation compares VALUES across members, so all
    members MUST share one unit — mixed units refused outright with
    the ``convert_units`` remedy (no opt-out).  Output is Z_SCORE on
    every key; set-level frequency passes through.
  - OPR13     : typed ``CrossSectionalZscoreError`` refusals: non-Set
    input, fewer than 2 members, mixed units, or an all-NaN output.
    A date with fewer than ``min_members`` non-NaN members, or with
    zero cross-sectional dispersion (all members equal — z undefined),
    emits NaN for every member at that date (legitimate per-date
    missingness; the rolling-operator zero-variance precedent).  The
    std≤0 mask runs BEFORE division, so ±Inf is unreachable.
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
from shared.operators.cross_sectional_zscore.schemas import (
    CrossSectionalZscoreParams,
)


_OPERATOR_NAME = "cross_sectional_zscore"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


class CrossSectionalZscoreError(ValueError):
    """Raised by ``cross_sectional_zscore`` on a recoverable
    user-facing failure (a ``ValueError`` subclass, OPR13)."""


def cross_sectional_zscore(
    series_set: SeriesSet,
    *,
    params: Optional[CrossSectionalZscoreParams] = None,
    config: Optional[OperatorConfig] = None,
) -> SeriesSet:
    """Standardise each member of ``series_set`` against the cross-section.

    Parameters
    ----------
    series_set :
        An aligned ``SeriesSet`` (canonically from ``align_series``)
        whose members all share one unit.
    params :
        Optional ``CrossSectionalZscoreParams``.  When ``None`` every
        field is resolved from the bundled ``config.yaml`` (OPR8).
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    SeriesSet
        Same keys and common index; each member's payload is its
        per-date z-score vs the cross-section (Z_SCORE units).
        Missingness is fresh (``RawNoCleaning``); frequency passes
        through; the set-level lineage extends the input's chain.

    Raises
    ------
    CrossSectionalZscoreError
        The input is not a ``SeriesSet``; it has fewer than 2 members;
        its members carry mixed units; or every date's z-scores are
        NaN.
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
        params = CrossSectionalZscoreParams(
            ddof=int(config.default_value("ddof")),
            min_members=int(config.default_value("min_members")),
        )

    # ------------------------------------------------------------------
    # 3. Structural-input validation (OPR9 typed I/O; OPR11 units).
    # ------------------------------------------------------------------
    if not isinstance(series_set, SeriesSet):
        raise CrossSectionalZscoreError(
            "cross_sectional_zscore: input must be a SeriesSet artifact; "
            f"got {type(series_set).__name__}.  Build one upstream with "
            "align_series."
        )

    keys = series_set.keys()
    if len(keys) < 2:
        raise CrossSectionalZscoreError(
            f"cross_sectional_zscore: the set has {len(keys)} member(s); "
            "a cross-sectional z-score needs at least 2 members."
        )

    distinct_units = {u.value for u in series_set.units_by_key.values()}
    if len(distinct_units) > 1:
        raise CrossSectionalZscoreError(
            "cross_sectional_zscore: the set's members carry mixed units "
            f"({sorted(distinct_units)}).  Standardising compares VALUES "
            "across members, so mixed units have no honest "
            "interpretation — insert convert_units on the offending "
            "members upstream (the sole unit-transition site)."
        )

    # ------------------------------------------------------------------
    # 4. Per-date standardisation across the members.
    # ------------------------------------------------------------------
    frame = pd.DataFrame(
        {k: series_set.series_by_key[k] for k in keys},
        index=series_set.common_index,
    )
    n_valid = frame.notna().sum(axis=1)
    cross_mean = frame.mean(axis=1)
    cross_std = frame.std(axis=1, ddof=params.ddof)

    # Zero-dispersion dates (all members equal) make z undefined; mask
    # the std to NaN BEFORE dividing so ±Inf is unreachable (the
    # rolling-operator zero-variance → NaN precedent).
    cross_std = cross_std.where(cross_std > 0.0)

    z = frame.sub(cross_mean, axis=0).div(cross_std, axis=0)
    z[n_valid < params.min_members] = np.nan

    n_finite = int(np.isfinite(z.to_numpy(dtype=float)).sum())
    if n_finite == 0:
        raise CrossSectionalZscoreError(
            f"cross_sectional_zscore: produced an all-NaN output "
            f"({len(frame)} dates × {len(keys)} members, "
            f"min_members={params.min_members}, ddof={params.ddof}).  No "
            "date had enough non-NaN members with non-zero dispersion."
        )

    # ------------------------------------------------------------------
    # 5. Lineage — the cross_sectional_rank inductive precedent (OPR10).
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "ddof": params.ddof,
        "min_members": params.min_members,
        "n_members": len(keys),
        "n_dates": int(len(frame)),
        "member_keys": sorted(keys),
        "member_units": sorted(distinct_units),
    }
    z_step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params=sanitize_params_for_lineage(step_params),
        input_hashes=(series_set.lineage.head_hash,),
    )
    out_set_lineage = series_set.lineage.append(z_step)

    input_head_step = series_set.lineage.steps[-1]
    out_upstream_by_key = {
        k: series_set.upstream_lineage_by_key[k].append(input_head_step)
        for k in keys
    }

    return SeriesSet(
        series_by_key={k: z[k] for k in keys},
        units_by_key={k: TimeSeriesUnits.Z_SCORE for k in keys},
        missingness_by_key={k: RawNoCleaning() for k in keys},
        upstream_lineage_by_key=out_upstream_by_key,
        common_index=series_set.common_index,
        frequency=series_set.frequency,
        lineage=out_set_lineage,
    )


__all__ = ["cross_sectional_zscore", "CrossSectionalZscoreError"]
