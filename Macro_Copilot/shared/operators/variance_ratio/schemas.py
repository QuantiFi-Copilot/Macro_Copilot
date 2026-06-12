"""variance_ratio — parameter schema.

Per OPR8: every consequential method variant is an explicit typed
field; each field's schema default mirrors the ``config.yaml`` default
(a test asserts they never diverge).

``variance_ratio`` is the FIRST knob-bearing A5 test with TWO genuine
choices (the zero-knob ``stationarity_adf``/``ljung_box``/
``normality_test`` precede it):

  - ``q`` — the aggregation horizon (``>= 2``).  ``None`` (the default)
    resolves to the minimal horizon ``2`` at runtime — an explicit,
    lineage-stamped default (the ``ljung_box.lags`` None→rule
    precedent), never a silent omission; an explicit integer pins it.
    YAML-authoritative (declared in ``config.yaml`` ``defaults:``).
  - ``robust`` — heteroskedasticity-consistent (Lo–MacKinlay M2)
    standardisation when ``True`` (default; the general-purpose-safe
    choice), the homoskedastic (M1) statistic when ``False``.  Both are
    legitimate Lo–MacKinlay statistics testing the SAME random-walk
    null, so per OPR8 this is an explicit typed knob — NOT a design-lock
    (unlike the overlapping+bias-corrected estimator, which is locked
    in the operator because non-overlapping tests a weaker, different
    estimator).  A structural method-selection default: schema-only,
    deliberately NOT in the YAML ``defaults:`` block (OPR8 — a param is
    either schema-default OR YAML-authoritative, never both).
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class VarianceRatioParams(BaseModel):
    """Parameters for the ``variance_ratio`` operator.

    Fields:
      - ``q`` — the aggregation horizon (VR compares the q-period
        variance to ``q`` times the one-period variance).  ``None``
        resolves to ``2`` (the minimal horizon) at runtime (recorded in
        lineage); an explicit integer ``>= 2`` pins it.
      - ``robust`` — ``True`` (default) for the heteroskedasticity-
        consistent (M2) z-statistic; ``False`` for the homoskedastic
        (M1) one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    q: Optional[int] = Field(default=None, ge=2)
    robust: bool = Field(default=True)


__all__ = ["VarianceRatioParams"]
