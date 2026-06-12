"""resample — parameter schema.

Per OPR8: every consequential method variant is an explicit typed
field; each field's schema default mirrors the ``config.yaml`` default.
A test asserts the two never diverge (OPR8).

DESIGN LOCKS (OPR7, documented in the operator module, recorded in
lineage): period anchors and labelling are FIXED — weekly buckets
anchor to Friday (``W-FRI``), monthly/quarterly/yearly to period END
(``ME``/``QE``/``YE``), and both ``label`` and ``closed`` are
``'right'`` (each bucket is labelled by its period-end date).  Allowing
label/closed variants would silently re-date financial observations —
a P5 hazard with no analytical gain.  Upsampling is structurally
impossible through this schema (the target set is W/M/Q/Y only) and a
row-count guard refuses any residual fabrication case.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


ResampleFrequency = Literal["W", "M", "Q", "Y"]
ResampleMethod = Literal["last", "mean", "first", "max", "min"]


class ResampleParams(BaseModel):
    """Parameters for the ``resample`` operator.

    Variants:
      - ``target_frequency`` — the lower frequency to bucket into:
        W (weekly, Friday-anchored) | M | Q | Y (period-end
        anchored).  DOWNSAMPLING ONLY.
      - ``method`` — how each bucket reduces its rows: last (the
        period-end reading — the default) | mean | first | max | min.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    target_frequency: ResampleFrequency = "W"
    method: ResampleMethod = "last"


__all__ = ["ResampleParams", "ResampleFrequency", "ResampleMethod"]
