"""shared.operators.apply_mask — finance-blind subsample of a Series by a mask.

Phase 2A operator (workflow-templates milestone) that implements the
"split sample by mask" load-bearing primitive of the
``regime_conditioned_relationship`` archetype:

  classify regimes → split sample by mask → run a per-subsample
  analysis → compare across regimes

``apply_mask`` provides the "split sample by mask" surface — given
a Series and an EventSet (used here as a generic boolean mask, not
necessarily as a sparse trigger set), returns a new Series whose
values are the input restricted to the dates where the mask is True.

Public surface:

  - ``apply_mask``           the operator itself
  - ``ApplyMaskParams``      typed parameter object
  - ``CONFIG_PATH``          path to bundled config.yaml
"""

from pathlib import Path

from shared.operators.apply_mask.operator import (
    apply_mask,
    ApplyMaskError,
)
from shared.operators.apply_mask.schemas import ApplyMaskParams


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "apply_mask",
    "ApplyMaskError",
    "ApplyMaskParams",
    "CONFIG_PATH",
]
