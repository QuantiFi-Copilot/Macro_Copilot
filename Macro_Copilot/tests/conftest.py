"""
tests/conftest.py — pytest configuration
==========================================

Ensures the project root is on ``sys.path`` so test modules can import
``rates_agent``, ``shared``, ``orchestrator`` etc. without each file
having to manipulate ``sys.path`` itself.

The pre-existing CLI smoke-test scripts (``tests/test_*_direct.py``)
all do their own ``sys.path.insert``; they pre-date pytest adoption and
are not picked up by pytest because they lack ``test_*`` functions.
This conftest therefore does NOT collide with them.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
