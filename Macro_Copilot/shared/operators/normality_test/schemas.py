"""normality_test — parameter schema.

Per OPR8: every consequential method variant is an explicit typed
field.  ``normality_test`` deliberately has ZERO knobs in v1 (the A5
family ruling): Jarque–Bera is the single canonical moment-based test
(Anderson–Darling / Shapiro–Wilk are declared planned extensions,
never silent alternates).  The empty frozen params model keeps the
OPR8 ``params: Optional[...] = None`` surface uniform (the
streak/transition_events/stationarity_adf precedent).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class NormalityTestParams(BaseModel):
    """Parameters for the ``normality_test`` operator (none in v1 —
    Jarque–Bera is the locked canonical test; alternates are declared
    planned extensions)."""

    model_config = ConfigDict(frozen=True, extra="forbid")


__all__ = ["NormalityTestParams"]
