"""lead_lag — parameter schema.

Per OPR8: every consequential method variant is an explicit typed
field; each field's schema default mirrors the ``config.yaml`` default
(the YAML is the authoritative source — the operator resolves from it
when ``params is None``).  A test asserts the two never diverge (OPR8).

Multi-input operator (2 Series); CARRIES the OPR11
``require_matching_*`` controls (strict by default).  No units flag:
a correlation is dimensionless, so the operator is unit-INVARIANT
across its inputs (the ``correlation`` doctrine) — both units are
recorded in lineage and every output value is in ``RATIO`` units.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# Same closed set as ``correlation`` / ``rolling_correlation``:
# pearson + spearman implemented, kendall declared-but-unbuilt →
# NotImplementedError (OPR8 honest refusal).  Mirrors the siblings so
# the full-sample, rolling, and cross-lag forms are directly
# substitutable.
LeadLagMethod = Literal["pearson", "spearman", "kendall"]


class LeadLagParams(BaseModel):
    """Parameters for the ``lead_lag`` operator.

    Variants:
      - ``max_lag``     — the CCF is computed at every integer lag
                          k ∈ [−max_lag, +max_lag], in ROWS of the
                          shared input index (the same row-based
                          convention every rolling operator's
                          ``window`` uses).
      - ``method``      — pearson (linear) | spearman (rank) | kendall
                          (planned, refuses cleanly).
      - ``min_periods`` — minimum overlapping non-NaN pairs AT EACH
                          LAG required to emit a non-NaN value for
                          that lag (a correlation is undefined below
                          2); lags below the floor emit NaN.

    Structural-metadata flags (OPR11), strict by default:
      - ``require_matching_frequency``
      - ``require_matching_missingness``
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_lag: int = Field(default=10, ge=1)
    method: LeadLagMethod = "pearson"
    min_periods: int = Field(default=2, ge=2)
    require_matching_frequency: bool = True
    require_matching_missingness: bool = True


__all__ = ["LeadLagParams", "LeadLagMethod"]
