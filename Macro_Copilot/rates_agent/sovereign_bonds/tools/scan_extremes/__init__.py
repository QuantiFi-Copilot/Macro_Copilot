"""
rates_agent.sovereign_bonds.tools.scan_extremes — config-driven sovereign z-score scanner.

This package replaces the legacy single-file
``rates_agent/sovereign_bonds/tools/scanner.py`` with the canonical
folder-per-tool layout:

    scan_extremes/
      __init__.py    (this file — public-API re-exports)
      config.yaml    (conventions + methodology metadata)
      schemas.py     (Pydantic input / output models)
      compute.py     (deterministic math, config-driven)

Backward-compat
---------------
External callers continue to import ``scan_extremes`` and the schema
classes from this package's canonical path:

    from rates_agent.sovereign_bonds.tools.scan_extremes import scan_extremes
    from rates_agent.sovereign_bonds.tools.schemas import ScannerInput

The first works because this ``__init__.py`` re-exports it.  The
second works because ``rates_agent.sovereign_bonds.tools.schemas/__init__.py``
(the re-export hub) imports the same classes from
``rates_agent.sovereign_bonds.tools.scan_extremes.schemas`` (this
package's canonical location).

Migration note
--------------
This migration closes PR-10G gap #4 (the two flat-file scanners
predating the per-tool-folder convention).  The OIS sibling at
``rates_agent/ois/tools/scan_ois_extremes/`` ships in the same commit.
The legacy paths
``rates_agent/sovereign_bonds/tools/scanner.py`` and
``rates_agent/sovereign_bonds/tools/schemas/scanner.py`` are deleted
in the same commit; the schemas hub re-exports from the canonical
package now.
"""

from rates_agent.sovereign_bonds.tools.scan_extremes.compute import (
    CONFIG_PATH,
    scan_extremes,
)
from rates_agent.sovereign_bonds.tools.scan_extremes.schemas import (
    ScannerInput,
    ScannerOutput,
    ScannerResultRow,
)

__all__ = [
    "CONFIG_PATH",
    "scan_extremes",
    "ScannerInput",
    "ScannerOutput",
    "ScannerResultRow",
]
