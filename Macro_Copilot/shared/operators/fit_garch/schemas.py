"""fit_garch — parameter schema.

Per OPR8: every consequential method variant is an explicit typed
field.  ``fit_garch`` deliberately has ZERO knobs in v1 (the A4 model-
fit family; the fit_ou precedent): the order (GARCH(1,1)), the
innovation distribution (Gaussian), the mean model (constant μ = the
sample mean, ESTIMATED not chosen) and the estimator (Gaussian MLE via
SLSQP) are all DESIGN-LOCKED and lineage-stamped.  Higher GARCH(p,q)
orders, Student-t / skew-t innovations, zero-mean / AR-mean models and
EGARCH/GJR asymmetry are declared planned extensions, never silent
knobs.  The empty frozen params model keeps the OPR8
``params: Optional[...] = None`` surface uniform.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class FitGarchParams(BaseModel):
    """Parameters for the ``fit_garch`` operator (none in v1 —
    GARCH(1,1)-Gaussian-constant-mean is design-locked; higher orders /
    Student-t / asymmetry are declared planned extensions)."""

    model_config = ConfigDict(frozen=True, extra="forbid")


__all__ = ["FitGarchParams"]
