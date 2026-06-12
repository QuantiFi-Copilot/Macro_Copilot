"""winsorize — parameter schema.

Per OPR8: every consequential method variant is an explicit typed
field; each field's schema default mirrors the ``config.yaml`` default.
A test asserts the two never diverge (OPR8).

Mode semantics:
  - ``quantile``: bounds are the FULL-SAMPLE empirical quantiles
    [q, 1−q].  The full-sample scope LOOKS AHEAD (a value early in the
    series is clipped using quantiles computed over later data) — this
    is the canonical pre-fit hygiene transform and the look-ahead is
    disclosed in the card, the YAML and lineage
    (``quantile_scope='full_sample'``); a rolling variant is declared
    in ``planned_extensions``.  ``lower_bound``/``upper_bound`` must
    be None in this mode.
  - ``absolute``: bounds are caller-supplied fixed values (at least
    one of ``lower_bound``/``upper_bound``, in the series' own
    units); ``quantile`` is ignored — and nulled in lineage per OPR14.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


WinsorizeMode = Literal["quantile", "absolute"]


class WinsorizeParams(BaseModel):
    """Parameters for the ``winsorize`` operator.

    Variants:
      - ``mode``        — quantile (symmetric empirical [q, 1−q]
                          clipping — the default) | absolute (fixed
                          caller-supplied bounds).
      - ``quantile``    — the symmetric tail mass q ∈ (0, 0.5) for
                          quantile mode (bounds = the q-th and
                          (1−q)-th full-sample quantiles).
      - ``lower_bound`` / ``upper_bound`` — fixed bounds for absolute
                          mode, in the series' own units; at least one
                          required there, both forbidden in quantile
                          mode.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: WinsorizeMode = "quantile"
    quantile: float = Field(default=0.05, gt=0.0, lt=0.5)
    lower_bound: Optional[float] = None
    upper_bound: Optional[float] = None


__all__ = ["WinsorizeParams", "WinsorizeMode"]
