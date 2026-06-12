"""fit_ou — parameter schema.

Per OPR8: every consequential method variant is an explicit typed
field.  ``fit_ou`` deliberately has ZERO knobs in v1 (the A4 model-fit
family opener; the stationarity_adf precedent): the estimator — AR(1)
OLS via SVD ``lstsq`` — is DESIGN-LOCKED as the single canonical
discrete-OU fit and lineage-stamped; exact-MLE and Kalman-OU are
declared planned extensions, never silent knobs.  An OU fit has no
honest tuning parameter (φ/α/σ are all ESTIMATED, not chosen).  The
empty frozen params model keeps the OPR8 ``params: Optional[...] =
None`` surface uniform.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class FitOuParams(BaseModel):
    """Parameters for the ``fit_ou`` operator (none in v1 — the AR(1)
    OLS estimator is design-locked; exact-MLE / Kalman-OU are declared
    planned extensions)."""

    model_config = ConfigDict(frozen=True, extra="forbid")


__all__ = ["FitOuParams"]
