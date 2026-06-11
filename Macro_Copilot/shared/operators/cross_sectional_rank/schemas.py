"""cross_sectional_rank — parameter schema.

Per OPR8: every consequential method variant is an explicit typed
field; each field's schema default mirrors the ``config.yaml`` default
(the YAML is the authoritative source — the operator resolves from it
when ``params is None``).  A test asserts the two never diverge (OPR8).

Single-input operator (one SeriesSet): no ``require_matching_*`` flags
(OPR11's multi-artifact controls govern cross-ARTIFACT agreement; the
within-set same-units requirement is a hard mathematical precondition
of ranking and is enforced in code with no opt-out — a mixed-unit
ranking has no honest interpretation).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# pandas DataFrame.rank tie-handling methods (the reference
# implementation; the config cites it as the methodology source).
RankTies = Literal["average", "min", "max", "first", "dense"]

# ordinal → positions 1..N (COUNT units); normalized → percentile of
# the member within the cross-section, 0–100 (PCT_RANK units, the
# percentile_rank precedent).  The output ARTIFACT type is SeriesSet
# for both (OPR2); only the units tag follows the variant (the
# conditional_aggregate COUNT-vs-passthrough precedent).
RankMethod = Literal["ordinal", "normalized"]


class CrossSectionalRankParams(BaseModel):
    """Parameters for the ``cross_sectional_rank`` operator.

    Variants:
      - ``rank_method``  — ordinal (1..N positions, COUNT units) |
                           normalized (0–100 percentile of the member
                           within the cross-section, PCT_RANK units).
      - ``ascending``    — True ranks smallest value = 1 (the plain
                           mathematical default); set False for
                           largest-first screens.
      - ``ties``         — pandas rank tie-handling method.
      - ``min_members``  — minimum non-NaN members at a date for that
                           date's ranks to be emitted; dates below the
                           floor emit NaN for every member.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rank_method: RankMethod = "ordinal"
    ascending: bool = True
    ties: RankTies = "average"
    min_members: int = Field(default=2, ge=2)


__all__ = ["CrossSectionalRankParams", "RankMethod", "RankTies"]
