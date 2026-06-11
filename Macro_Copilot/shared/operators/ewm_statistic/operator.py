"""ewm_statistic — exponentially-weighted mean/std of a Series.

Finance-blind ``single_series_transform`` operator.  Computes the
exponentially-weighted moving average (statistic='mean' — the EWMA
level) or the exponentially-weighted standard deviation
(statistic='std' — the EW dispersion) of one Series, emitting a Series
in the input's units.  Consolidates the plan's "ewma, ewm_volatility"
row as ONE operator with a ``statistic`` variant (the
``rolling_statistic`` OPR2 precedent).

DESIGN LOCKS (OPR7, recorded in lineage): decay is parameterised by
``span`` ONLY (halflife/alpha are bijective re-parameterisations of
the SAME decay — genuine aliasing that would fracture replay
identity); ``adjust=True`` + std ``bias=False`` are locked as the
single canonical (textbook, debiased — pandas-default) estimator, the
biased recursive flavour being a finance-desk convention that belongs
in a primitive's YAML; ``ignore_na=False`` (pandas' default —
absolute-position weights across interior NaNs) is output-affecting
and therefore locked, documented and lineage-stamped.

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind reduction family; zero finance
    vocabulary (an EW std of input values — annualization or any
    calendar scaling is finance math and lives in primitives).
  - OPR2      : ONE output artifact type — always a ``Series``; units
    passthrough for both statistics.
  - OPR8      : ``params: Optional[...] = None``; defaults resolve
    from ``config.yaml``; min_periods None → span (the rolling
    family's strict null→window convention).
  - OPR9      : one ``Series`` in, one ``Series`` out.
  - OPR10     : one ``OperatorStep`` appended to the input's chain.
  - OPR13     : typed ``EwmStatisticError`` refusals: non-Series
    input, or an all-NaN output.  Warmup positions (< min_periods
    observations) are NaN — legitimate missingness.
  - OPR14     : pure + rerun-deterministic; the locked adjust/bias
    choices ride in lineage params.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

from shared.artifacts.lineage import OperatorStep, sanitize_params_for_lineage
from shared.artifacts.types import Series
from shared.config.operator_config import (
    OperatorConfig,
    _check_config_identity,
    load_operator_config,
)
from shared.operators.ewm_statistic.schemas import EwmStatisticParams


_OPERATOR_NAME = "ewm_statistic"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

# DESIGN-LOCKED weighting choices (OPR7), all recorded in lineage:
# adjust=True (textbook finitely-truncated weighting) + bias=False
# (debiased std) = the single canonical estimator; ignore_na=False
# (absolute-position weights across interior NaNs) is output-affecting
# and locked explicitly rather than silently inherited from pandas.
_ADJUST = True
_STD_BIAS = False
_IGNORE_NA = False


class EwmStatisticError(ValueError):
    """Raised by ``ewm_statistic`` on a recoverable user-facing failure
    (a ``ValueError`` subclass, OPR13)."""


def ewm_statistic(
    series: Series,
    *,
    params: Optional[EwmStatisticParams] = None,
    config: Optional[OperatorConfig] = None,
) -> Series:
    """Exponentially-weighted mean or std of ``series``.

    Parameters
    ----------
    series :
        The input ``Series``.
    params :
        Optional ``EwmStatisticParams``.  When ``None`` every field is
        resolved from the bundled ``config.yaml`` (OPR8).
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    Series
        The EW statistic on the input index (NaN during warmup), in
        the input's units; frequency and series-key context preserved;
        missingness passthrough (the EW transform emits a value
        wherever enough observations exist — the input's cleaning
        policy still describes the underlying data); lineage extended
        by one ``OperatorStep``.

    Raises
    ------
    EwmStatisticError
        The input is not a ``Series``, min_periods exceeds the input
        length so the output is all-NaN, or the output is otherwise
        all-NaN.
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
        raw_min_periods = config.default_value("min_periods")
        params = EwmStatisticParams(
            statistic=config.default_value("statistic"),
            span=int(config.default_value("span")),
            min_periods=(
                int(raw_min_periods) if raw_min_periods is not None else None
            ),
            look_ahead_safe=bool(config.default_value("look_ahead_safe")),
        )

    # ------------------------------------------------------------------
    # 3. Structural-input validation (OPR9 typed I/O).
    # ------------------------------------------------------------------
    if not isinstance(series, Series):
        raise EwmStatisticError(
            "ewm_statistic: input must be a Series artifact; got "
            f"{type(series).__name__}."
        )

    span = int(params.span)
    min_periods = (
        int(params.min_periods) if params.min_periods is not None else span
    )

    # ------------------------------------------------------------------
    # 4. The EW reduction (pandas ewm; design-locked adjust/bias).
    # ------------------------------------------------------------------
    roller = series.payload.ewm(
        span=span, min_periods=min_periods, adjust=_ADJUST,
        ignore_na=_IGNORE_NA,
    )
    if params.statistic == "mean":
        result = roller.mean()
    elif params.statistic == "std":
        result = roller.std(bias=_STD_BIAS)
    else:
        # Defensive (the schema's Literal closes the set): a future
        # expansion landing without a branch must refuse (OPR8).
        raise NotImplementedError(
            f"ewm_statistic: statistic={params.statistic!r} is declared "
            "but not implemented; see config.yaml "
            "methodology.planned_extensions."
        )

    if params.look_ahead_safe:
        # Shift by one period so the value at t reflects data <= t-1.
        # Same mechanics as rolling_statistic / rolling_zscore — single
        # source of rolling hygiene across the operator layer.
        result = result.shift(1)

    # Guard against ±Inf landing in the payload (Series rejects ±Inf at
    # construction): the EW std squares deviations inside pandas'
    # accumulator, so extreme-but-finite inputs can overflow.  Convert
    # to NaN-missingness, the rolling_statistic precedent for windowed
    # single-series reductions.
    result = result.replace([np.inf, -np.inf], np.nan)
    result = result.astype(float)
    n_finite = int(np.isfinite(result.to_numpy(dtype=float)).sum())
    if n_finite == 0:
        raise EwmStatisticError(
            f"ewm_statistic: produced an all-NaN output "
            f"({len(series.payload)} input rows, span={span}, "
            f"min_periods={min_periods}, "
            f"statistic={params.statistic!r}).  Increase the input "
            "lookback or lower span/min_periods."
        )
    result.name = f"ewm_{params.statistic}__{series.series_key}"

    # ------------------------------------------------------------------
    # 5. Lineage — one OperatorStep (OPR10); the design locks ride in
    #    params so the weighting is auditable.
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "statistic": params.statistic,
        "span": span,
        "min_periods": min_periods,
        "look_ahead_safe": params.look_ahead_safe,
        "adjust": _ADJUST,        # design-locked (OPR7)
        "std_bias": _STD_BIAS,    # design-locked (OPR7); std only
        "ignore_na": _IGNORE_NA,  # design-locked (OPR7)
        "n_obs": int(len(series.payload)),
        "n_finite_output": n_finite,
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
        series_key=f"ewm_{params.statistic}__{series.series_key}",
        payload=result,
        units=series.units,
        frequency=series.frequency,
        missingness_policy=series.missingness_policy,
        lineage=out_lineage,
    )


__all__ = ["ewm_statistic", "EwmStatisticError"]
