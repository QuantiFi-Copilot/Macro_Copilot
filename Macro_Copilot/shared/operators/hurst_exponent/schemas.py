"""hurst_exponent — parameter schema.

Per OPR8: every consequential method variant is an explicit typed
field.  ``hurst_exponent`` deliberately has ZERO knobs in v1 (the A5
family ruling, the stationarity_adf precedent): the estimator
(classical rescaled-range), the small-sample correction (Anis–Lloyd),
and the geometric scale set are DESIGN-LOCKED as the single canonical
Hurst flavour and lineage-stamped; DFA / aggregated-variance /
periodogram estimators and a caller-chosen scale rule are declared
planned extensions, never silent knobs.  The empty frozen params model
keeps the OPR8 ``params: Optional[...] = None`` surface uniform.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class HurstExponentParams(BaseModel):
    """Parameters for the ``hurst_exponent`` operator (none in v1 —
    classical R/S + the Anis–Lloyd correction + the geometric scale set
    are design-locked; alternate estimators are planned extensions)."""

    model_config = ConfigDict(frozen=True, extra="forbid")


__all__ = ["HurstExponentParams"]
