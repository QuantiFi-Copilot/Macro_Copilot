"""
rates_agent.ois.tools.scan_ois_extremes — config-driven OIS z-score scanner.

Sibling of ``rates_agent/sovereign_bonds/tools/scan_extremes/`` for the
OIS universe.  Replaces the legacy single-file
``rates_agent/ois/tools/scanner.py`` with the canonical four-file
folder layout:

    scan_ois_extremes/
      __init__.py
      config.yaml
      schemas.py
      compute.py

This migration closes the OIS half of PR-10G gap #4 (the two flat-file
scanners predating the per-tool-folder convention).  The sovereign
sibling ships in the same commit.

Backward-compat
---------------
The schemas hub at ``rates_agent/ois/tools/schemas/__init__.py``
re-exports the public schema classes so existing
``from rates_agent.ois.tools.schemas import OISScannerInput`` imports
continue to work.  The MCP wrapper at ``rates_agent/ois/mcp_server.py``
and the workflow registry at ``rates_agent/workflows/__init__.py``
both import from the canonical package path after this migration.
"""

from rates_agent.ois.tools.scan_ois_extremes.compute import (
    CONFIG_PATH,
    scan_ois_extremes,
)
from rates_agent.ois.tools.scan_ois_extremes.schemas import (
    OISScannerInput,
    OISScannerOutput,
    OISScannerResultRow,
)

__all__ = [
    "CONFIG_PATH",
    "scan_ois_extremes",
    "OISScannerInput",
    "OISScannerOutput",
    "OISScannerResultRow",
]
