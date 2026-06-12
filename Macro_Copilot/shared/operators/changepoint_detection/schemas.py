"""changepoint_detection — parameter schema.

Per OPR8: every consequential method variant is an explicit typed
field.

  - ``n_changepoints`` is REQUIRED-NO-DEFAULT (the hp_filter ``lamb`` /
    bandpass band precedent): the NUMBER of breaks to find is the
    central methodology choice and has no honest default — a silent
    default would invent a methodology.  ``params=None`` refuses
    naming it.
  - ``min_size`` (the minimum segment length) has an honest default
    (the L2-SSE floor of 2) resolved from ``config.yaml``; ``None``
    resolves at runtime (the ljung_box None→config precedent).

The cost model (L2 mean-shift) and the algorithm (greedy binary
segmentation) are DESIGN-LOCKED in the operator — a penalty/BIC count
and PELT/dynamic-programming are declared planned extensions, never
silent knobs.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ChangepointDetectionParams(BaseModel):
    """Parameters for the ``changepoint_detection`` operator.

    Fields:
      - ``n_changepoints`` — the number of mean-shift breaks to find
        (REQUIRED, >= 1).  Binary segmentation returns the top-gain
        splits (fewer if positive-gain splits are exhausted).
      - ``min_size`` — the minimum segment length (>= 2).  ``None``
        resolves to the config default (2) at runtime.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    n_changepoints: int = Field(ge=1)
    min_size: Optional[int] = Field(default=None, ge=2)


__all__ = ["ChangepointDetectionParams"]
