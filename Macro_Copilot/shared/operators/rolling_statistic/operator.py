"""rolling_statistic — generic windowed reducer over a Series.

A v2.0 finance-blind operator (ADR 0016) in the
``single_series_transform`` method family.  Applies one of
``{mean, std, min, max, sum}`` over a trailing window and emits a
Series of the same length, preserving the input unit.

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind structural method; zero finance
    vocabulary or math.  Output unit PRESERVES the input unit (a
    rolling mean of bps is in bps; a rolling sum of percent is in
    percent) — no coercion.  A future unit transform is owned by the
    single sanctioned ``convert_units`` operator (ADR 0016 Decision 4).
  - OPR2      : ONE output artifact type for every parameter value —
    always a ``Series`` of the same shape as the input.  EWMA and
    rolling-quantile are SEPARATE operators per planned_extensions.
  - OPR8      : ``params: Optional[RollingStatisticParams] = None``;
    every default resolves from ``config.yaml`` when omitted; schema
    defaults mirror the YAML and a parity test pins that.  All five
    statistics in the closed Literal are implemented; the operator
    refuses any unknown value via ``NotImplementedError`` (defensive,
    not reachable from the schema).
  - OPR9      : one ``Series`` in, one ``Series`` out (closed family).
  - OPR10     : appends exactly one ``OperatorStep`` via ``.build``;
    params pass through ``sanitize_params_for_lineage``.
  - OPR11     : single-input operator — does NOT need
    ``require_matching_*`` flags.
  - OPR13     : every recoverable failure raises
    ``RollingStatisticError`` (a ``ValueError`` subclass).
  - OPR12/14  : config name+version identity checked; pure +
    rerun-deterministic (same inputs → same ``head_hash``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from shared.artifacts.lineage import OperatorStep, sanitize_params_for_lineage
from shared.artifacts.types import Series
from shared.config.operator_config import (
    OperatorConfig,
    _check_config_identity,
    load_operator_config,
)
from shared.operators.rolling_statistic.schemas import (
    RollingStatisticName,
    RollingStatisticParams,
)


_OPERATOR_NAME = "rolling_statistic"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# All five statistics in the Literal are implemented in v1.  An unknown
# value cannot reach the operator via the schema (Literal enforces the
# closed set), but the operator's dispatch path still uses an explicit
# allowlist so a future Literal expansion without the matching dispatch
# branch fails honestly via ``NotImplementedError`` (OPR8 / OPR13).
_IMPLEMENTED_STATISTICS: tuple[RollingStatisticName, ...] = (
    "mean", "std", "min", "max", "sum",
)


class RollingStatisticError(ValueError):
    """Raised by ``rolling_statistic`` on a recoverable user-facing
    failure (a ``ValueError`` subclass, OPR13)."""


def rolling_statistic(
    series: Series,
    *,
    params: Optional[RollingStatisticParams] = None,
    config: Optional[OperatorConfig] = None,
) -> Series:
    """Compute a trailing-window reducer over ``series``.

    Parameters
    ----------
    series :
        Input ``Series`` artifact.
    params :
        Optional ``RollingStatisticParams``.  When ``None`` every field
        is resolved from the bundled ``config.yaml`` (OPR8).
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    Series
        A new ``Series`` aligned to the input's index, payload =
        the rolling reducer; units PRESERVE the input unit, frequency
        inherits the input, missingness policy propagated, lineage
        extended by one ``OperatorStep``.

    Raises
    ------
    RollingStatisticError
        Input is not a ``Series``; the input is empty; the entire
        output is ``NaN``.
    NotImplementedError
        ``statistic`` reached the operator from outside the schema's
        closed Literal (defensive — not reachable through ``Params``).
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
        params = RollingStatisticParams(
            statistic=config.default_value("statistic"),
            window=int(config.default_value("window")),
            min_periods=(
                int(raw_min_periods) if raw_min_periods is not None else None
            ),
            ddof=int(config.default_value("ddof")),
            look_ahead_safe=bool(config.default_value("look_ahead_safe")),
        )

    # ------------------------------------------------------------------
    # 3. Honest refusal for a declared-but-unbuilt statistic (defensive;
    #    the schema's Literal already enforces the closed set, but a
    #    Literal expansion landed WITHOUT a matching dispatch branch
    #    must refuse loudly — OPR8 / OPR13).
    # ------------------------------------------------------------------
    if params.statistic not in _IMPLEMENTED_STATISTICS:
        raise NotImplementedError(
            f"rolling_statistic: statistic={params.statistic!r} is "
            "declared in the valid set but not yet implemented.  "
            f"Implemented statistics: {list(_IMPLEMENTED_STATISTICS)}."
        )

    # ------------------------------------------------------------------
    # 4. Structural-input validation (OPR9 typed I/O / OPR13).
    # ------------------------------------------------------------------
    if not isinstance(series, Series):
        raise RollingStatisticError(
            "rolling_statistic: input must be a Series artifact; got "
            f"{type(series).__name__}."
        )

    payload = series.payload
    if len(payload) == 0:
        raise RollingStatisticError(
            "rolling_statistic: input series is empty."
        )

    # ------------------------------------------------------------------
    # 5. Compute the rolling reducer.  min_periods defaults to window
    #    when None (no partial-window stats — the strict choice).
    # ------------------------------------------------------------------
    window = int(params.window)
    min_periods = (
        int(params.min_periods) if params.min_periods is not None else window
    )
    roller = payload.rolling(window=window, min_periods=min_periods)

    if params.statistic == "mean":
        result = roller.mean()
    elif params.statistic == "std":
        result = roller.std(ddof=params.ddof)
    elif params.statistic == "min":
        result = roller.min()
    elif params.statistic == "max":
        result = roller.max()
    elif params.statistic == "sum":
        result = roller.sum()
    else:
        # Unreachable through the schema Literal, but a Literal-only
        # check is not load-bearing in lineage — fall through to the
        # honest refusal at the top of step 3.
        raise NotImplementedError(
            f"rolling_statistic: statistic={params.statistic!r} dispatch "
            "branch missing."
        )

    if params.look_ahead_safe:
        # Shift by one period so the value at t reflects data <= t-1.
        # Same mechanics as rolling_zscore / threshold_events — single
        # source of rolling hygiene across the operator layer.
        result = result.shift(1)

    # Guard against ±Inf landing in the payload (Series rejects ±Inf
    # at construction).  A rolling SUM of a series with very large
    # values can in theory overflow to ±inf in float64; convert to NaN
    # so the artifact validator does not reject the entire output.
    result = result.replace([np.inf, -np.inf], np.nan)
    result = result.astype(float)
    result.name = payload.name

    # ------------------------------------------------------------------
    # 6. Refuse an all-NaN output (typed refusal — OPR13/OPR14).
    # ------------------------------------------------------------------
    n_obs = int(len(payload))
    n_finite = int(result.notna().sum())
    if n_finite == 0:
        raise RollingStatisticError(
            f"rolling_statistic: statistic={params.statistic!r} produced "
            f"an all-NaN output ({n_obs} input rows, window={window}, "
            f"min_periods={min_periods}, look_ahead_safe="
            f"{params.look_ahead_safe}).  Increase the input lookback or "
            "lower window/min_periods."
        )

    # ------------------------------------------------------------------
    # 7. Lineage — one OperatorStep; params sanitised before hashing
    #    (OPR10).
    # ------------------------------------------------------------------
    # OPR14: meaningless params are normalised to None before hashing so
    # identity tracks CONTENT.  ddof is consumed ONLY by statistic='std';
    # for every other statistic two calls that differ only in ddof
    # produce byte-identical payloads, so their lineage hashes must
    # match.  Set the field to None when ignored (sanitize_params_for_
    # lineage preserves None as a JSON-canonical value).
    ddof_for_lineage = int(params.ddof) if params.statistic == "std" else None
    step_params: Dict[str, Any] = {
        "statistic": params.statistic,
        "window": window,
        "min_periods": min_periods,
        "ddof": ddof_for_lineage,
        "look_ahead_safe": bool(params.look_ahead_safe),
        "input_units": series.units.value,
        "n_input_obs": n_obs,
        "n_finite_output": n_finite,
    }
    op_step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params=sanitize_params_for_lineage(step_params),
        input_hashes=(series.lineage.head_hash,),
    )
    out_lineage = series.lineage.append(op_step)

    return Series(
        series_key=series.series_key,
        payload=result,
        # Unit PRESERVATION — a rolling mean of percent is in percent;
        # a rolling sum of bps is in bps.  Finance-blind: no coercion
        # (ADR 0016 Decision 4).
        units=series.units,
        frequency=series.frequency,
        missingness_policy=series.missingness_policy,
        lineage=out_lineage,
    )


__all__ = ["rolling_statistic", "RollingStatisticError"]
