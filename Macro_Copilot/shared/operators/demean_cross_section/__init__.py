"""shared.operators.demean_cross_section — public API.

Stable re-exports so callers can ``from shared.operators.
demean_cross_section import demean_cross_section`` without reaching
into submodules.  The four exports (CONFIG_PATH, the operator, the
Params, the Error) are the OPR16 ``__init__``-export contract every
operator satisfies.
"""

from pathlib import Path

from shared.operators.demean_cross_section.operator import (
    demean_cross_section,
    DemeanCrossSectionError,
)
from shared.operators.demean_cross_section.schemas import (
    DemeanCrossSectionParams,
)


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "demean_cross_section",
    "DemeanCrossSectionError",
    "DemeanCrossSectionParams",
    "CONFIG_PATH",
]
