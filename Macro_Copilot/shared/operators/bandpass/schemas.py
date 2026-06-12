"""bandpass — parameter schema.

Per OPR8: every consequential method variant is an explicit typed
field.  ``low``/``high`` are REQUIRED-NO-DEFAULT (the hp_filter ``lamb``
precedent): the frequency band is frequency-dependent METHODOLOGY (the
6–32 business-cycle band is for QUARTERLY data; the same cycle is a
different ``[low, high]`` at another sampling frequency) and a fixed
default applied at the wrong frequency silently extracts a garbage band
— the caller must choose.  ``params=None`` refuses cleanly naming both
fields.

The cross-field invariant ``high > low`` is MATHEMATICAL (a band must
have positive width) and so lives in code (a ``model_validator``), never
in YAML (OPR7).

The filter is the standard Christiano–Fitzgerald asymmetric full-sample
band-pass (look-ahead — disclosed in the operator module, YAML, card,
registry text, and lineage's ``filter_scope='full_sample_two_sided'``);
``drift=True`` is design-locked (the near-unit-root default).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BandpassParams(BaseModel):
    """Parameters for the ``bandpass`` operator.

    Fields:
      - ``low``  — the SHORTEST period (in observations) admitted into
        the band (REQUIRED, >= 2 — the Nyquist floor).
      - ``high`` — the LONGEST period admitted into the band
        (REQUIRED, > ``low``).

    The band ``[low, high]`` is in OBSERVATION units; choosing it is
    frequency-dependent methodology (no honest default).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    low: int = Field(ge=2)
    high: int = Field(ge=3)

    @model_validator(mode="after")
    def _check_band(self) -> "BandpassParams":
        if self.high <= self.low:
            raise ValueError(
                f"bandpass: high ({self.high}) must be > low "
                f"({self.low}) — the band [low, high] is in period "
                "(observation) units and must have positive width."
            )
        return self


__all__ = ["BandpassParams"]
