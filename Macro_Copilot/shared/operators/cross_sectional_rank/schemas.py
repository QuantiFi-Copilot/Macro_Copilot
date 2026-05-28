"""Pydantic parameter schema for cross_sectional_rank.

Ranks the columns of a typed Panel WITHIN each row (date) — i.e.
across the cross-section at each timestamp.  The ranking family's
consequential variants are exposed explicitly per the operator-
architecture admission rule (no hidden finance defaults):

  - ``method``       rank | percentile | normalized
        ``rank``        integer-style ordinal rank (1..N), ties per
                        ``tie_method``.  Units = COUNT.
        ``percentile``  0..100 percentile of the cross-section.
                        Units = PCT_RANK.
        ``normalized``  0..1 fractional rank.  Units = RATIO.
  - ``ascending``    True  → smallest value gets rank 1 (pandas
                            default).
                     False → largest value gets rank 1 (the usual
                            "rank #1 = top of the screen" convention
                            for desk scanners).  Left explicit so the
                            caller always declares the direction.
  - ``tie_method``   average | min | max | first | dense — how tied
                     values share ranks (pandas rank ``method``).

Distinct family note: this is the RANKING family.  Rolling z-score
normalization is a SEPARATE family (``rolling_zscore_panel``).  The
two are not folded together — that would be the wastebasket pattern
the architecture doc warns against.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CrossSectionalRankParams(BaseModel):
    """Parameters for ``cross_sectional_rank`` (V1 surface)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    method: Literal["rank", "percentile", "normalized"] = Field(
        default="rank",
        description=(
            "Ranking output kind. 'rank' = ordinal 1..N (units COUNT); "
            "'percentile' = 0..100 (units PCT_RANK); 'normalized' = "
            "0..1 fractional rank (units RATIO). All three are within "
            "the SAME ranking family — legitimate variants, unlike "
            "z-score which is a different family."
        ),
    )
    ascending: bool = Field(
        default=True,
        description=(
            "True → smallest value ranks 1 (pandas default). False → "
            "largest value ranks 1 (the 'rank #1 = top of screen' "
            "desk convention). Always declared explicitly — no hidden "
            "default direction."
        ),
    )
    tie_method: Literal["average", "min", "max", "first", "dense"] = Field(
        default="average",
        description=(
            "How tied values share ranks (pandas rank 'method'). "
            "'average' (default) splits the tie evenly; 'min'/'max' "
            "assign the low/high rank to all ties; 'first' breaks ties "
            "by column order; 'dense' leaves no gaps after a tie."
        ),
    )


__all__ = ["CrossSectionalRankParams"]
