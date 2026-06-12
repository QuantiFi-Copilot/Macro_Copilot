"""hp_filter — parameter schema.

Per OPR8: every consequential method variant is an explicit typed
field.  ``component`` mirrors the ``config.yaml`` default; ``lamb`` is
REQUIRED-NO-DEFAULT (the weighted_combination weights precedent): the
HP smoothing strength is frequency-dependent METHODOLOGY (quarterly
convention 1600; monthly ≈ 14400; daily ≈ 1e6–1e7) and a fixed default
applied to the wrong frequency silently produces a garbage trend — the
caller must choose.

The filter is the standard TWO-SIDED Hodrick–Prescott decomposition:
the trend at every position uses the WHOLE sample (look-ahead,
disclosed in the operator module, YAML, card, registry text, and
lineage's ``filter_scope='full_sample_two_sided'``).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


HpComponent = Literal["trend", "cycle"]


class HpFilterParams(BaseModel):
    """Parameters for the ``hp_filter`` operator.

    Fields:
      - ``component`` — which side of the decomposition to emit:
        trend (the smooth long-run path — the default) | cycle (the
        residual, x − trend).  The two sum exactly to the input.
      - ``lamb``      — the HP smoothing strength (REQUIRED, > 0).
        Frequency-dependent: ~1600 for quarterly data, ~14400 for
        monthly, ~1e6–1e7 for daily.  No default — choosing it is
        methodology.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    component: HpComponent = "trend"
    lamb: float = Field(gt=0.0)


__all__ = ["HpFilterParams", "HpComponent"]
