"""top_n — per-date top/bottom-n selection mask over a SeriesSet.

Finance-blind ``cross_sectional`` operator — the screen-membership
step.  At every date, keeps the values of the ``n`` largest
(``mode='top'``) or smallest (``mode='bottom'``) non-NaN members of an
aligned ``SeriesSet`` and masks every other member's value to NaN,
emitting a ``SeriesSet`` with the SAME keys ("the top-n basket per
date, over time").

NaN SEMANTICS (P5 disclosure — read before consuming): an output NaN
means *"this member was NOT selected at this date"* (or the date was
below the member floor) — NOT that upstream data was missing.  Members
that are NaN upstream are non-contenders at that date.  The selection
rule (n, mode, the design-locked tie-break, the floor) is recorded in
lineage params so the mask is fully auditable.

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind structural method (a per-date
    selection by value); zero finance vocabulary or math.
  - OPR2      : ONE operator for both directions — ``mode`` is a
    variant (the cross_sectional_rank ``ascending`` pattern); ONE
    output artifact type — always a ``SeriesSet`` with all input keys.
  - OPR7      : tie-breaking is DESIGN-LOCKED to pandas
    ``rank(method='first')`` — deterministic position-order
    resolution; documented in schemas.py + YAML and recorded in
    lineage (a caller-varied tie rule would break replay identity for
    no analytical gain).
  - OPR8      : ``params: Optional[TopNParams] = None``; defaults
    resolve from ``config.yaml``.
  - OPR9      : one ``SeriesSet`` in, one ``SeriesSet`` out.
  - OPR10     : one ``OperatorStep``; the set-level chain EXTENDS the
    input's; per-key upstreams store the input's COMPOSED chains —
    the ``cross_sectional_rank`` inductive precedent.
  - OPR11     : selection compares VALUES across members, so all
    members MUST share one unit — mixed units refused outright with
    the ``convert_units`` remedy (no opt-out).  Output units =
    passthrough; set-level frequency and the common index pass
    through UNCHANGED (only payloads are masked — dropping dates
    would corrupt the frequency contract).
  - OPR13     : typed ``TopNError`` refusals: non-Set input, fewer
    than 2 members, mixed units, or an all-NaN output.  A date with
    fewer than ``min_members`` non-NaN members emits NaN for every
    member; a date with fewer than ``n`` (but >= floor) valid members
    keeps ALL of them (documented, not an error).
  - OPR12/14  : config name+version identity checked; pure + rerun-
    deterministic (the design-locked tie-break makes selection
    deterministic under ties).
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
from shared.operators.top_n.schemas import TopNParams


_OPERATOR_NAME = "top_n"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

# DESIGN-LOCKED tie-break (OPR7): pandas rank(method='first') resolves
# ties by position order — deterministic, replay-stable.  Documented in
# schemas.py and recorded in lineage params.
_TIE_METHOD = "first"


class TopNError(ValueError):
    """Raised by ``top_n`` on a recoverable user-facing failure (a
    ``ValueError`` subclass, OPR13)."""


def top_n(
    series_set: SeriesSet,
    *,
    params: Optional[TopNParams] = None,
    config: Optional[OperatorConfig] = None,
) -> SeriesSet:
    """Keep the per-date top/bottom-n members; mask the rest to NaN.

    Parameters
    ----------
    series_set :
        An aligned ``SeriesSet`` (canonically from ``align_series``)
        whose members all share one unit.
    params :
        Optional ``TopNParams``.  When ``None`` every field is
        resolved from the bundled ``config.yaml`` (OPR8).
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    SeriesSet
        Same keys, common index and frequency; each member's payload
        keeps its value at the dates where it is among the per-date
        top/bottom n and is NaN elsewhere (selection mask — see the
        module docstring for the NaN disclosure).  Units passthrough;
        missingness fresh (``RawNoCleaning``); the set-level lineage
        extends the input's chain with the full selection rule
        recorded.

    Raises
    ------
    TopNError
        The input is not a ``SeriesSet``; it has fewer than 2 members;
        its members carry mixed units; or every date's selection is
        empty (all-NaN output).
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
        params = TopNParams(
            n=int(config.default_value("n")),
            mode=config.default_value("mode"),
            min_members=int(config.default_value("min_members")),
        )

    # ------------------------------------------------------------------
    # 3. Structural-input validation (OPR9 typed I/O; OPR11 units).
    # ------------------------------------------------------------------
    if not isinstance(series_set, SeriesSet):
        raise TopNError(
            "top_n: input must be a SeriesSet artifact; got "
            f"{type(series_set).__name__}.  Build one upstream with "
            "align_series."
        )

    keys = series_set.keys()
    if len(keys) < 2:
        raise TopNError(
            f"top_n: the set has {len(keys)} member(s); a per-date "
            "selection needs at least 2 members."
        )

    distinct_units = {u.value for u in series_set.units_by_key.values()}
    if len(distinct_units) > 1:
        raise TopNError(
            "top_n: the set's members carry mixed units "
            f"({sorted(distinct_units)}).  Selecting by value compares "
            "VALUES across members, so mixed units have no honest "
            "interpretation — insert convert_units on the offending "
            "members upstream (the sole unit-transition site)."
        )
    common_unit = TimeSeriesUnits(next(iter(distinct_units)))

    # ------------------------------------------------------------------
    # 4. Per-date selection: rank with the design-locked deterministic
    #    tie-break; keep rank <= n; below-floor dates mask everything.
    # ------------------------------------------------------------------
    frame = pd.DataFrame(
        {k: series_set.series_by_key[k] for k in keys},
        index=series_set.common_index,
    )
    n_valid = frame.notna().sum(axis=1)
    ranks = frame.rank(
        axis=1,
        method=_TIE_METHOD,
        ascending=(params.mode == "bottom"),
    )
    selected = ranks <= params.n
    masked = frame.where(selected)
    masked[n_valid < params.min_members] = np.nan

    n_finite = int(np.isfinite(masked.to_numpy(dtype=float)).sum())
    if n_finite == 0:
        raise TopNError(
            f"top_n: produced an all-NaN output ({len(frame)} dates × "
            f"{len(keys)} members, n={params.n}, mode={params.mode!r}, "
            f"min_members={params.min_members}).  No date had enough "
            "non-NaN members."
        )

    # ------------------------------------------------------------------
    # 5. Lineage — the cross_sectional_rank inductive precedent (OPR10);
    #    the FULL selection rule is recorded so the NaN mask is
    #    auditable (P5).
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "n": params.n,
        "mode": params.mode,
        "tie_method": _TIE_METHOD,  # design-locked (OPR7)
        "min_members": params.min_members,
        "selection_semantics": (
            "output NaN means NOT SELECTED at that date (or the date "
            "was below min_members) — not upstream missingness; "
            "upstream-NaN members are non-contenders"
        ),
        "n_members": len(keys),
        "n_dates": int(len(frame)),
        "member_keys": sorted(keys),
        "member_units": sorted(distinct_units),
    }
    select_step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params=sanitize_params_for_lineage(step_params),
        input_hashes=(series_set.lineage.head_hash,),
    )
    out_set_lineage = series_set.lineage.append(select_step)

    # ART9 LIN-2 invariant: each out-upstream carries the INPUT producer's
    # step but NOT this top_n step — get_series appends self.lineage.steps[-1]
    # (top_n).  Appending top_n here too would double-append downstream.
    input_head_step = series_set.lineage.steps[-1]
    out_upstream_by_key = {
        k: series_set.upstream_lineage_by_key[k].append(input_head_step)
        for k in keys
    }

    return SeriesSet(
        series_by_key={k: masked[k] for k in keys},
        units_by_key={k: common_unit for k in keys},
        missingness_by_key={k: RawNoCleaning() for k in keys},
        upstream_lineage_by_key=out_upstream_by_key,
        common_index=series_set.common_index,
        frequency=series_set.frequency,
        lineage=out_set_lineage,
    )


__all__ = ["top_n", "TopNError"]
