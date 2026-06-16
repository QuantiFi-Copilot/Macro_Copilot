"""cross_sectional_rank — per-date rank of each member of a SeriesSet.

Finance-blind ``cross_sectional`` operator — the morning-screen core.
At every date, ranks each member of an aligned ``SeriesSet`` among the
cross-section of all members (the universe), emitting a ``SeriesSet``
with the same keys whose member payloads are the per-date ranks.  The
North-Star ingredient: "…ranked across the universe".

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind structural method (a rank); zero
    finance vocabulary or math.
  - OPR2      : ONE output artifact type — always a ``SeriesSet``
    (every input key present; ranks per date).  Only the UNITS tag
    follows the ``rank_method`` variant (ordinal → COUNT, normalized →
    PCT_RANK) — the conditional_aggregate COUNT-vs-passthrough
    precedent.
  - OPR8      : ``params: Optional[CrossSectionalRankParams] = None``;
    defaults resolve from ``config.yaml``.
  - OPR9      : one ``SeriesSet`` in, one ``SeriesSet`` out (closed
    family).
  - OPR10     : one ``OperatorStep`` via ``.build``; the output's
    set-level chain EXTENDS the input set's chain (connected via the
    set head in ``input_hashes``); each output member's
    ``upstream_lineage_by_key`` stores the input's COMPOSED per-key
    chain (the stored upstream + the input set's head step) so that
    ``get_series`` appends this rank step to a complete chain —
    the inductive pattern ``align_series``/``get_series`` establish.
  - OPR11     : ranking compares VALUES across members, so all members
    MUST share one unit — mixed units are refused outright with the
    ``convert_units`` remedy (no opt-out: a mixed-unit ranking has no
    honest interpretation).  Set-level frequency passes through.
  - OPR13     : every recoverable failure raises
    ``CrossSectionalRankError`` (a ``ValueError`` subclass): non-Set
    input, fewer than 2 members, mixed units, or an all-NaN output.
    A date with fewer than ``min_members`` non-NaN members emits NaN
    for every member at that date (legitimate per-date missingness);
    a member that is NaN at a date gets a NaN rank there, and the
    remaining members are ranked among themselves.
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
from shared.operators.cross_sectional_rank.schemas import (
    CrossSectionalRankParams,
)


_OPERATOR_NAME = "cross_sectional_rank"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


class CrossSectionalRankError(ValueError):
    """Raised by ``cross_sectional_rank`` on a recoverable user-facing
    failure (a ``ValueError`` subclass, OPR13)."""


def cross_sectional_rank(
    series_set: SeriesSet,
    *,
    params: Optional[CrossSectionalRankParams] = None,
    config: Optional[OperatorConfig] = None,
) -> SeriesSet:
    """Rank each member of ``series_set`` among the cross-section.

    Parameters
    ----------
    series_set :
        An aligned ``SeriesSet`` (canonically from ``align_series``)
        whose members all share one unit.
    params :
        Optional ``CrossSectionalRankParams``.  When ``None`` every
        field is resolved from the bundled ``config.yaml`` (OPR8).
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    SeriesSet
        Same keys and common index; each member's payload is its
        per-date rank among the non-NaN members (ordinal 1..N in COUNT
        units, or normalized 0–100 in PCT_RANK units).  Missingness is
        fresh (``RawNoCleaning`` — ranks are fresh derivations);
        frequency passes through; the set-level lineage extends the
        input's chain by this one step.

    Raises
    ------
    CrossSectionalRankError
        The input is not a ``SeriesSet``; it has fewer than 2 members;
        its members carry mixed units; or every date's ranks are NaN
        (no date had ``min_members`` non-NaN members).
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
        params = CrossSectionalRankParams(
            rank_method=config.default_value("rank_method"),
            ascending=bool(config.default_value("ascending")),
            ties=config.default_value("ties"),
            min_members=int(config.default_value("min_members")),
        )

    # ------------------------------------------------------------------
    # 3. Structural-input validation (OPR9 typed I/O; OPR11 units).
    # ------------------------------------------------------------------
    if not isinstance(series_set, SeriesSet):
        raise CrossSectionalRankError(
            "cross_sectional_rank: input must be a SeriesSet artifact; "
            f"got {type(series_set).__name__}.  Build one upstream with "
            "align_series."
        )

    keys = series_set.keys()
    if len(keys) < 2:
        raise CrossSectionalRankError(
            f"cross_sectional_rank: the set has {len(keys)} member(s); "
            "a cross-sectional rank needs at least 2 members to be "
            "meaningful."
        )

    distinct_units = {u.value for u in series_set.units_by_key.values()}
    if len(distinct_units) > 1:
        raise CrossSectionalRankError(
            "cross_sectional_rank: the set's members carry mixed units "
            f"({sorted(distinct_units)}).  Ranking compares VALUES "
            "across members, so mixed units have no honest "
            "interpretation — insert convert_units on the offending "
            "members upstream (the sole unit-transition site)."
        )

    # ------------------------------------------------------------------
    # 4. Rank per date across the members (pandas DataFrame.rank,
    #    axis=1 — the methodology reference recorded in config.yaml).
    # ------------------------------------------------------------------
    frame = pd.DataFrame(
        {k: series_set.series_by_key[k] for k in keys},
        index=series_set.common_index,
    )
    n_valid = frame.notna().sum(axis=1)

    if params.rank_method == "ordinal":
        ranks = frame.rank(
            axis=1, method=params.ties, ascending=params.ascending,
        )
        out_units = TimeSeriesUnits.COUNT
    else:  # normalized
        ranks = frame.rank(
            axis=1, method=params.ties, ascending=params.ascending,
            pct=True,
        ) * 100.0
        out_units = TimeSeriesUnits.PCT_RANK

    # Dates with fewer than min_members non-NaN members carry no
    # meaningful cross-section — NaN every member there.
    ranks[n_valid < params.min_members] = np.nan

    n_finite = int(np.isfinite(ranks.to_numpy(dtype=float)).sum())
    if n_finite == 0:
        raise CrossSectionalRankError(
            f"cross_sectional_rank: produced an all-NaN output "
            f"({len(frame)} dates × {len(keys)} members, "
            f"min_members={params.min_members}).  No date had enough "
            "non-NaN members — check the upstream alignment."
        )

    # ------------------------------------------------------------------
    # 5. Lineage — ONE OperatorStep; the set-level chain EXTENDS the
    #    input set's chain; per-key upstreams store the input's
    #    COMPOSED per-key chain so get_series appends this step to a
    #    complete provenance (OPR10; the align_series/get_series
    #    inductive pattern).
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "rank_method": params.rank_method,
        "ascending": params.ascending,
        "ties": params.ties,
        "min_members": params.min_members,
        "n_members": len(keys),
        "n_dates": int(len(frame)),
        "member_keys": sorted(keys),
        "member_units": sorted(distinct_units),
    }
    rank_step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params=sanitize_params_for_lineage(step_params),
        input_hashes=(series_set.lineage.head_hash,),
    )
    out_set_lineage = series_set.lineage.append(rank_step)

    # ART9 LIN-2 invariant: each out-upstream carries the INPUT producer's
    # step but NOT this rank step — get_series appends self.lineage.steps[-1]
    # (rank).  Appending rank here too would double-append downstream.
    input_head_step = series_set.lineage.steps[-1]
    out_upstream_by_key = {
        k: series_set.upstream_lineage_by_key[k].append(input_head_step)
        for k in keys
    }

    return SeriesSet(
        series_by_key={k: ranks[k] for k in keys},
        units_by_key={k: out_units for k in keys},
        # Ranks are fresh derivations; NaN means "member missing or
        # cross-section below the member floor at that date".
        missingness_by_key={k: RawNoCleaning() for k in keys},
        upstream_lineage_by_key=out_upstream_by_key,
        common_index=series_set.common_index,
        frequency=series_set.frequency,
        lineage=out_set_lineage,
    )


__all__ = ["cross_sectional_rank", "CrossSectionalRankError"]
