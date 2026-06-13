"""fit_regime_gmm — parameter schema.

Per OPR8: every consequential method variant is an explicit typed
field.  ``n_states`` (the number of regimes K) is REQUIRED-NO-DEFAULT
(the changepoint ``n_changepoints`` / hp_filter ``lamb`` precedent): the
number of regimes is the central methodology choice and has no honest
default — a silent default would invent a methodology.  ``params=None``
refuses naming the field.

Everything else is DESIGN-LOCKED in the operator/quant (zero further
knobs): full covariances with a ridge, the EM estimator, the
deterministic k-means++ init, the canonical relabel, and the
max_iter/tol/ridge constants.  Diagonal/spherical covariances and a
state-probabilities Panel output are declared planned extensions.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class FitRegimeGmmParams(BaseModel):
    """Parameters for the ``fit_regime_gmm`` operator.

    Fields:
      - ``n_states`` — the number of regimes K (REQUIRED, >= 2).  The
        Gaussian mixture is fit with this many components; the per-date
        most-likely component is the emitted regime label.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    n_states: int = Field(ge=2)


__all__ = ["FitRegimeGmmParams"]
