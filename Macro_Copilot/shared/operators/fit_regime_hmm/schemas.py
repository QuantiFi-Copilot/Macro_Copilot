"""fit_regime_hmm — parameter schema.

Per OPR8: every consequential method variant is an explicit typed
field.  ``n_states`` (the number of regimes K) is REQUIRED-NO-DEFAULT
(the fit_regime_gmm precedent; ``params=None`` refuses).

Everything else is DESIGN-LOCKED (zero further knobs, mirroring
``FitRegimeGmmParams``): full covariances with a ridge, the Baum-Welch
estimator, the fixed-seed k-means++ init, the sticky-diagonal
transition init, the canonical relabel, and the Viterbi decode.  The
state-probabilities Panel, a posterior (gamma-argmax) decode and
diagonal covariances are declared planned extensions, never silent
knobs.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class FitRegimeHmmParams(BaseModel):
    """Parameters for the ``fit_regime_hmm`` operator.

    Fields:
      - ``n_states`` — the number of regimes K (REQUIRED, >= 2).  The
        Gaussian HMM is fit with this many states; the Viterbi-decoded
        per-date state is the emitted regime label.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    n_states: int = Field(ge=2)


__all__ = ["FitRegimeHmmParams"]
