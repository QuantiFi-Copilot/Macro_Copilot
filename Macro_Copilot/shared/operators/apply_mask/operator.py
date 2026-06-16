"""apply_mask — finance-blind subsample of a Series by a boolean EventSet mask.

Implements the "split sample by mask" load-bearing primitive of the
``regime_conditioned_relationship`` archetype:

  classify regimes → split sample by mask → run a per-subsample
  analysis → compare across regimes

The operator does NOT know whether the mask represents sparse
trigger events (event_study archetype's threshold_events output) or
persistent regime states (a regime classifier's output).  Both
shape into the same boolean ``EventSet.mask`` typed artifact;
downstream consumers see only "the input restricted to mask=True
dates."  The semantic distinction lives in lineage (the
``threshold_events`` step's params record the rule that produced
the mask).

A mask that selects ZERO dates returns a typed EMPTY Series (FM-6) —
"no day matched" is a legitimate answer, not an error; downstream
``summarize_series(count)`` turns it into the honest scalar 0.

Lineage contract
----------------
The output ``Series.lineage`` is composed by appending an
``OperatorStep`` to the *input Series's* lineage.  The
``EventSet`` mask is recorded as an ``auxiliary_lineages`` entry
on the operator step so downstream lineage walkers can recover
which mask was applied.  Mirrors the discipline ``series_arithmetic``
established for binary operators (PR #75).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from shared.artifacts.lineage import Lineage, OperatorStep
from shared.artifacts.types import EventSet, Series
from shared.config.operator_config import (
    OperatorConfig,
    OperatorConfigError,
    _check_config_identity,
    load_operator_config,
)
from shared.operators.apply_mask.schemas import ApplyMaskParams


_OPERATOR_NAME = "apply_mask"
# 1.1.0 (OPR14d): the FM-6 zero-match behavioural change — a mask that
# fires on ZERO dates now returns a typed EMPTY Series instead of
# RAISING.  The hash folds version (not code), so the behavioural shift
# carries a version bump even though the changed path never persisted an
# artifact before (it raised).  config.operator.version mirrors this.
_OPERATOR_VERSION = "1.1.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


class ApplyMaskError(ValueError):
    """Raised by ``apply_mask`` on a recoverable user-facing failure."""


def apply_mask(
    series: Series,
    mask: EventSet,
    params: Optional[ApplyMaskParams] = None,
    config: Optional[OperatorConfig] = None,
) -> Series:
    """Subsample ``series`` by ``mask``'s boolean values.

    Parameters
    ----------
    series :
        The input ``Series`` to subsample.
    mask :
        The ``EventSet`` whose ``.mask`` (a per-date bool Series)
        defines which dates to keep.
    params :
        Optional ``ApplyMaskParams``.  Defaults loaded from bundled
        config.yaml when omitted.
    config :
        Optional ``OperatorConfig``; defaults to the bundled
        ``config.yaml`` (process-cached).

    Returns
    -------
    Series
        Output Series with payload subsampled to mask=True dates per
        ``preserve_full_index`` policy.  Lineage extends the input
        series's chain with this operator step; the mask's lineage
        is captured as an ``auxiliary_lineage``.

        Zero-match contract (FM-6): when the mask selects ZERO dates
        (all-False mask under ``preserve_full_index=false``), the
        output is a typed EMPTY Series — same series_key / units /
        frequency / missingness, empty payload, ``n_true=0`` recorded
        in the lineage step.  This keeps "0 matching days"
        expressible: downstream ``summarize_series(count)`` yields 0.
    """
    if config is None:
        config = load_operator_config(_CONFIG_PATH)

    # Config identity (OPR12) — name AND version — BEFORE reading
    # defaults so a wrong config surfaces clearly.
    _check_config_identity(config, _OPERATOR_NAME, _OPERATOR_VERSION)

    # Typed input guards (OPR13 / ERR-8): wrong-typed inputs would
    # otherwise raise a raw AttributeError on ``.frequency`` / ``.mask``.
    if not isinstance(series, Series):
        raise ApplyMaskError(
            f"apply_mask: series must be a Series artifact; got "
            f"{type(series).__name__}."
        )
    if not isinstance(mask, EventSet):
        raise ApplyMaskError(
            f"apply_mask: mask must be an EventSet artifact; got "
            f"{type(mask).__name__}."
        )

    if params is None:
        params = ApplyMaskParams(
            index_policy=config.default_value("index_policy"),
            preserve_full_index=config.default_value("preserve_full_index"),
            require_matching_frequency=config.default_value(
                "require_matching_frequency"
            ),
            require_matching_missingness=config.default_value(
                "require_matching_missingness"
            ),
        )

    # ------------------------------------------------------------------
    # Structural-metadata compatibility (OPR11)
    # ------------------------------------------------------------------
    # Frequency — REAL check.  The EventSet carries a ``frequency`` tag
    # propagated from its source Series by ``threshold_events``, so a
    # mismatch against the target Series is genuinely detectable.  This
    # mirrors the discipline ``event_windows`` enforces against its
    # EventSet + target pair: a mask detected on one cadence applied to
    # a differently-tagged target is the canonical hazard.  Strict mode
    # (default) raises; lenient mode accepts and records the choice in
    # lineage.  Cases: both None → pass; equal → pass; differing
    # values (incl. one None / other concrete partial tagging) → strict
    # raise.
    if params.require_matching_frequency and series.frequency != mask.frequency:
        raise ApplyMaskError(
            f"apply_mask: incompatible frequencies "
            f"series={series.frequency!r} vs mask={mask.frequency!r}.  "
            "Pass require_matching_frequency=False to opt into "
            "mixed-frequency masking explicitly."
        )
    # Missingness — uniformity flag only.  An EventSet is a boolean
    # mask with no ``missingness_policy``, so there is no second regime
    # to compare against; ``require_matching_missingness`` is recorded
    # in lineage (below) but the check is vacuous and the output
    # inherits ``series.missingness_policy`` unchanged.  See the schema
    # docstring for why the missingness axis is not modelled here.

    series_index = series.payload.index
    mask_index = mask.mask.index

    # ------------------------------------------------------------------
    # Index-policy resolution
    # ------------------------------------------------------------------
    if params.index_policy == "strict_match":
        if not series_index.equals(mask_index):
            raise ApplyMaskError(
                f"apply_mask: index_policy='strict_match' requires "
                f"series.payload.index == mask.mask.index, but they "
                f"differ.  Lengths: series={len(series_index)}, "
                f"mask={len(mask_index)}.  Either align both inputs "
                "via align_series upstream or pass "
                "index_policy='intersect'."
            )
        common_index = series_index
    elif params.index_policy == "intersect":
        common_index = pd.DatetimeIndex(
            series_index.intersection(mask_index),
        ).sort_values()
        if len(common_index) == 0:
            raise ApplyMaskError(
                f"apply_mask: series and mask indexes have NO dates "
                f"in common (series has {len(series_index)} dates, "
                f"mask has {len(mask_index)}).  Subsample is empty; "
                "check that both inputs cover overlapping date ranges."
            )
    else:
        # Pydantic Literal already enforces this; defensive guard.
        raise ApplyMaskError(
            f"apply_mask: unsupported index_policy="
            f"{params.index_policy!r}."
        )

    # ------------------------------------------------------------------
    # Subsample
    # ------------------------------------------------------------------
    series_aligned = series.payload.reindex(common_index)
    mask_aligned = mask.mask.reindex(common_index).fillna(False).astype(bool)

    if params.preserve_full_index:
        # Keep the full intersected date axis (the series∩mask
        # intersection under intersect; the shared index under
        # strict_match); mask=False cells become NaN.
        new_payload = series_aligned.where(mask_aligned)
    else:
        # Sparse output: only mask=True dates survive.
        new_payload = series_aligned[mask_aligned]

    # FM-6 count-of-zero: a mask that fired on ZERO dates is a
    # legitimate, informative outcome — "no day matched the
    # condition" — so the operator returns a typed EMPTY Series
    # (same series_key / units / frequency / missingness, empty
    # payload) instead of raising.  Downstream
    # ``summarize_series(statistic='count')`` then yields the honest
    # answer 0; other downstream operators hit their own typed
    # empty-input refusals.  The Series wrapper accepts an empty
    # payload (the DatetimeIndex / numeric-dtype invariants hold
    # trivially); the realized cardinality (n_true=0) is recorded in
    # the lineage step below.  (Pre-FM-6 behavior — raising
    # ApplyMaskError "output payload is empty" — made the true
    # answer "0 matching days" inexpressible; see campaign run b04.)

    # Preserve the input series's series_key (the data identity is
    # unchanged — we just subsampled).
    new_payload = new_payload.copy()
    new_payload.name = series.payload.name

    # ------------------------------------------------------------------
    # Lineage step
    # ------------------------------------------------------------------
    step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params={
            "index_policy": params.index_policy,
            "preserve_full_index": params.preserve_full_index,
            # OPR11 structural-metadata controls, recorded for lineage
            # honesty so a reviewer sees a relaxed flag where it was
            # relaxed.  ``require_matching_missingness`` is a uniformity
            # flag — vacuous for a boolean EventSet — but recorded for
            # symmetry with the frequency control and the sibling
            # multi-artifact operators (series_arithmetic / event_windows).
            "require_matching_frequency": params.require_matching_frequency,
            "require_matching_missingness": params.require_matching_missingness,
            # Pin the realized mask cardinality for diagnostic clarity
            # (a templater reading the lineage can confirm the mask
            # actually fired on a non-empty subset).
            "n_true": int(mask_aligned.sum()),
            "n_total": int(len(mask_aligned)),
        },
        input_hashes=(series.lineage.head_hash, mask.lineage.head_hash),
        auxiliary_lineages=(mask.lineage,),
    )
    new_lineage = series.lineage.append(step)

    return Series(
        series_key=series.series_key,
        payload=new_payload,
        units=series.units,
        frequency=series.frequency,
        missingness_policy=series.missingness_policy,
        lineage=new_lineage,
    )


__all__ = [
    "apply_mask",
    "ApplyMaskError",
]
