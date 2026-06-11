"""rolling_covariance — parameter schema.

Per OPR8: every consequential method variant is an explicit typed
field; each field's schema default mirrors the ``config.yaml`` default
(the YAML is the authoritative source — the operator resolves from it
when ``params is None``).  A test asserts the two never diverge (OPR8).

Multi-input operator (2 Series); CARRIES the OPR11 ``require_matching_*``
controls — including ``require_matching_units``, because a covariance
is unit-BEARING (semantic unit = product of the inputs' units), the
same family doctrine as the full-sample ``covariance`` sibling.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RollingCovarianceParams(BaseModel):
    """Parameters for the ``rolling_covariance`` operator.

    Variants:
      - ``window``      — trailing-window length, in rows of the
                          (shared) input index.
      - ``min_periods`` — minimum overlapping non-NaN pairs within a
                          window required to emit a non-NaN value;
                          when ``None`` defaults to ``window``.
      - ``ddof``        — denominator degrees-of-freedom delta:
                          1 (sample covariance, Bessel-corrected — the
                          default) or 0 (population covariance).

    Structural-metadata flags (OPR11), strict by default:
      - ``require_matching_units``       — unit-BEARING (see module
        docstring); mixed-unit inputs refused unless explicitly opted
        out (the opt-out is recorded in lineage).
      - ``require_matching_frequency``
      - ``require_matching_missingness``
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    window: int = Field(default=60, ge=2)
    min_periods: Optional[int] = Field(default=None, ge=2)
    ddof: int = Field(default=1, ge=0, le=1)
    require_matching_units: bool = True
    require_matching_frequency: bool = True
    require_matching_missingness: bool = True

    @model_validator(mode="after")
    def _validate(self) -> "RollingCovarianceParams":
        if self.min_periods is not None and self.min_periods > self.window:
            raise ValueError(
                f"min_periods={self.min_periods} cannot exceed window="
                f"{self.window} — otherwise the rolling covariance can "
                "never emit a non-NaN row."
            )
        return self


__all__ = ["RollingCovarianceParams"]
