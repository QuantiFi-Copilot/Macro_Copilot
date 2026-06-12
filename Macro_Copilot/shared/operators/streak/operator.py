"""streak — signed run-length of a Series' sign at each position.

Finance-blind ``single_series_transform`` operator.  Emits, at every
position, the SIGNED length of the current same-sign run: +k on the
k-th consecutive positive value, −k on the k-th consecutive negative
value, 0 on a zero value (a boundary, not a member of either run).
The input is any SIGN-able Series — condition logic composes UPSTREAM
(e.g. ``series_arithmetic(subtract level)`` turns "days above level"
into "days with positive sign"), so the operator stays a pure
structural counter.

DESIGN LOCKS (OPR7, lineage-stamped):
  - sign_rule: >0 positive run | <0 negative run | ==0 boundary
    (zero is NOT a continuation of either side — a run dies at 0 and
    restarts at ±1 after it).
  - nan_policy='break': a NaN position emits NaN (the family's
    legitimate-missingness convention) AND breaks the run — the next
    finite value starts at ±1.  Silently extending a run across a gap
    would overstate persistence.

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind structural method (run-length
    counting); zero finance vocabulary.
  - OPR2      : ONE output artifact type — always a ``Series`` in
    COUNT units (a run length is a row count, whatever the input's
    units — the cross_sectional_rank ordinal precedent).
  - OPR8      : ``params: Optional[StreakParams] = None`` (zero knobs
    in v1; the empty model keeps the surface uniform).
  - OPR9      : one ``Series`` in, one ``Series`` out.
  - OPR10     : one ``OperatorStep``; the design locks ride in params.
  - OPR13     : typed ``StreakError`` refusals: non-Series input; an
    all-NaN input.  No arithmetic on values beyond sign — no overflow
    surface (counts are bounded by the row count).
  - OPR14     : pure + rerun-deterministic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from shared.artifacts.lineage import OperatorStep, sanitize_params_for_lineage
from shared.artifacts.types import Series
from shared.artifacts.units import TimeSeriesUnits
from shared.config.operator_config import (
    OperatorConfig,
    _check_config_identity,
    load_operator_config,
)
from shared.operators.streak.schemas import StreakParams


_OPERATOR_NAME = "streak"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


class StreakError(ValueError):
    """Raised by ``streak`` on a recoverable user-facing failure (a
    ``ValueError`` subclass, OPR13)."""


def streak(
    series: Series,
    *,
    params: Optional[StreakParams] = None,
    config: Optional[OperatorConfig] = None,
) -> Series:
    """Signed run-length of ``series``' sign at each position.

    Parameters
    ----------
    series :
        The input ``Series`` (any units; the SIGN is what is counted —
        compose a subtract upstream for "above level" conditions).
    params :
        Optional ``StreakParams`` (no fields in v1).  ``None`` is
        equivalent.
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    Series
        Signed run lengths on the input index (+k / −k / 0; NaN at
        NaN positions, which also BREAK the run), in COUNT units;
        frequency and missingness policy pass through; lineage
        extended by one ``OperatorStep``.

    Raises
    ------
    StreakError
        The input is not a ``Series``, or it has no finite values.
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
        params = StreakParams()

    # ------------------------------------------------------------------
    # 3. Structural-input validation (OPR9 typed I/O).
    # ------------------------------------------------------------------
    if not isinstance(series, Series):
        raise StreakError(
            "streak: input must be a Series artifact; got "
            f"{type(series).__name__}."
        )

    values = series.payload.to_numpy(dtype=float)
    n_obs = int(len(values))
    if n_obs == 0 or not np.isfinite(values).any():
        raise StreakError(
            f"streak: the input has no finite values ({n_obs} rows)."
        )

    # ------------------------------------------------------------------
    # 4. The run-length count (single pass; sign_rule + nan_policy
    #    design-locked).
    # ------------------------------------------------------------------
    out = np.full(n_obs, np.nan)
    run = 0  # signed: +k / −k; 0 = no active run
    for i in range(n_obs):
        v = values[i]
        if np.isnan(v):
            run = 0  # nan_policy='break': the gap kills the run
            continue  # output stays NaN at the missing position
        if v > 0.0:
            run = run + 1 if run > 0 else 1
        elif v < 0.0:
            run = run - 1 if run < 0 else -1
        else:
            run = 0  # zero is a boundary, not a member of either run
        out[i] = float(run)

    result = pd.Series(out, index=series.payload.index, dtype=float)
    result.name = f"streak__{series.series_key}"

    longest_pos = int(out[np.isfinite(out)].max()) if np.isfinite(out).any() else 0
    longest_neg = int(out[np.isfinite(out)].min()) if np.isfinite(out).any() else 0

    # ------------------------------------------------------------------
    # 5. Lineage — one OperatorStep (OPR10); locks + run extremes ride
    #    in params (data-derived diagnostics, the detrend precedent).
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "sign_rule": ">0 positive run | <0 negative run | ==0 boundary",
        "nan_policy": "break",  # design-locked (OPR7)
        "longest_positive_run": max(longest_pos, 0),
        "longest_negative_run": abs(min(longest_neg, 0)),
        "n_obs": n_obs,
        "input_units": series.units.value,
        "output_units": TimeSeriesUnits.COUNT.value,
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
        series_key=f"streak__{series.series_key}",
        payload=result,
        # A run length is a ROW COUNT whatever the input's units —
        # the cross_sectional_rank ordinal-COUNT precedent (OPR11
        # per-variant override family).
        units=TimeSeriesUnits.COUNT,
        frequency=series.frequency,
        missingness_policy=series.missingness_policy,
        lineage=out_lineage,
    )


__all__ = ["streak", "StreakError"]
