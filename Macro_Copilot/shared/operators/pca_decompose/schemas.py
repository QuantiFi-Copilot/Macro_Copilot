"""pca_decompose — parameter schema.

Per OPR8: every consequential method variant is an explicit typed
field.  ``n_components`` (the number of principal components K to
retain) is REQUIRED-NO-DEFAULT (the fit_regime_gmm ``n_states`` /
changepoint ``n_changepoints`` precedent): how many factors to keep is
the central methodology choice and has no honest default — a silent
default (or a default-to-all) would invent a methodology.
``params=None`` refuses naming the field.

Everything else is DESIGN-LOCKED (zero further knobs): correlation PCA
(z-score each column), the SVD estimator, the canonical sign
convention.  Covariance PCA, a variance-threshold K and a rolling
re-fit are declared planned extensions.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PcaDecomposeParams(BaseModel):
    """Parameters for the ``pca_decompose`` operator.

    Fields:
      - ``n_components`` — the number of principal components K to
        retain (REQUIRED, >= 1; the operator additionally refuses
        ``K > n_features``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    n_components: int = Field(ge=1)


__all__ = ["PcaDecomposeParams"]
