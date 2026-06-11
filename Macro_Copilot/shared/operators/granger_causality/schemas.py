"""granger_causality — parameter schema.

Per OPR8: every consequential method variant is an explicit typed
field; each field's schema default mirrors the ``config.yaml`` default
(the YAML is the authoritative source — the operator resolves from it
when ``params is None``).  A test asserts the two never diverge (OPR8).

Multi-input operator (2 Series); CARRIES the OPR11
``require_matching_*`` controls (strict by default).  No units flag:
the F statistic is dimensionless, so the operator is unit-INVARIANT
across its inputs — both units are recorded in lineage and the output
is a ``RATIO``-tagged ScalarMetric.

There is deliberately NO ``min_periods`` field: the observation floor
for a nested-OLS F-test is a mathematical invariant of the lag order
(df2 = n − 2·n_lags − 1 must be ≥ 1), enforced in code (PR7-style:
invariants live in code, not YAML).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class GrangerCausalityParams(BaseModel):
    """Parameters for the ``granger_causality`` operator.

    Variants:
      - ``n_lags`` — the lag order p of BOTH nested models (the
        restricted model regresses right_t on its own p lags; the
        unrestricted model adds left's p lags), in ROWS of the shared
        input index.
      - ``condition_number_threshold`` — refuse the unrestricted fit
        when its design matrix's condition number exceeds this (a
        numerically meaningless F must not ship).

    Structural-metadata flags (OPR11), strict by default:
      - ``require_matching_frequency``
      - ``require_matching_missingness``
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    n_lags: int = Field(default=5, ge=1)
    condition_number_threshold: float = Field(default=1e10, gt=0)
    require_matching_frequency: bool = True
    require_matching_missingness: bool = True


__all__ = ["GrangerCausalityParams"]
