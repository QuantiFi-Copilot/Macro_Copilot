"""rolling_regression — finance-blind rolling-OLS over two typed Series.

Wraps ``shared.analytics.regression.rolling_ols`` (the same compute
primitive used by the ``rolling_regression`` sovereign-bond tool) so
methodology cannot drift between the operator-graph surface and the
standalone-primitive surface.

Inputs / outputs
----------------
Inputs:
  - ``lhs: Series``     dependent / target series
  - ``rhs: Series``     single-regressor independent series

Output:
  - ``SeriesSet`` with keys ``{"beta", "alpha", "r_squared"}``.
    Per-key units propagate per the methodology table:

        beta       : RATIO       (effective_lhs_unit / effective_rhs_unit;
                                  V1 requires lhs_basis == rhs_basis so
                                  the ratio is unambiguous)
        alpha      : effective lhs unit  (PERCENT→BPS transition when
                                  basis=level_change on a PERCENT input)
        r_squared  : RATIO       (dimensionless [0, 1])

Lineage contract
----------------
The SeriesSet's own ``lineage`` head is this operator's
``OperatorStep``.  Per-key ``upstream_lineage_by_key``:

  - ``beta``       : lhs.lineage  (target's chain)
  - ``alpha``      : lhs.lineage  (intercept tracks the target)
  - ``r_squared``  : lhs.lineage  (R² tracks the fit on the target)

The rhs's lineage is captured as an ``auxiliary_lineages`` entry on
the operator step so downstream lineage walkers can recover the
regressor chain.  Mirrors the discipline ``series_arithmetic`` and
``apply_mask`` established for binary operators.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pandas as pd

from shared.analytics.regression import rolling_ols
from shared.artifacts.lineage import Lineage, OperatorStep
from shared.artifacts.types import Series, SeriesSet
from shared.artifacts.units import TimeSeriesUnits
from shared.config.operator_config import (
    OperatorConfig,
    OperatorConfigError,
    _check_config_identity,
    load_operator_config,
)
from shared.operators.rolling_regression.schemas import (
    RollingRegressionParams,
)


_OPERATOR_NAME = "rolling_regression"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


class RollingRegressionError(ValueError):
    """Raised by ``rolling_regression`` on a recoverable user-facing
    failure (empty intersection, basis mismatch, unsupported unit
    combination)."""


def _effective_unit(unit: TimeSeriesUnits, basis: str) -> TimeSeriesUnits:
    """Map (input_unit, basis) → effective unit after the basis
    transition.  Same methodology-owned PERCENT→BPS transition the
    ``event_windows`` operator uses (operator-architecture
    consistency)."""
    if basis == "raw_value":
        return unit
    # basis == "level_change"
    if unit == TimeSeriesUnits.PERCENT:
        # F1 RESIDUE (scheduled for extraction — ADR 0016 §Cross-
        # verification close-out): same PERCENT→BPS election as
        # event_windows.  Superseded in principle by Decision 4
        # (``convert_units`` is the only sanctioned conversion site);
        # retained only because ≈27 B-layer consumers depend on the BPS
        # output and must migrate in lock-step.  Do NOT add new callers
        # that rely on it.
        return TimeSeriesUnits.BPS
    # Other unit kinds keep their unit on diff (a BPS series's diff is
    # still BPS; a Z_SCORE series's diff is still Z_SCORE — same
    # operator-architecture rule).
    return unit


def rolling_regression(
    lhs: Series,
    rhs: Series,
    params: Optional[RollingRegressionParams] = None,
    config: Optional[OperatorConfig] = None,
) -> SeriesSet:
    """Run a rolling-OLS regression of ``lhs`` on ``rhs``.

    See module docstring + the bundled config.yaml's methodology
    block for the exact contract.
    """
    if config is None:
        config = load_operator_config(_CONFIG_PATH)

    # Config identity (OPR12) — name AND version.
    _check_config_identity(config, _OPERATOR_NAME, _OPERATOR_VERSION)

    if params is None:
        # rolling_regression has a required per-call field (``window``)
        # with no sensible default — there is nothing to resolve from
        # config, so refuse cleanly (OPR8 / OPR13) rather than fabricate.
        raise RollingRegressionError(
            "rolling_regression requires explicit params (at least "
            "``window``); none were supplied."
        )

    # ------------------------------------------------------------------
    # 0. Structural-metadata compatibility (OPR11).  Default-lenient to
    #    preserve rolling_regression's inner-join + dropna behaviour (a
    #    regression over overlapping dates is well-defined across
    #    cadences); flip require_matching_* to True for the strict
    #    event-study discipline align_series / series_arithmetic use.
    #    Gated on the flags, so the default path is unchanged and the
    #    OperatorStep hash is untouched (these checks add no lineage
    #    params in v1.0.0).
    # ------------------------------------------------------------------
    if params.require_matching_frequency and lhs.frequency != rhs.frequency:
        raise RollingRegressionError(
            f"rolling_regression: incompatible frequencies "
            f"lhs={lhs.frequency!r} vs rhs={rhs.frequency!r}.  Pass "
            "require_matching_frequency=False to opt into mixed-"
            "frequency regression explicitly."
        )
    if params.require_matching_missingness:
        lhs_sig = json.dumps(
            lhs.missingness_policy.model_dump(mode="json"),
            sort_keys=True, separators=(",", ":"),
        )
        rhs_sig = json.dumps(
            rhs.missingness_policy.model_dump(mode="json"),
            sort_keys=True, separators=(",", ":"),
        )
        if lhs_sig != rhs_sig:
            raise RollingRegressionError(
                "rolling_regression: incompatible missingness policies "
                "lhs vs rhs.  Pass require_matching_missingness=False to "
                "opt into mixed policies explicitly."
            )

    # ------------------------------------------------------------------
    # 1. Inner-join lhs and rhs onto a common DatetimeIndex.
    # ------------------------------------------------------------------
    common_index = pd.DatetimeIndex(
        lhs.payload.index.intersection(rhs.payload.index),
    ).sort_values()
    if len(common_index) == 0:
        raise RollingRegressionError(
            f"rolling_regression: lhs.payload.index and "
            f"rhs.payload.index have NO dates in common (lhs has "
            f"{len(lhs.payload.index)} dates, rhs has "
            f"{len(rhs.payload.index)}).  Either align upstream via "
            "align_series or pass overlapping windows."
        )

    lhs_aligned = lhs.payload.reindex(common_index).astype(float)
    rhs_aligned = rhs.payload.reindex(common_index).astype(float)

    # ------------------------------------------------------------------
    # 2. Apply per-side basis (raw_value / level_change).  PERCENT
    #    inputs in level_change mode pick up the methodology-owned
    #    PERCENT→BPS transition (×100), same as event_windows.
    # ------------------------------------------------------------------
    def _prepare(series: pd.Series, unit: TimeSeriesUnits, basis: str) -> pd.Series:
        if basis == "raw_value":
            return series
        # level_change
        diffed = series.diff()
        if unit == TimeSeriesUnits.PERCENT:
            # F1 RESIDUE (×100 value side of the PERCENT→BPS election;
            # ADR 0016 §Cross-verification close-out) — see _effective_unit.
            diffed = diffed * 100.0
        return diffed

    y = _prepare(lhs_aligned, lhs.units, params.lhs_basis)
    x = _prepare(rhs_aligned, rhs.units, params.rhs_basis)

    panel = pd.concat({"_y": y, "_x": x}, axis=1)
    # Drop the row(s) with NaN (the first row when basis=level_change,
    # plus any row where one of the inputs is missing).
    panel = panel.dropna(how="any")
    if len(panel) < params.min_periods:
        raise RollingRegressionError(
            f"rolling_regression: after basis transition + NaN drop, "
            f"panel has {len(panel)} rows < min_periods="
            f"{params.min_periods}.  Either increase the input "
            "lookback, lower min_periods (with caution), or use "
            "basis=raw_value."
        )

    # ------------------------------------------------------------------
    # 3. Run rolling-OLS via the shared compute primitive.
    # ------------------------------------------------------------------
    result = rolling_ols(
        y=panel["_y"],
        X=panel[["_x"]],
        window=params.window,
        min_periods=params.min_periods,
        add_constant=params.add_constant,
    )

    # ------------------------------------------------------------------
    # 4. Build typed Series outputs + the wrapping SeriesSet.
    # ------------------------------------------------------------------
    eff_lhs_unit = _effective_unit(lhs.units, params.lhs_basis)

    beta_payload = result.betas["_x"]
    beta_payload.name = "beta"
    alpha_payload = result.alpha
    alpha_payload.name = "alpha"
    r_squared_payload = result.r_squared
    r_squared_payload.name = "r_squared"

    # Operator step (head of SeriesSet's lineage).
    step_params = {
        "window": params.window,
        "min_periods": params.min_periods,
        "lhs_basis": params.lhs_basis,
        "rhs_basis": params.rhs_basis,
        "add_constant": params.add_constant,
        "lhs_series_key": lhs.series_key,
        "rhs_series_key": rhs.series_key,
        "effective_lhs_unit": str(eff_lhs_unit.value),
        "effective_rhs_unit": str(_effective_unit(
            rhs.units, params.rhs_basis,
        ).value),
        "n_rows_panel": int(len(panel)),
    }
    op_step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params=step_params,
        input_hashes=(lhs.lineage.head_hash, rhs.lineage.head_hash),
        auxiliary_lineages=(rhs.lineage,),
    )
    set_lineage = lhs.lineage.append(op_step)

    series_by_key = {
        "beta": beta_payload,
        "alpha": alpha_payload,
        "r_squared": r_squared_payload,
    }
    units_by_key = {
        # beta = effective_lhs_unit / effective_rhs_unit ⇒ RATIO when
        # bases match (V1 invariant).
        "beta": TimeSeriesUnits.RATIO,
        "alpha": eff_lhs_unit,
        "r_squared": TimeSeriesUnits.RATIO,
    }
    missingness_by_key = {
        "beta": lhs.missingness_policy,
        "alpha": lhs.missingness_policy,
        "r_squared": lhs.missingness_policy,
    }
    upstream_lineage_by_key = {
        "beta": lhs.lineage,
        "alpha": lhs.lineage,
        "r_squared": lhs.lineage,
    }

    # The SeriesSet's frequency is the input frequency (preserved when
    # both inputs agree; None when they disagree or are untagged).
    common_frequency = (
        lhs.frequency if lhs.frequency == rhs.frequency else None
    )

    return SeriesSet(
        series_by_key=series_by_key,
        units_by_key=units_by_key,
        missingness_by_key=missingness_by_key,
        upstream_lineage_by_key=upstream_lineage_by_key,
        common_index=panel.index,
        frequency=common_frequency,
        lineage=set_lineage,
    )


__all__ = [
    "rolling_regression",
    "RollingRegressionError",
]
