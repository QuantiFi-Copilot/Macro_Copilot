"""pairwise_spread_matrix — all pairwise differences of a SeriesSet.

Finance-blind ``cross_sectional`` operator.  At every date, computes
member_i − member_j for every UNORDERED pair of an aligned
``SeriesSet``'s members (upper triangle over sorted keys, i < j) and
emits ONE wide ``Panel`` — rows = the shared dates, one column per
pair, named ``<ki>__minus__<kj>`` — the full relative-positioning
matrix over time.

DESIGN LOCKS (OPR7 — documented here, recorded in lineage, not knobs):

  - pair enumeration: upper triangle over SORTED keys (i < j); the
    reverse pair is the exact negation, so emitting both would double
    the width for zero information;
  - direction convention: column ``a__minus__b`` is a − b;
  - column naming: ``<ki>__minus__<kj>`` — deterministic and
    direction-transparent;
  - member ceiling: at most ``_MAX_MEMBERS`` members (N members
    produce N(N−1)/2 columns; an unbounded width is dishonest output
    and computationally hostile — summarise larger universes with
    cross_sectional_statistic instead).

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind structural method; zero finance
    vocabulary (column names are key algebra, never interpretation).
  - OPR2      : ONE output artifact type — always a ``Panel``.
  - OPR8      : ``params: Optional[...] = None``; the Params class is
    deliberately empty (no honest knobs exist) and the YAML
    ``defaults:`` block is explicitly empty.
  - OPR9      : one ``SeriesSet`` in, one ``Panel`` out (closed
    family).
  - OPR10     : one ``OperatorStep``; the Panel's lineage is the input
    set's chain EXTENDED by this step (the set-producing step already
    folds every member's head hash — the cross_sectional_statistic
    honesty rationale).
  - OPR11     : differences require ONE unit across all members —
    mixed units refused outright with the ``convert_units`` remedy
    (no opt-out).  Every pair column carries that common unit in
    ``units_by_column``.
  - OPR13     : typed ``PairwiseSpreadMatrixError`` refusals: non-Set
    input, fewer than 2 members, more than ``_MAX_MEMBERS`` members,
    mixed units, an overflowing (±Inf) difference (subtraction of
    extreme finite values is not closed over the float range — the
    demean_cross_section precedent), or an all-NaN output.  A pair
    value is NaN exactly where either member is NaN (pairwise
    propagation — legitimate missingness).
  - OPR12/14  : config name+version identity checked; pure + rerun-
    deterministic.
"""

from __future__ import annotations

from itertools import combinations
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from shared.artifacts.lineage import OperatorStep, sanitize_params_for_lineage
from shared.artifacts.missingness import RawNoCleaning
from shared.artifacts.types import Panel, SeriesSet
from shared.artifacts.units import TimeSeriesUnits
from shared.config.operator_config import (
    OperatorConfig,
    _check_config_identity,
    load_operator_config,
)
from shared.operators.pairwise_spread_matrix.schemas import (
    PairwiseSpreadMatrixParams,
)


_OPERATOR_NAME = "pairwise_spread_matrix"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

# DESIGN-LOCKED member ceiling (OPR7): N members emit N(N−1)/2 columns
# (50 → 1,225; 78 → 3,003).  Beyond the ceiling the matrix stops being
# honest, readable output; summarise larger universes with
# cross_sectional_statistic.  Recorded in lineage params.
_MAX_MEMBERS = 50


class PairwiseSpreadMatrixError(ValueError):
    """Raised by ``pairwise_spread_matrix`` on a recoverable
    user-facing failure (a ``ValueError`` subclass, OPR13)."""


def pairwise_spread_matrix(
    series_set: SeriesSet,
    *,
    params: Optional[PairwiseSpreadMatrixParams] = None,
    config: Optional[OperatorConfig] = None,
) -> Panel:
    """Compute every pairwise difference of ``series_set``'s members.

    Parameters
    ----------
    series_set :
        An aligned ``SeriesSet`` (canonically from ``align_series``)
        whose members all share one unit; 2–50 members.
    params :
        Optional ``PairwiseSpreadMatrixParams`` (deliberately empty —
        every structural choice is design-locked; see the module
        docstring).
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    Panel
        Rows = the shared dates; one column per unordered pair
        (``<ki>__minus__<kj>``, sorted-key upper triangle, value =
        ki − kj); every column in the members' common unit; fresh
        ``RawNoCleaning`` missingness; lineage = the input set's chain
        extended by this step.

    Raises
    ------
    PairwiseSpreadMatrixError
        The input is not a ``SeriesSet``; it has fewer than 2 or more
        than 50 members; its members carry mixed units; a difference
        overflows the float range; or every pair value is NaN.
    """
    # ------------------------------------------------------------------
    # 1. Load config + identity check — OPR12.
    # ------------------------------------------------------------------
    if config is None:
        config = load_operator_config(_CONFIG_PATH)
    _check_config_identity(config, _OPERATOR_NAME, _OPERATOR_VERSION)

    # ------------------------------------------------------------------
    # 2. Resolve params — OPR8 (the class is empty; instantiation keeps
    #    the uniform ABI and the frozen/extra-forbid guarantees).
    # ------------------------------------------------------------------
    if params is None:
        params = PairwiseSpreadMatrixParams()

    # ------------------------------------------------------------------
    # 3. Structural-input validation (OPR9 typed I/O; OPR11 units).
    # ------------------------------------------------------------------
    if not isinstance(series_set, SeriesSet):
        raise PairwiseSpreadMatrixError(
            "pairwise_spread_matrix: input must be a SeriesSet artifact; "
            f"got {type(series_set).__name__}.  Build one upstream with "
            "align_series."
        )

    keys = series_set.keys()
    if len(keys) < 2:
        raise PairwiseSpreadMatrixError(
            f"pairwise_spread_matrix: the set has {len(keys)} member(s); "
            "pairwise differences need at least 2 members."
        )
    if len(keys) > _MAX_MEMBERS:
        n_cols = len(keys) * (len(keys) - 1) // 2
        raise PairwiseSpreadMatrixError(
            f"pairwise_spread_matrix: the set has {len(keys)} members, "
            f"which would emit {n_cols} pair columns; the design-locked "
            f"ceiling is {_MAX_MEMBERS} members.  Summarise larger "
            "universes with cross_sectional_statistic, or select a "
            "subset upstream (top_n / select_from_series_set)."
        )

    distinct_units = {u.value for u in series_set.units_by_key.values()}
    if len(distinct_units) > 1:
        raise PairwiseSpreadMatrixError(
            "pairwise_spread_matrix: the set's members carry mixed "
            f"units ({sorted(distinct_units)}).  A pairwise difference "
            "requires one unit — insert convert_units on the offending "
            "members upstream (the sole unit-transition site)."
        )
    common_unit = TimeSeriesUnits(next(iter(distinct_units)))

    # ------------------------------------------------------------------
    # 4. Upper-triangle pairwise differences over sorted keys.
    # ------------------------------------------------------------------
    pairs = list(combinations(keys, 2))  # keys() is sorted → i < j
    columns: Dict[str, pd.Series] = {
        f"{ki}__minus__{kj}": (
            series_set.series_by_key[ki] - series_set.series_by_key[kj]
        )
        for ki, kj in pairs
    }
    payload = pd.DataFrame(columns, index=series_set.common_index)

    # Overflow honesty (the demean_cross_section precedent):
    # subtraction of extreme finite values is not closed over the float
    # range; ±Inf is forbidden in any payload (ART11) and must be a
    # typed refusal, never a raw constructor crash.
    if np.isinf(payload.to_numpy(dtype=float)).any():
        raise PairwiseSpreadMatrixError(
            "pairwise_spread_matrix: a pairwise difference overflows "
            "the float range (numerical failure, not missingness)."
        )

    if int(np.isfinite(payload.to_numpy(dtype=float)).sum()) == 0:
        raise PairwiseSpreadMatrixError(
            f"pairwise_spread_matrix: produced an all-NaN output "
            f"({len(payload)} dates × {len(pairs)} pairs).  No date had "
            "an overlapping non-NaN member pair."
        )

    # ------------------------------------------------------------------
    # 5. Lineage — one OperatorStep extending the input set's chain
    #    (OPR10); the design locks are recorded so the matrix is
    #    auditable.
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "pair_enumeration": "upper triangle over sorted keys (i < j)",
        "direction_convention": "column 'a__minus__b' is a − b",
        "max_members": _MAX_MEMBERS,  # design-locked (OPR7)
        "n_members": len(keys),
        "n_pairs": len(pairs),
        "n_dates": int(len(payload)),
        "member_keys": sorted(keys),
        "member_units": sorted(distinct_units),
    }
    spread_step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params=sanitize_params_for_lineage(step_params),
        input_hashes=(series_set.lineage.head_hash,),
    )
    out_lineage = series_set.lineage.append(spread_step)

    return Panel(
        payload=payload,
        units_by_column={c: common_unit for c in payload.columns},
        missingness_policy=RawNoCleaning(),
        lineage=out_lineage,
    )


__all__ = ["pairwise_spread_matrix", "PairwiseSpreadMatrixError"]
