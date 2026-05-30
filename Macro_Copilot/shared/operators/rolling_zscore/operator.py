"""rolling_zscore — trailing-window standardisation of a Series.

A v2.0 finance-blind operator (ADR 0016) in the
``single_series_transform`` method family.  Computes
``(x - rolling_mean) / rolling_std`` over a trailing window and emits a
Series of the same length, always in ``Z_SCORE`` units.

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind structural method; zero finance
    vocabulary or math.  Runs unchanged on rates, FX, equities, or
    temperature data — units propagate as ``Z_SCORE`` regardless of
    the input unit (a z-score is dimensionless).
  - OPR2      : ONE output artifact type for every parameter value —
    always a ``Series`` of the same shape as the input.  An EWMA
    variant ships as a SEPARATE operator, not a basis flag here.
  - OPR8      : ``params: Optional[RollingZscoreParams] = None``;
    every default resolves from ``config.yaml`` when omitted; schema
    defaults mirror the YAML and a parity test pins that.
  - OPR9      : one ``Series`` in, one ``Series`` out (closed family).
  - OPR10     : appends exactly one ``OperatorStep`` via ``.build``;
    params pass through ``sanitize_params_for_lineage``.
  - OPR11     : single-input operator — does NOT need
    ``require_matching_*`` flags.
  - OPR13     : every recoverable failure raises ``RollingZscoreError``
    (a ``ValueError`` subclass); an all-NaN output, an empty input, or
    a non-Series input are typed refusals (never an empty payload or
    a raw ``AttributeError``).
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
from shared.artifacts.units import TimeSeriesUnits
from shared.config.operator_config import (
    OperatorConfig,
    _check_config_identity,
    load_operator_config,
)
from shared.operators.rolling_zscore.schemas import RollingZscoreParams


_OPERATOR_NAME = "rolling_zscore"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


class RollingZscoreError(ValueError):
    """Raised by ``rolling_zscore`` on a recoverable user-facing failure.

    Subclass of ``ValueError`` (OPR13) so the rest of the substrate's
    ``except ValueError`` handling continues to work; the transport
    boundary converts it to the user-facing error envelope.
    """


def rolling_zscore(
    series: Series,
    *,
    params: Optional[RollingZscoreParams] = None,
    config: Optional[OperatorConfig] = None,
) -> Series:
    """Compute the trailing-window z-score of ``series``.

    Parameters
    ----------
    series :
        Input ``Series`` artifact.
    params :
        Optional ``RollingZscoreParams``.  When ``None`` every field is
        resolved from the bundled ``config.yaml`` (OPR8).
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    Series
        A new ``Series`` aligned to the input's index, payload =
        ``(x - rolling_mean) / rolling_std``, units ``Z_SCORE``,
        frequency inherited from the input, missingness policy
        propagated, lineage extended by one ``OperatorStep``.

    Raises
    ------
    RollingZscoreError
        Input is not a ``Series``; the input is empty; or the entire
        output is ``NaN`` (e.g. ``window`` longer than the series).
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
        params = RollingZscoreParams(
            window=int(config.default_value("window")),
            min_periods=(
                int(raw_min_periods) if raw_min_periods is not None else None
            ),
            ddof=int(config.default_value("ddof")),
            look_ahead_safe=bool(config.default_value("look_ahead_safe")),
        )

    # ------------------------------------------------------------------
    # 3. Structural-input validation (OPR9 typed I/O / OPR13).
    # ------------------------------------------------------------------
    if not isinstance(series, Series):
        raise RollingZscoreError(
            "rolling_zscore: input must be a Series artifact; got "
            f"{type(series).__name__}."
        )

    payload = series.payload
    if len(payload) == 0:
        raise RollingZscoreError(
            "rolling_zscore: input series is empty."
        )

    # ------------------------------------------------------------------
    # 4. Compute the rolling z-score.  Min-periods defaults to window
    #    when omitted (no partial-window stats — the strict choice).
    # ------------------------------------------------------------------
    window = int(params.window)
    min_periods = (
        int(params.min_periods) if params.min_periods is not None else window
    )

    # Rolling mean / std.  ``std(ddof=...)`` returns NaN for any window
    # with fewer than min_periods observations.
    rolling_mean = payload.rolling(
        window=window, min_periods=min_periods,
    ).mean()
    rolling_std = payload.rolling(
        window=window, min_periods=min_periods,
    ).std(ddof=params.ddof)

    if params.look_ahead_safe:
        # Lookahead-safe: stats at time t use only data <= t-1.
        # Implemented as a one-period shift of the rolling stats so
        # the value at index t reflects observations [t-window, t-1]
        # rather than [t-window+1, t].  Same mechanics as
        # threshold_events._build_threshold_series — single source of
        # rolling hygiene across the operator layer.
        rolling_mean = rolling_mean.shift(1)
        rolling_std = rolling_std.shift(1)

    # Standardise.  Where rolling_std == 0 (a degenerate window of
    # constants), division yields ±inf — convert to NaN so the output
    # payload satisfies the artifact-layer ban on ±Inf (NaN is the
    # legitimate missingness sentinel).
    zscore = (payload - rolling_mean) / rolling_std
    zscore = zscore.replace([np.inf, -np.inf], np.nan)
    zscore = zscore.astype(float)
    zscore.name = payload.name

    # ------------------------------------------------------------------
    # 5. Refuse an all-NaN output (typed refusal, never an empty
    #    payload — OPR13/OPR14).  An all-NaN output most commonly
    #    means window > len(series) or every window had zero variance.
    # ------------------------------------------------------------------
    n_obs = int(len(payload))
    n_finite = int(zscore.notna().sum())
    if n_finite == 0:
        raise RollingZscoreError(
            f"rolling_zscore: produced an all-NaN output ({n_obs} input "
            f"rows, window={window}, min_periods={min_periods}, "
            f"look_ahead_safe={params.look_ahead_safe}).  Increase the "
            "input lookback, lower the window/min_periods, or check for "
            "a constant input."
        )

    # ------------------------------------------------------------------
    # 6. Lineage — one OperatorStep; params sanitised before hashing
    #    (OPR10).
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "window": window,
        "min_periods": min_periods,
        "ddof": int(params.ddof),
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
        payload=zscore,
        units=TimeSeriesUnits.Z_SCORE,
        frequency=series.frequency,
        missingness_policy=series.missingness_policy,
        lineage=out_lineage,
    )


__all__ = ["rolling_zscore", "RollingZscoreError"]
