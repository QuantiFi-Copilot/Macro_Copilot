"""percentile_rank — trailing- or expanding-window percentile rank.

A v2.0 finance-blind operator (ADR 0016) in the
``single_series_transform`` method family.  For each point, computes
the percentile rank of that observation within its own (optionally
trailing) history.

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind structural method; zero finance
    vocabulary or math.  Runs unchanged on any numeric series.
  - OPR2      : ONE output artifact type for every parameter value —
    always a ``Series``, always in ``PCT_RANK`` units (0–100, the
    enum-canonical semantic).  Cross-sectional ranking ships as a
    SEPARATE operator (per planned_extensions).
  - OPR8      : ``params: Optional[PercentileRankParams] = None``;
    every default resolves from ``config.yaml`` when omitted; schema
    defaults mirror the YAML and a parity test pins that.  All four
    tie-handling ``method`` values are implemented (delegated to
    ``scipy.stats.percentileofscore``).
  - OPR9      : one ``Series`` in, one ``Series`` out (closed family).
  - OPR10     : appends exactly one ``OperatorStep`` via ``.build``;
    params pass through ``sanitize_params_for_lineage``.
  - OPR11     : single-input operator — does NOT need
    ``require_matching_*`` flags.
  - OPR13     : every recoverable failure raises
    ``PercentileRankError`` (a ``ValueError`` subclass).
  - OPR12/14  : config name+version identity checked; pure +
    rerun-deterministic (same inputs → same ``head_hash``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
from scipy.stats import percentileofscore

from shared.artifacts.lineage import OperatorStep, sanitize_params_for_lineage
from shared.artifacts.types import Series
from shared.artifacts.units import TimeSeriesUnits
from shared.config.operator_config import (
    OperatorConfig,
    _check_config_identity,
    load_operator_config,
)
from shared.operators.percentile_rank.schemas import (
    PercentileRankMethod,
    PercentileRankParams,
)


_OPERATOR_NAME = "percentile_rank"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# All four scipy.stats.percentileofscore kinds are implemented.  The
# allowlist is defensive — a Literal extension landed without a matching
# scipy support must refuse loudly (OPR8 honest refusal).
_IMPLEMENTED_METHODS: tuple[PercentileRankMethod, ...] = (
    "mean", "weak", "strict", "rank",
)


class PercentileRankError(ValueError):
    """Raised by ``percentile_rank`` on a recoverable user-facing
    failure (a ``ValueError`` subclass, OPR13)."""


def percentile_rank(
    series: Series,
    *,
    params: Optional[PercentileRankParams] = None,
    config: Optional[OperatorConfig] = None,
) -> Series:
    """Compute the trailing- or expanding-window percentile rank of
    ``series``.

    Parameters
    ----------
    series :
        Input ``Series`` artifact.
    params :
        Optional ``PercentileRankParams``.  When ``None`` every field
        is resolved from the bundled ``config.yaml`` (OPR8).
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    Series
        A new ``Series`` aligned to the input's index, payload =
        percentile rank in ``[0, 100]``, units ``PCT_RANK``, frequency
        inherited, missingness policy propagated, lineage extended by
        one ``OperatorStep``.

    Raises
    ------
    PercentileRankError
        Input is not a ``Series``; input is empty; entire output is
        ``NaN``.
    NotImplementedError
        ``method`` is declared in the valid set but not yet supported
        (defensive — not reachable through the schema's Literal).
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
        raw_window = config.default_value("window")
        params = PercentileRankParams(
            window=(int(raw_window) if raw_window is not None else None),
            min_periods=int(config.default_value("min_periods")),
            method=config.default_value("method"),
            look_ahead_safe=bool(config.default_value("look_ahead_safe")),
        )

    # ------------------------------------------------------------------
    # 3. Defensive honest refusal for declared-but-unbuilt method
    #    (Literal-bypassing path; not reachable from *Params).
    # ------------------------------------------------------------------
    if params.method not in _IMPLEMENTED_METHODS:
        raise NotImplementedError(
            f"percentile_rank: method={params.method!r} is declared in "
            "the valid set but not yet implemented.  Implemented "
            f"methods: {list(_IMPLEMENTED_METHODS)}."
        )

    # ------------------------------------------------------------------
    # 4. Structural-input validation (OPR9 typed I/O / OPR13).
    # ------------------------------------------------------------------
    if not isinstance(series, Series):
        raise PercentileRankError(
            "percentile_rank: input must be a Series artifact; got "
            f"{type(series).__name__}."
        )

    payload = series.payload
    if len(payload) == 0:
        raise PercentileRankError(
            "percentile_rank: input series is empty."
        )

    # ------------------------------------------------------------------
    # 5. Compute the rolling percentile rank.  Iterate over rows so
    #    look_ahead_safe semantics are explicit; clear-over-clever
    #    when the look-ahead boundary is the entire load-bearing
    #    distinction.
    # ------------------------------------------------------------------
    n = len(payload)
    values = payload.to_numpy(dtype=float)
    result = np.full(n, np.nan, dtype=float)
    window = params.window  # may be None (expanding)
    look_ahead_safe = bool(params.look_ahead_safe)
    min_periods = int(params.min_periods)
    method = params.method

    for t in range(n):
        current = values[t]
        if not np.isfinite(current):
            # NaN at t → NaN rank (cannot rank a missing observation).
            continue
        if look_ahead_safe:
            end = t  # exclusive of t
            start = 0 if window is None else max(0, t - window)
        else:
            end = t + 1  # inclusive of t
            start = 0 if window is None else max(0, t - window + 1)
        if end <= start:
            continue
        history = values[start:end]
        history = history[np.isfinite(history)]
        if len(history) < min_periods:
            continue
        result[t] = float(percentileofscore(history, current, kind=method))

    out_payload = pd.Series(result, index=payload.index, dtype=float)
    out_payload.name = payload.name

    # ------------------------------------------------------------------
    # 6. Refuse an all-NaN output (typed refusal — OPR13/OPR14).
    # ------------------------------------------------------------------
    n_finite = int(out_payload.notna().sum())
    if n_finite == 0:
        raise PercentileRankError(
            f"percentile_rank: produced an all-NaN output ({n} input "
            f"rows, window={window}, min_periods={min_periods}, "
            f"look_ahead_safe={look_ahead_safe}).  Increase the input "
            "lookback, lower min_periods, or switch to an expanding "
            "window (window=None)."
        )

    # ------------------------------------------------------------------
    # 7. Lineage — one OperatorStep; params sanitised before hashing
    #    (OPR10).
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "window": window,
        "min_periods": min_periods,
        "method": method,
        "look_ahead_safe": look_ahead_safe,
        "input_units": series.units.value,
        "n_input_obs": int(n),
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
        payload=out_payload,
        # PCT_RANK — the enum-canonical 0–100 percentile rank semantic.
        units=TimeSeriesUnits.PCT_RANK,
        frequency=series.frequency,
        missingness_policy=series.missingness_policy,
        lineage=out_lineage,
    )


__all__ = ["percentile_rank", "PercentileRankError"]
