"""
rates_agent.sovereign_bonds.tools.curve_move_classifier — config-driven
curve-move classifier.

Renamed from the legacy ``curve_regime`` tool.  See ``compute.py``'s
"Why rename" docstring for the rationale: this tool classifies a
single observed move, NOT a statistical persistence state ("regime"
in the technical sense).

Folder layout follows the per-tool pattern established by the
tool-config pilot:

    curve_move_classifier/
      __init__.py    (this file — public-API re-exports)
      config.yaml    (conventions + methodology metadata)
      schemas.py     (Pydantic input / output models)
      compute.py     (deterministic logic, config-driven)

External callers reach the public API via this package's path:

    from rates_agent.sovereign_bonds.tools.curve_move_classifier import (
        classify_curve_move_compute,
        CurveMoveInput,
        CurveMoveOutput,
        CONFIG_PATH,
    )

Or via the sovereign-bonds schemas hub:

    from rates_agent.sovereign_bonds.tools.schemas import CurveMoveInput

Note for tests: ``__init__.py`` re-exports only public symbols.
``fetch_tenor_group``, ``date``, etc. live inside ``compute.py``'s
namespace; tests must patch them at
``...curve_move_classifier.compute.<name>``, NOT on the package init.
"""

from rates_agent.sovereign_bonds.tools.curve_move_classifier.compute import (
    CONFIG_PATH,
    classify_curve_move_compute,
)
from rates_agent.sovereign_bonds.tools.curve_move_classifier.schemas import (
    CurveMoveCurrentMetrics,
    CurveMoveInput,
    CurveMoveOutput,
)


__all__ = [
    "CONFIG_PATH",
    "classify_curve_move_compute",
    "CurveMoveCurrentMetrics",
    "CurveMoveInput",
    "CurveMoveOutput",
]
