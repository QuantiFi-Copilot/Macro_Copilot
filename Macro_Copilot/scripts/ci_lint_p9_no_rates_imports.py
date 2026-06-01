#!/usr/bin/env python
"""scripts/ci_lint_p9_no_rates_imports.py — PR-10 P9 CI lint.

**P9 enforcement.**  Per ``tmp/orchestration.md`` §PR-10:

  > A grep test that fails CI if any file under ``shared/operators/``
  > imports from ``rates_agent/`` (P9 enforcement).

P9 is the substrate's finance-blindness discipline: operators MUST
NOT depend on any domain code.  An import from ``rates_agent/`` into
``shared/operators/`` would invert the layer and lock the substrate
to a specific domain.  This lint is the forcing function.

Usage
=====

  python scripts/ci_lint_p9_no_rates_imports.py [--root PATH]

Exit codes
----------
  0 — no rates_agent imports detected in shared/operators/.
  1 — at least one violation found; report printed.
  2 — usage / I/O error.

CI wiring
---------

Add to ``.github/workflows/open_dag_lints.yml``:

  - name: P9 — no rates_agent imports in shared/operators/
    run: python scripts/ci_lint_p9_no_rates_imports.py

Finance-blindness
=================

This script reads source files but does NOT execute or import them.
Pure source-scan — regex against ``rates_agent`` import statements.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import List, NamedTuple, Optional, Tuple


DEFAULT_ROOT = Path(__file__).resolve().parent.parent / "shared" / "operators"

# Match ``from rates_agent[...] import [...]`` and ``import rates_agent[...]``.
_RATES_IMPORT_RE = re.compile(
    r"^\s*(?:from|import)\s+rates_agent(?:\.[A-Za-z_.]+)?\b",
    re.MULTILINE,
)


class Violation(NamedTuple):
    """One offending file + line."""

    file: Path
    line_number: int
    snippet: str


def scan(root: Path) -> List[Violation]:
    """Walk ``root`` (recursive) and return every ``rates_agent``
    import found in ``*.py`` files."""
    out: List[Violation] = []
    if not root.is_dir():
        return out
    for py in sorted(root.rglob("*.py")):
        try:
            text = py.read_text(encoding="utf-8")
        except OSError:
            continue
        for m in _RATES_IMPORT_RE.finditer(text):
            # Compute line number from offset.
            line_no = text.count("\n", 0, m.start()) + 1
            line_text = text.splitlines()[line_no - 1] if line_no - 1 < len(text.splitlines()) else ""
            out.append(Violation(
                file=py,
                line_number=line_no,
                snippet=line_text.strip(),
            ))
    return out


def render_report(violations: List[Violation], root: Path) -> str:
    lines: List[str] = []
    lines.append(
        "P9 lint: no `rates_agent` imports under "
        f"{root}/ (finance-blindness)."
    )
    lines.append("")
    if not violations:
        lines.append("PASS: no rates_agent imports found.")
        return "\n".join(lines)
    lines.append(f"FAIL: {len(violations)} violation(s):")
    for v in violations:
        try:
            rel = v.file.relative_to(root.parent.parent)
        except ValueError:
            rel = v.file
        lines.append(f"  {rel}:{v.line_number}: {v.snippet}")
    lines.append("")
    lines.append(
        "P9 — shared/operators/ is finance-blind substrate.  Operators "
        "MUST NOT import from rates_agent/.  Refactor the offending "
        "import OR move the operator out of shared/."
    )
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="CI lint: P9 — no rates_agent imports under shared/operators/.",
    )
    parser.add_argument(
        "--root", type=Path, default=DEFAULT_ROOT,
        help="Root to scan (default <repo>/shared/operators).",
    )
    args = parser.parse_args(argv)
    try:
        violations = scan(args.root)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(render_report(violations, args.root))
    return 1 if violations else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
