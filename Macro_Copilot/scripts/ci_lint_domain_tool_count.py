#!/usr/bin/env python
"""scripts/ci_lint_domain_tool_count.py — PR-10 CI lint.

**Per-domain tool-count cap.**  Parses each domain MCP server file
(``rates_agent/<domain>/mcp_server.py``) and counts ``@mcp.tool()``
decorators.  Fails (exit code 1) when any domain exceeds
``MAX_TOOLS_PER_DOMAIN``.

Why this lint exists
====================

Per ``tmp/orchestration.md`` §PR-10:
  > scripts/ci_lint_domain_tool_count.py: parse each domain MCP
  > server, count ``@mcp.tool()`` decorators, fail if any domain
  > exceeds MAX_TOOLS_PER_DOMAIN (TBD; suggest 25 for the PoC —
  > forces a sub-domain split before fan-out gets unmanageable).

The Selector's per-domain catalogue is what L2 reasons over.  When a
domain accumulates too many primitives, the Selector's prompt grows
unboundedly and prior density per choice falls.  The lint is the
forcing function: a domain at the cap MUST be split into
sub-domains before more primitives are added.

Usage
=====

  python scripts/ci_lint_domain_tool_count.py [--limit N] [--root PATH]

Defaults
--------
  --limit 25
  --root   <repo>/rates_agent

Exit codes
----------
  0 — every domain under the cap.
  1 — at least one domain over the cap; report printed.
  2 — usage / I/O error.

CI wiring
---------

Add to ``.github/workflows/`` (or equivalent):

  - name: Domain tool-count lint
    run: python scripts/ci_lint_domain_tool_count.py --limit 25

Finance-blindness
=================

This script reads ``rates_agent/`` source files but does NOT execute
them or import any domain code.  It's a pure source-scan lint —
parses files as text, counts decorator occurrences.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple


DEFAULT_MAX_TOOLS_PER_DOMAIN = 25
DEFAULT_ROOT = Path(__file__).resolve().parent.parent / "rates_agent"

# Regex for ``@mcp.tool()`` decorators.  Tolerates whitespace and
# arguments inside the parentheses (e.g. ``@mcp.tool(name="x")``).
_TOOL_DECORATOR_RE = re.compile(
    r"^\s*@(?:mcp|app|server)\.tool\s*\(", re.MULTILINE,
)


class DomainToolCount(NamedTuple):
    """One domain's tool count + source file path.  NamedTuple
    (not dataclass) so the module loads cleanly under
    ``importlib.util.spec_from_file_location`` — dataclasses with
    ``from __future__ import annotations`` need ``__module__``
    resolution that the spec-loader path doesn't provide."""

    domain: str
    server_path: Path
    tool_count: int


def find_domain_mcp_servers(root: Path) -> List[Tuple[str, Path]]:
    """Return [(domain_name, server_file_path), ...] for every
    rates_agent/<domain>/mcp_server.py file.

    Excludes ``rates_agent/workflows/mcp_server.py`` — workflows is
    a cross-domain harness, not a per-domain primitive surface.
    """
    out: List[Tuple[str, Path]] = []
    if not root.is_dir():
        return out
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        server = child / "mcp_server.py"
        if not server.is_file():
            continue
        if child.name == "workflows":
            # Cross-domain harness — not subject to the per-domain cap.
            continue
        out.append((child.name, server))
    return out


def count_tool_decorators(server_path: Path) -> int:
    """Count ``@mcp.tool()`` / ``@server.tool()`` / ``@app.tool()``
    decorators in the given file."""
    try:
        text = server_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(
            f"Could not read MCP server file {server_path}: {exc}"
        ) from exc
    return len(_TOOL_DECORATOR_RE.findall(text))


def audit(
    root: Path,
    limit: int,
) -> Tuple[List[DomainToolCount], List[DomainToolCount]]:
    """Audit every domain under ``root``.  Returns
    (within_cap, over_cap) tuples of DomainToolCount."""
    within: List[DomainToolCount] = []
    over: List[DomainToolCount] = []
    for domain_name, server_path in find_domain_mcp_servers(root):
        count = count_tool_decorators(server_path)
        record = DomainToolCount(
            domain=domain_name,
            server_path=server_path,
            tool_count=count,
        )
        if count > limit:
            over.append(record)
        else:
            within.append(record)
    return within, over


def render_report(
    within: List[DomainToolCount],
    over: List[DomainToolCount],
    limit: int,
) -> str:
    """Render a deterministic plain-text report."""
    lines: List[str] = []
    lines.append(
        f"Domain tool-count lint — limit={limit} primitives per domain."
    )
    lines.append("")
    total_domains = len(within) + len(over)
    lines.append(f"Audited {total_domains} domain(s):")
    for r in sorted(within, key=lambda d: d.domain):
        lines.append(f"  OK    {r.domain}: {r.tool_count}")
    for r in sorted(over, key=lambda d: d.domain):
        lines.append(f"  OVER  {r.domain}: {r.tool_count} (cap {limit})")
    lines.append("")
    if over:
        lines.append(
            f"FAIL: {len(over)} domain(s) over the cap.  Split into "
            "sub-domains before adding more primitives."
        )
    else:
        lines.append("PASS: every domain is within the per-domain primitive cap.")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="CI lint: per-domain MCP tool-count cap.",
    )
    parser.add_argument(
        "--limit", type=int, default=DEFAULT_MAX_TOOLS_PER_DOMAIN,
        help=(
            f"Maximum tools allowed per domain (default "
            f"{DEFAULT_MAX_TOOLS_PER_DOMAIN})."
        ),
    )
    parser.add_argument(
        "--root", type=Path, default=DEFAULT_ROOT,
        help=(
            "Root directory containing per-domain mcp_server.py files "
            "(default <repo>/rates_agent)."
        ),
    )
    args = parser.parse_args(argv)

    try:
        within, over = audit(args.root, args.limit)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    report = render_report(within, over, args.limit)
    print(report)
    return 1 if over else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
