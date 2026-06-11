"""lag — parameter schema.

Per OPR8: every consequential method variant is an explicit typed
field; each field's schema default mirrors the ``config.yaml`` default.
A test asserts the two never diverge (OPR8).

``periods`` is restricted to >= 1 (pure LAG): a negative value would
be a LEAD — it pulls FUTURE values back to t, silently smuggling
look-ahead into any composed signal and undermining the rolling
family's ``look_ahead_safe`` hygiene.  Lead is declared in
``methodology.planned_extensions`` with the hazard documented; it is
not a knob here.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class LagParams(BaseModel):
    """Parameters for the ``lag`` operator.

    Fields:
      - ``periods`` — how many rows back: the value from ``periods``
        rows EARLIER appears at each position (pandas ``shift(k)``
        semantics); the first ``periods`` rows are NaN.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    periods: int = Field(default=1, ge=1)


__all__ = ["LagParams"]
