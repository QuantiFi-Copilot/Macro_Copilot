"""detrend — parameter schema.

Per OPR8: every consequential method variant is an explicit typed
field; each field's schema default mirrors the ``config.yaml`` default.
A test asserts the two never diverge (OPR8).

Both methods fit over the FULL SAMPLE (the trend at every position
uses data from the whole series — look-ahead, disclosed in the
operator module, the YAML, the card, the registry text, and lineage's
``trend_scope='full_sample'``; the winsorize disclosure pattern).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


DetrendMethod = Literal["demean", "linear"]


class DetrendParams(BaseModel):
    """Parameters for the ``detrend`` operator.

    Variants:
      - ``method`` — demean (subtract the full-sample mean — the
        center-zero transform) | linear (subtract an OLS line fitted
        on the 0..n−1 row positions — removes a straight-line drift).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    method: DetrendMethod = "linear"


__all__ = ["DetrendParams", "DetrendMethod"]
