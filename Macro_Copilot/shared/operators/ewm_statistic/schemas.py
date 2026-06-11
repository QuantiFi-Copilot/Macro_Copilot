"""ewm_statistic — parameter schema.

Per OPR8: every consequential method variant is an explicit typed
field; each field's schema default mirrors the ``config.yaml`` default.
A test asserts the two never diverge (OPR8).

Single-input operator (one Series): no ``require_matching_*`` flags.

DESIGN LOCKS (OPR7, documented here and in the operator module,
recorded in lineage):

  - DECAY VIA ``span`` ONLY.  halflife/alpha are bijective
    re-parameterisations of the SAME decay — exposing all three would
    let one computation be expressed multiple ways and fracture replay
    identity (genuine aliasing).  Integer spans reach halflife targets
    approximately, the same granularity restriction as the rolling
    family's integer windows.
  - ``adjust=True`` and std ``bias=False``.  These are NOT aliases —
    different settings produce different outputs.  They are locked as
    the single canonical estimator (the textbook finitely-truncated
    weighting and the debiased std — both pandas defaults); the
    biased recursive flavour (the RiskMetrics-style convention) is a
    finance-desk convention that belongs in a primitive's YAML, never
    in a finance-blind operator.  (Contrast: ``rolling_statistic``
    exposes ``ddof`` because both ddof values are unit-consistent
    plain statistics with no convention attached.)
  - ``ignore_na=False`` (pandas' default): with interior NaNs, the
    relative weights are computed on ABSOLUTE positions (a NaN gap
    widens the decay between neighbours).  Output-affecting, so it is
    locked, documented and lineage-stamped rather than silently
    inherited.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# One cohesive exponentially-weighted reduction family (OPR2).
EwmStatistic = Literal["mean", "std"]


class EwmStatisticParams(BaseModel):
    """Parameters for the ``ewm_statistic`` operator.

    Variants:
      - ``statistic``       — mean (the EWMA level) | std (the
                              exponentially-weighted dispersion).
      - ``span``            — the pandas ewm span (decay ≈ 2/(span+1));
                              the single, canonical decay parameter.
      - ``min_periods``     — minimum observations before a value is
                              emitted; when ``None`` defaults to
                              ``span`` (strict — no noisy warmup), the
                              rolling family's null→window convention.
      - ``look_ahead_safe`` — when True, shift the result by one
                              period so the value at t reflects data
                              ≤ t−1 (the rolling family's shared
                              hygiene knob; default False — an EW
                              statistic is a smoothing transform).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    statistic: EwmStatistic = "mean"
    span: int = Field(default=20, ge=2)
    min_periods: Optional[int] = Field(default=None, ge=1)
    look_ahead_safe: bool = False


__all__ = ["EwmStatisticParams", "EwmStatistic"]
