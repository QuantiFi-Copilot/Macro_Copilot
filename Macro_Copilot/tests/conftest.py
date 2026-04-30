"""
tests/conftest.py — pytest configuration
==========================================

Two responsibilities:

1.  Project-root ``sys.path`` setup so test modules can import
    ``rates_agent``, ``shared``, ``orchestrator`` etc. without each
    file manipulating ``sys.path`` itself.

2.  Excluding the pre-existing CLI smoke-test scripts from collection.
    Those files (``tests/test_*_direct.py``, ``tests/test_ws_chat.py``)
    are standalone runners (argparse + ``if __name__ == "__main__":``)
    that pre-date pytest adoption.  Several of them import from
    ``rates_agent.tools.*`` — a path that moved to
    ``rates_agent.sovereign_bonds.tools.*`` during the earlier
    supervisor refactor — so attempting to import them at collection
    time errors out.  They still run as CLI scripts; re-pathing those
    imports is its own follow-up PR.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# Files in tests/ that pytest should not try to import or collect.
# Listed by basename; pytest matches against the filename within this
# conftest's directory.
collect_ignore = [
    "test_butterfly_direct.py",
    "test_cross_market_direct.py",
    "test_curve_regime_direct.py",
    "test_scanner_direct.py",
    "test_tool_direct.py",
    "test_ws_chat.py",
    "test_yield_levels_direct.py",
]
