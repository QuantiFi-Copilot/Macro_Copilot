"""lead_lag — the cross-correlation function between two Series.

Finance-blind ``statistical_relationship`` operator.  Computes
``corr(left_t, right_{t+k})`` at every integer lag
k ∈ [−max_lag, +max_lag] (lags in ROWS of the shared input index, the
same row-based convention every rolling operator's ``window`` uses)
and emits ONE ``Series`` whose position k carries the correlation at
lag k — the "who moves first" profile between two quantities.

SIGN CONVENTION (pinned, recorded in lineage): positive k means LEFT
LEADS RIGHT by k rows — the value at lag +k is the correlation of
today's left with right k rows LATER (``corr(left_t, right_{t+k})``).
Negative k therefore means right leads left.

OUTPUT ENCODING (the live substrate precedent — mirrors
``conditional_aggregate``): the closed artifact family has no
lag-indexed shape, and a ``Panel`` requires true calendar rows, so the
lag axis is encoded on a synthetic ``DatetimeIndex`` anchored at
``_OFFSET_ANCHOR`` (lag k → anchor + k days).  The integer lags are
ALSO recorded in lineage params so consumers never reverse-engineer
the encoding; ``frequency=None`` because the synthetic index has no
business-day meaning.  (The plan's §7 sketch said ``Panel``; the
honest closed-family shape per the live code is this offset-anchor
``Series`` — a true lag-indexed 2-D artifact would be an ART4/ADR
question.)

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind statistical method; zero finance
    vocabulary or math; lags are rows, never calendar/day-count.
  - OPR2      : ONE output artifact type — always a ``Series`` (the
    CCF profile).  A single-lag correlation number is the separate
    ``correlation`` operator.
  - OPR8      : ``params: Optional[LeadLagParams] = None``; defaults
    resolve from ``config.yaml``; ``kendall`` declared-but-unbuilt →
    clean ``NotImplementedError``.
  - OPR9      : two ``Series`` in, one ``Series`` out (closed family).
  - OPR10     : one ``OperatorStep`` via ``.build``; right's chain in
    ``auxiliary_lineages``; the lag vector, sign convention, per-lag
    overlap counts and both inputs' units recorded in params.
  - OPR11     : strict-by-default on frequency + missingness;
    unit-INVARIANT (a correlation is dimensionless) — output RATIO.
  - OPR13     : every recoverable failure raises ``LeadLagError`` (a
    ``ValueError`` subclass).  A lag with insufficient overlap or a
    zero-variance arm emits NaN AT THAT LAG (legitimate per-lag
    missingness, the rolling_correlation precedent); an ALL-NaN
    profile is a typed refusal.
  - OPR12/14  : config name+version identity checked; pure + rerun-
    deterministic.

REVERSAL PROPERTY (documented, tested, never a refusal):
``lead_lag(left, right)`` at lag k equals ``lead_lag(right, left)``
at lag −k.

Composition contract: ``lead_lag`` does NOT align.  The two Series
must already share an identical ``DatetimeIndex`` — align upstream
with ``align_series``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd

from shared.artifacts.lineage import OperatorStep, sanitize_params_for_lineage
from shared.artifacts.missingness import RawNoCleaning
from shared.artifacts.types import Series
from shared.artifacts.units import TimeSeriesUnits
from shared.config.operator_config import (
    OperatorConfig,
    _check_config_identity,
    load_operator_config,
)
from shared.operators.lead_lag.schemas import LeadLagMethod, LeadLagParams


_OPERATOR_NAME = "lead_lag"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

# Synthetic anchor for the output Series's DatetimeIndex.  Lags are
# encoded as ``_OFFSET_ANCHOR + Timedelta(days=lag)``.  DESIGN-LOCKED
# (OPR7): must equal conditional_aggregate's anchor so the substrate
# has ONE offset-encoding convention; a consistency test asserts the
# two constants are identical.  The integer lags are ALSO recorded in
# lineage params so consumers never reverse-engineer the encoding.
_OFFSET_ANCHOR: pd.Timestamp = pd.Timestamp("1970-01-01")

_IMPLEMENTED_METHODS: Tuple[LeadLagMethod, ...] = ("pearson", "spearman")


class LeadLagError(ValueError):
    """Raised by ``lead_lag`` on a recoverable user-facing failure (a
    ``ValueError`` subclass, OPR13)."""


def lead_lag(
    left: Series,
    right: Series,
    *,
    params: Optional[LeadLagParams] = None,
    config: Optional[OperatorConfig] = None,
) -> Series:
    """Compute the CCF ``corr(left_t, right_{t+k})`` for every lag k.

    Parameters
    ----------
    left, right :
        The two ``Series`` artifacts.  They MUST share an identical
        ``DatetimeIndex`` (align upstream with ``align_series``).
        Positive output lags mean ``left`` LEADS ``right``.
    params :
        Optional ``LeadLagParams``.  When ``None`` every field is
        resolved from the bundled ``config.yaml`` (OPR8).
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    Series
        The CCF profile on the synthetic offset-anchor index (lag k →
        anchor + k days; the integer lags ride in lineage params),
        units ``RATIO``, ``frequency=None``, fresh ``RawNoCleaning``
        missingness, lineage extended by one ``OperatorStep``.

    Raises
    ------
    LeadLagError
        Inputs are not ``Series``; indexes differ; frequency or
        missingness mismatch under strict mode; or the entire CCF
        profile is NaN (no lag had ``min_periods`` overlapping pairs
        with non-degenerate variance).
    NotImplementedError
        ``method`` is declared in the valid set but not yet built
        (``kendall``).
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
        params = LeadLagParams(
            max_lag=int(config.default_value("max_lag")),
            method=config.default_value("method"),
            min_periods=int(config.default_value("min_periods")),
        )

    # ------------------------------------------------------------------
    # 3. Honest refusal for a declared-but-unbuilt method — OPR8.
    # ------------------------------------------------------------------
    if params.method not in _IMPLEMENTED_METHODS:
        raise NotImplementedError(
            f"lead_lag: method={params.method!r} is declared in the "
            "valid set but not yet implemented; see config.yaml "
            "methodology.planned_extensions.  Implemented methods: "
            f"{list(_IMPLEMENTED_METHODS)}."
        )

    # ------------------------------------------------------------------
    # 4. Structural-input validation (OPR9 typed I/O; OPR11 metadata).
    # ------------------------------------------------------------------
    if not isinstance(left, Series) or not isinstance(right, Series):
        raise LeadLagError(
            "lead_lag: both inputs must be Series artifacts; got "
            f"left={type(left).__name__}, right={type(right).__name__}."
        )

    # lead_lag does NOT align — that is align_series's job.
    if not left.payload.index.equals(right.payload.index):
        raise LeadLagError(
            "lead_lag: left and right Series must share an identical "
            "DatetimeIndex.  Align upstream with align_series first "
            f"(left.len={len(left.payload)}, right.len={len(right.payload)})."
        )

    # Frequency — strict by default (OPR11).
    if params.require_matching_frequency and left.frequency != right.frequency:
        raise LeadLagError(
            f"lead_lag: incompatible frequencies left={left.frequency!r} "
            f"vs right={right.frequency!r}.  Pass "
            "require_matching_frequency=False to opt into mixed-"
            "frequency lags explicitly."
        )

    # Missingness — strict by default (OPR11).
    if params.require_matching_missingness:
        left_sig = json.dumps(
            left.missingness_policy.model_dump(mode="json"),
            sort_keys=True, separators=(",", ":"),
        )
        right_sig = json.dumps(
            right.missingness_policy.model_dump(mode="json"),
            sort_keys=True, separators=(",", ":"),
        )
        if left_sig != right_sig:
            raise LeadLagError(
                "lead_lag: incompatible missingness policies left vs "
                "right.  Pass require_matching_missingness=False to opt "
                "into mixed policies explicitly."
            )

    # NOTE (OPR11): unit-INVARIANT — a correlation is dimensionless;
    # both units are recorded in lineage; every output value is RATIO.

    # ------------------------------------------------------------------
    # 5. Compute the CCF, one lag at a time.
    #    Lag k pairs left_t with right_{t+k}: shifting right by −k rows
    #    places right_{t+k} at position t.  Insufficient overlap or a
    #    zero-variance arm at a lag → NaN at THAT lag (per-lag
    #    missingness, the rolling_correlation precedent).
    # ------------------------------------------------------------------
    lags = list(range(-params.max_lag, params.max_lag + 1))
    left_p = left.payload
    right_p = right.payload
    values: list = []
    overlap_counts: list = []
    for k in lags:
        shifted = right_p.shift(-k)
        pair = pd.concat([left_p, shifted], axis=1, keys=["l", "r"]).dropna()
        n_pairs = int(len(pair))
        overlap_counts.append(n_pairs)
        if n_pairs < params.min_periods:
            values.append(np.nan)
            continue
        if pair["l"].nunique() <= 1 or pair["r"].nunique() <= 1:
            # zero variance ⇒ undefined coefficient ⇒ NaN at this lag.
            values.append(np.nan)
            continue
        coeff = float(pair["l"].corr(pair["r"], method=params.method))
        # Defensive: a correlation is bounded; scrub any non-finite
        # artefact of degenerate numerics into per-lag missingness.
        values.append(coeff if np.isfinite(coeff) else np.nan)

    n_finite = int(np.isfinite(np.asarray(values, dtype=float)).sum())
    if n_finite == 0:
        raise LeadLagError(
            f"lead_lag: produced an all-NaN CCF profile ({len(left_p)} "
            f"input rows, max_lag={params.max_lag}, "
            f"min_periods={params.min_periods}).  Increase the input "
            "lookback, lower max_lag/min_periods, or check for a "
            "constant series."
        )

    index = pd.DatetimeIndex(
        [_OFFSET_ANCHOR + pd.Timedelta(days=int(k)) for k in lags]
    )
    result = pd.Series(values, index=index, dtype=float)
    result.name = f"lead_lag__{left.series_key}__{right.series_key}"

    # ------------------------------------------------------------------
    # 6. Lineage — one OperatorStep; right's chain in auxiliary_lineages;
    #    the lag semantics recorded so the encoding is auditable (OPR10).
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "max_lag": params.max_lag,
        "method": params.method,
        "min_periods": params.min_periods,
        "lag_values": lags,
        "sign_convention": (
            "positive k means left LEADS right by k rows "
            "(corr(left_t, right_{t+k}))"
        ),
        "offset_anchor": str(_OFFSET_ANCHOR.date()),
        "n_obs": int(len(left_p)),
        "n_finite_lags": n_finite,
        "n_overlapping_pairs_per_lag": overlap_counts,
        "left_units": left.units.value,
        "right_units": right.units.value,
        "left_series_key": left.series_key,
        "right_series_key": right.series_key,
        "require_matching_frequency": params.require_matching_frequency,
        "require_matching_missingness": params.require_matching_missingness,
    }
    op_step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params=sanitize_params_for_lineage(step_params),
        input_hashes=(left.lineage.head_hash, right.lineage.head_hash),
        auxiliary_lineages=(right.lineage,),
    )
    out_lineage = left.lineage.append(op_step)

    return Series(
        series_key=f"lead_lag__{left.series_key}__{right.series_key}",
        payload=result,
        units=TimeSeriesUnits.RATIO,
        # The synthetic-anchor index has no business-day meaning; the
        # integer-lag semantics live in lineage params (the
        # conditional_aggregate precedent).
        frequency=None,
        # Values are computed fresh from the inputs; no cleaning applied.
        missingness_policy=RawNoCleaning(),
        lineage=out_lineage,
    )


__all__ = ["lead_lag", "LeadLagError"]
