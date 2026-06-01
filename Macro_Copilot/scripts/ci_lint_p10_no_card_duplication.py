#!/usr/bin/env python
"""scripts/ci_lint_p10_no_card_duplication.py — PR-10 P10 CI lint.

**P10 enforcement.**  Per ``tmp/orchestration.md`` §PR-10:

  > A grep test that fails CI if any operator's ``config.yaml``
  > description content is duplicated as a Python string anywhere
  > under ``shared/operators/`` or ``shared/workflow/`` (P10
  > enforcement).

P10 is the **single source of truth** discipline: rich description
content lives ONLY in each operator's bundled ``config.yaml``
``card:`` block.  Duplicating that content as a Python string under
``shared/operators/`` or ``shared/workflow/`` would create two
sources of truth.  This lint catches the regression by extracting
distinctive sentences from each operator's YAML card and scanning
the Python sources for matches.

Heuristic
=========

For each operator's ``config.yaml`` the script extracts every
``one_line`` + the first sentence of each ``when_to_use`` /
``when_not_to_use`` / ``upstream_requirements`` / ``downstream_pattern``
bullet.  For each candidate sentence ≥ ``MIN_SNIPPET_LEN`` characters,
search the Python source tree for an EXACT match.  Any match is a
P10 violation.

Usage
=====

  python scripts/ci_lint_p10_no_card_duplication.py [--root PATH]

Exit codes
----------
  0 — no card-text duplication detected.
  1 — at least one violation; report printed.
  2 — usage / I/O error.

CI wiring
---------

  - name: P10 — no card-content duplication
    run: python scripts/ci_lint_p10_no_card_duplication.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

import yaml


_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OPERATORS = _REPO_ROOT / "shared" / "operators"
DEFAULT_WORKFLOW = _REPO_ROOT / "shared" / "workflow"

# Snippet length floor for the duplication scan.  Short fragments
# ("Series", "fan-in", etc.) would false-positive against legitimate
# substrate vocabulary.  60 chars is well above the floor for any
# legitimate finance idiom.
MIN_SNIPPET_LEN = 60


class Violation(NamedTuple):
    operator: str
    snippet: str
    found_in: Path
    line_number: int


def _extract_snippets(card_block: Dict[str, Any]) -> List[str]:
    """Extract distinctive snippets from a card block.

    Returns every ``one_line`` + every list-shaped bullet under
    ``when_to_use`` / ``when_not_to_use`` / ``upstream_requirements``
    / ``downstream_pattern`` whose length is ≥ MIN_SNIPPET_LEN.
    """
    out: List[str] = []
    for key in ("one_line",):
        v = card_block.get(key)
        if isinstance(v, str) and len(v.strip()) >= MIN_SNIPPET_LEN:
            out.append(v.strip())
    for key in (
        "when_to_use",
        "when_not_to_use",
        "upstream_requirements",
        "downstream_pattern",
    ):
        v = card_block.get(key)
        if not isinstance(v, list):
            continue
        for item in v:
            if isinstance(item, str) and len(item.strip()) >= MIN_SNIPPET_LEN:
                out.append(item.strip())
    return out


def _find_match(snippet: str, root: Path) -> List[Tuple[Path, int]]:
    """Search ``root`` (recursive) for ``snippet`` appearing in any
    ``.py`` file.  Returns (path, line_number) tuples."""
    hits: List[Tuple[Path, int]] = []
    if not root.is_dir():
        return hits
    for py in root.rglob("*.py"):
        try:
            text = py.read_text(encoding="utf-8")
        except OSError:
            continue
        if snippet not in text:
            continue
        # Find the first line offset.
        idx = text.find(snippet)
        line_no = text.count("\n", 0, idx) + 1
        hits.append((py, line_no))
    return hits


def scan(
    operators_root: Path,
    workflow_root: Path,
) -> List[Violation]:
    """Scan every operator's config.yaml ``card:`` block; assert
    no extracted snippet appears in any Python source under
    ``shared/operators/`` or ``shared/workflow/``."""
    violations: List[Violation] = []
    if not operators_root.is_dir():
        return violations
    for op_dir in sorted(operators_root.iterdir()):
        cfg = op_dir / "config.yaml"
        if not cfg.is_file():
            continue
        try:
            doc = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(doc, dict):
            continue
        card = doc.get("card")
        if not isinstance(card, dict):
            continue
        snippets = _extract_snippets(card)
        for snippet in snippets:
            for root in (operators_root, workflow_root):
                hits = _find_match(snippet, root)
                # Filter out the YAML file itself (only Python sources
                # are violations).
                hits = [
                    (p, ln) for (p, ln) in hits
                    if p.suffix == ".py"
                ]
                for (p, ln) in hits:
                    violations.append(Violation(
                        operator=op_dir.name,
                        snippet=snippet[:80] + ("..." if len(snippet) > 80 else ""),
                        found_in=p,
                        line_number=ln,
                    ))
    return violations


def render_report(violations: List[Violation]) -> str:
    lines: List[str] = []
    lines.append(
        "P10 lint: no operator card content duplicated as Python "
        "string under shared/operators/ or shared/workflow/."
    )
    lines.append("")
    if not violations:
        lines.append("PASS: no card-content duplication found.")
        return "\n".join(lines)
    lines.append(f"FAIL: {len(violations)} violation(s):")
    for v in violations:
        try:
            rel = v.found_in.relative_to(_REPO_ROOT)
        except ValueError:
            rel = v.found_in
        lines.append(
            f"  operator={v.operator}: snippet={v.snippet!r}"
        )
        lines.append(f"    duplicated in {rel}:{v.line_number}")
    lines.append("")
    lines.append(
        "P10 — operator description content MUST live ONLY in the "
        "operator's config.yaml.  Move the duplicated string back to "
        "YAML or delete the duplicate."
    )
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="CI lint: P10 — no card duplication.",
    )
    parser.add_argument(
        "--operators-root", type=Path, default=DEFAULT_OPERATORS,
        help="Operators root (default <repo>/shared/operators).",
    )
    parser.add_argument(
        "--workflow-root", type=Path, default=DEFAULT_WORKFLOW,
        help="Workflow root (default <repo>/shared/workflow).",
    )
    args = parser.parse_args(argv)
    try:
        violations = scan(args.operators_root, args.workflow_root)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(render_report(violations))
    return 1 if violations else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
