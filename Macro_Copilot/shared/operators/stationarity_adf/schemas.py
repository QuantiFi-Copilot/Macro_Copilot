"""stationarity_adf — parameter schema.

Per OPR8: every consequential method variant is an explicit typed
field.  ``stationarity_adf`` deliberately has ZERO knobs in v1 (the
A5 family ruling): the deterministic-term specification
(``regression='c'`` — intercept only) and the lag selection
(``autolag='AIC'``) are DESIGN-LOCKED as the single canonical ADF
flavour and lineage-stamped; ``'ct'``/``'n'`` regressions are declared
planned extensions, never silent knobs.  The empty frozen params model
keeps the OPR8 ``params: Optional[...] = None`` surface uniform (the
streak/transition_events precedent).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class StationarityAdfParams(BaseModel):
    """Parameters for the ``stationarity_adf`` operator (none in v1 —
    regression='c' and autolag='AIC' are design-locked; alternate
    deterministic terms are declared planned extensions)."""

    model_config = ConfigDict(frozen=True, extra="forbid")


__all__ = ["StationarityAdfParams"]
