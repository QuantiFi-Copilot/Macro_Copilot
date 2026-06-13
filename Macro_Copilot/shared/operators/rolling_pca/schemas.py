"""rolling_pca — parameter schema.

Per OPR8: every consequential method variant is an explicit typed
field, and neither has an honest default:

  - ``window`` — the trailing window length W (REQUIRED, >= 2; the
    operator additionally refuses ``W < n_features+1`` so each window's
    PCA is full-rank, and ``W > n_obs``).  A factor-structure window is
    load-bearing methodology with no honest default (unlike a generic
    rolling mean); ``params=None`` refuses.  The window is STRICT
    full-window-only — no partial-window scores (a partial window is
    rank-deficient), so there is no min_periods knob.
  - ``n_components`` — the number of principal components K to retain
    per window (REQUIRED, >= 1; <= n_features).

The PCA estimator (correlation PCA via SVD) and the cross-window sign
alignment are design-locked, reused from ``shared/quant/pca.py``.
Covariance PCA, a Procrustes factor-rotation alignment and a
reconstruct-from-rolling-factors residual are declared planned
extensions.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RollingPcaParams(BaseModel):
    """Parameters for the ``rolling_pca`` operator.

    Fields:
      - ``window`` — the trailing window length (REQUIRED, >= 2; the
        operator refuses W < n_features+1 and W > n_obs).
      - ``n_components`` — the number of principal components per window
        (REQUIRED, >= 1; <= n_features).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    window: int = Field(ge=2)
    n_components: int = Field(ge=1)


__all__ = ["RollingPcaParams"]
