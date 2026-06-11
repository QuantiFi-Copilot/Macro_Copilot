"""pairwise_spread_matrix — parameter schema.

Per OPR8 the operator still exposes ``params: Optional[...] = None``,
but this operator has NO user-choosable parameters: the pair
enumeration (upper triangle over SORTED keys, i < j), the direction
convention (ki − kj), the column-naming pattern (``<ki>__minus__<kj>``)
and the member ceiling are all DESIGN-LOCKED (OPR7) — they are
structural choices a caller must not vary (varying them would break
replay identity and column-name contracts for no analytical gain).
Each lock is documented in the operator module and recorded in lineage
params.  The YAML's ``defaults:`` block is explicitly empty.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class PairwiseSpreadMatrixParams(BaseModel):
    """Parameters for the ``pairwise_spread_matrix`` operator.

    Deliberately empty: every structural choice is design-locked (see
    the module docstring).  The class exists to satisfy the uniform
    OPR8/OPR16 operator ABI (``params: Optional[...] = None`` + a
    ``*Params`` export).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


__all__ = ["PairwiseSpreadMatrixParams"]
