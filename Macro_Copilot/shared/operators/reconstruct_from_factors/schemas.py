"""reconstruct_from_factors — parameter schema.

Per OPR8: every consequential method variant is an explicit typed
field, and neither has an honest default:

  - ``n_components`` — how many principal components define the
    reconstruction "fair value" (REQUIRED, >= 1; the operator also
    refuses ``K > n_features``).  A default-to-all would yield a ~zero
    residual (a useless signal); the changepoint/pca precedent makes it
    required.
  - ``target_column`` — which feature's residual to emit (REQUIRED; the
    operator validates it is one of the Panel's columns).

The PCA estimator (correlation PCA via SVD) is design-locked, reused
from ``shared/quant/pca.py``; the all-columns-residual SeriesSet
variant is a declared planned extension.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ReconstructFromFactorsParams(BaseModel):
    """Parameters for the ``reconstruct_from_factors`` operator.

    Fields:
      - ``n_components`` — the number of principal components defining
        the reconstruction (REQUIRED, >= 1; <= n_features).
      - ``target_column`` — the feature whose PCA residual to emit
        (REQUIRED; must be one of the Panel's columns).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    n_components: int = Field(ge=1)
    target_column: str = Field(min_length=1)


__all__ = ["ReconstructFromFactorsParams"]
