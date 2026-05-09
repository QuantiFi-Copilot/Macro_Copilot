"""
tests/conftest.py — pytest configuration
==========================================

Two responsibilities:

1.  Project-root ``sys.path`` setup so test modules can import
    ``rates_agent``, ``shared``, ``orchestrator`` etc. without each
    file manipulating ``sys.path`` itself.

2.  Excluding the pre-existing CLI smoke-test scripts from collection.
    Those files (``test_ws_chat.py``, the ``*_sql_validation.py``
    runners) are standalone scripts with their own
    ``if __name__ == "__main__":`` entry points and argparse
    surfaces; they aren't pytest tests and shouldn't be collected.

    Some are still imported by the multi-agent gauntlet
    (``tests/test_multi_agent_prompt_gauntlet.py``) as helper
    modules — that import path is fine because pytest collects the
    gauntlet itself, not its helpers.

Historical note: an earlier version of this list included a set of
``test_*_direct.py`` files that pre-dated pytest adoption and had
stale imports.  Those files have since been deleted from the tree
or replaced by ``*_sql_validation.py`` equivalents; the ignore
list below is now narrowed to the entries that genuinely exist.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# Files in tests/ that pytest should not try to import or collect.
# Listed by basename; pytest matches against the filename within this
# conftest's directory.  Every entry below corresponds to a file that
# actually exists in tests/ — keep it that way.
collect_ignore = [
    # WebSocket REPL client; run as `python tests/test_ws_chat.py`.
    "test_ws_chat.py",

    # SQL-baseline validators.  Each is a standalone CLI runner that
    # cross-checks one tool's output against an independent SQL
    # implementation.  Imported as helper modules by
    # tests/test_multi_agent_prompt_gauntlet.py.  Not pytest tests
    # themselves.
    "test_butterfly_sql_validation.py",
    "test_cross_market_sql_validation.py",
    "test_curve_move_classifier_sql_validation.py",
    "test_curve_spread_sql_validation.py",
    "test_ois_cross_market_spread_sql_validation.py",
    "test_ois_curve_spread_sql_validation.py",
    "test_ois_forward_rate_sql_validation.py",
    "test_scanner_sql_validation.py",
    "test_swap_spread_sql_validation.py",
    "test_yield_levels_sql_validation.py",
    "test_real_yield_level_sql_validation.py",
]
