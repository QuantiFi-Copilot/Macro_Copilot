"""ljung_box — parameter schema.

Per OPR8: every consequential method variant is an explicit typed
field; each field's schema default mirrors the ``config.yaml`` default.
A test asserts they never diverge (OPR8).

``lags`` follows the A5 family ruling: ``None`` (the default) resolves
to the classical convention ``min(10, n_obs // 5)`` at runtime — an
explicit, lineage-stamped auto-rule, never a silent omission; an
explicit positive integer pins the horizon.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class LjungBoxParams(BaseModel):
    """Parameters for the ``ljung_box`` operator.

    Fields:
      - ``lags`` — the autocorrelation horizon (the Q statistic jointly
        tests lags 1..lags).  ``None`` resolves to the classical
        ``min(10, n_obs // 5)`` rule at runtime (recorded in lineage);
        an explicit value pins it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    lags: Optional[int] = Field(default=None, ge=1)


__all__ = ["LjungBoxParams"]
