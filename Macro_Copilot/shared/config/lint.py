"""
lint.py — CI consistency check for tool configs
================================================

Walks the tool-config tree and flags conventions whose **value**
disagrees across tools.  A future ``ois.curve_spread`` and
``sovereign_bonds.curve_spread`` should both compute z-scores on the
same window length; if one drifts to 504 days while the other stays at
252, this lint surfaces it before the silent inconsistency reaches the
gauntlet.

What it does NOT flag
---------------------
- Conventions present in only one tool.  That's fine — a tool may have
  conventions that no other tool uses.
- Different ``source`` or ``rationale`` text across tools.  Only the
  ``value`` is compared, because two tools can legitimately cite
  different sources for the same numeric default.

Run as a CLI::

    python -m shared.config.lint
    python -m shared.config.lint --root /custom/path

Exit code is non-zero if any inconsistency is found, suitable for CI.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from shared.config.operator_config import (
    OperatorConfig,
    OperatorConfigError,
    load_operator_config,
)
from shared.config.tool_config import ToolConfigError, load_tool_config


# ============================================================================
# DISCOVERY
# ============================================================================

# Conventional layout for tool configs:
#
#   rates_agent/<domain>/tools/<tool_name>/config.yaml
#   fx_agent/<domain>/tools/<tool_name>/config.yaml
#
# This glob walks all of them in one pass. fx_agent added 2026-05-27 as
# part of the FX Phase D/E1 compliance follow-up — extends lint coverage
# to the FX subdomain tools that ship under the same primitive contract
# (PR3 — 4-file pattern, PR7 — YAML conventions, PR12 — registered
# methodology sources, PR13 — cross-config consistency). Mirrors the
# existing pattern; no rates_agent behavioural change.
_DEFAULT_TOOL_CONFIG_GLOBS: Tuple[str, ...] = (
    "rates_agent/*/tools/*/config.yaml",
    "fx_agent/*/tools/*/config.yaml",
)

# Operator configs (build plan v5 / R4) live alongside operator code.
# Path determines kind; existing tool YAMLs are NOT touched.
_DEFAULT_OPERATOR_CONFIG_GLOBS: Tuple[str, ...] = (
    "shared/operators/*/config.yaml",
)


def discover_tool_configs(
    project_root: Path,
    globs: Iterable[str] = _DEFAULT_TOOL_CONFIG_GLOBS,
) -> List[Path]:
    """Return every ``config.yaml`` matching the conventional tool path.

    Sorted absolute paths so output is stable across runs (good for CI
    diff comparisons).
    """
    seen: set[Path] = set()
    for pattern in globs:
        for p in project_root.glob(pattern):
            seen.add(p.resolve())
    return sorted(seen)


def discover_operator_configs(
    project_root: Path,
    globs: Iterable[str] = _DEFAULT_OPERATOR_CONFIG_GLOBS,
) -> List[Path]:
    """Return every operator ``config.yaml`` under ``shared/operators/``.

    Mirrors ``discover_tool_configs`` but walks the operator-layer path.
    Sorted absolute paths.
    """
    seen: set[Path] = set()
    for pattern in globs:
        for p in project_root.glob(pattern):
            seen.add(p.resolve())
    return sorted(seen)


def validate_operator_configs(config_paths: Iterable[Path]) -> List[OperatorConfig]:
    """Schema-validate every operator config; return the parsed configs.

    v1 operator lint is **schema-validation-only** (build plan v5 / R4).
    Default-drift detection across operators in the same method_family
    is a no-op until the second operator in any family ships, so we
    don't pretend to do it yet.  Schema validation alone is the load-
    bearing check during Phase 1A — it catches malformed defaults,
    missing fields, and ``valid_values`` violations.

    Raises ``OperatorConfigError`` on any validation failure (CI fails
    loudly).
    """
    configs: List[OperatorConfig] = []
    for path in config_paths:
        cfg = load_operator_config(path)  # raises OperatorConfigError on bad input
        configs.append(cfg)
    return configs


# ============================================================================
# CONSISTENCY ISSUE
# ============================================================================

@dataclass(frozen=True)
class ConsistencyIssue:
    """One inconsistency finding from the lint.

    Holds enough context for a CI log line that points the developer
    at the offending conventions across multiple tools.
    """

    convention_name: str
    occurrences: Tuple["_Occurrence", ...]

    def format_for_human(self) -> str:
        """Render as a multi-line string for terminal / CI logs."""
        header = (
            f"[INCONSISTENT] convention {self.convention_name!r} "
            f"has {len(self.occurrences)} disagreeing values across tools:"
        )
        body = "\n".join(
            f"  - tool={occ.tool_name!r}  value={occ.value!r}  "
            f"source={occ.source!r}  ({occ.config_path})"
            for occ in self.occurrences
        )
        return f"{header}\n{body}"


@dataclass(frozen=True)
class _Occurrence:
    tool_name: str
    config_path: Path
    value: object
    source: str


# ============================================================================
# CHECK
# ============================================================================

def check_yaml_consistency(
    config_paths: Iterable[Path],
) -> List[ConsistencyIssue]:
    """Inspect every config in ``config_paths`` and return the list of
    convention-value disagreements.

    Empty list = clean.

    Parameters
    ----------
    config_paths
        Paths to ``config.yaml`` files.  Use ``discover_tool_configs``
        to find them automatically, or pass an explicit list (handy
        for tests).

    Raises
    ------
    ToolConfigError
        If any config file fails to load or validate.  We don't catch
        that here — a broken YAML is a precondition, not a lint
        finding, and should fail CI loudly with the original error.
    """
    # convention_name -> list of occurrences across tools
    occurrences_by_name: Dict[str, List[_Occurrence]] = defaultdict(list)

    for path in config_paths:
        cfg = load_tool_config(path)
        for conv_name, conv in cfg.conventions.items():
            occurrences_by_name[conv_name].append(
                _Occurrence(
                    tool_name=cfg.tool.name,
                    config_path=path,
                    value=conv.value,
                    source=conv.source,
                )
            )

    issues: List[ConsistencyIssue] = []
    for conv_name, occs in occurrences_by_name.items():
        if len(occs) < 2:
            continue  # only declared in one tool — nothing to disagree with
        unique_values = {_hashable(occ.value) for occ in occs}
        if len(unique_values) > 1:
            # Stable order for diff-friendly output: sort by tool name
            # then config path.
            issues.append(
                ConsistencyIssue(
                    convention_name=conv_name,
                    occurrences=tuple(
                        sorted(occs, key=lambda o: (o.tool_name, str(o.config_path)))
                    ),
                )
            )

    # Stable order across runs — sort by convention name.
    issues.sort(key=lambda i: i.convention_name)
    return issues


def _hashable(value: object) -> object:
    """Return a hashable representation of ``value`` for set membership.

    Most convention values are already hashable (bool, int, float,
    str).  Defensive against future schema growth — if someone ever
    extends Convention to allow tuples/lists, we'd need to convert
    here.  For commit 1 the field validator forbids non-scalars, so
    this is effectively pass-through.
    """
    if isinstance(value, list):
        return tuple(value)
    return value


# ============================================================================
# CLI
# ============================================================================

def _project_root() -> Path:
    """Default project root — two levels up from this file (Macro_Copilot/)."""
    return Path(__file__).resolve().parent.parent.parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Walk every rates_agent/*/tools/*/config.yaml and flag "
            "conventions whose value disagrees across tools."
        ),
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=_project_root(),
        help="Project root to search under (default: this file's project root).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only emit output on failure.",
    )
    args = parser.parse_args(argv)

    paths = discover_tool_configs(args.root)
    if not args.quiet:
        if not paths:
            print(
                "No tool configs found under "
                f"{args.root} (searched: rates_agent/*/tools/*/config.yaml)"
            )
        else:
            print(f"Checking {len(paths)} tool config(s) under {args.root}:")
            for p in paths:
                print(f"  - {p.relative_to(args.root) if p.is_relative_to(args.root) else p}")

    try:
        issues = check_yaml_consistency(paths)
    except ToolConfigError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    # Operator configs (build plan v5 / R4) — schema-validation only in v1.
    op_paths = discover_operator_configs(args.root)
    if not args.quiet and op_paths:
        print(
            f"\nChecking {len(op_paths)} operator config(s) under "
            f"{args.root}/shared/operators/:"
        )
        for p in op_paths:
            print(
                "  - "
                f"{p.relative_to(args.root) if p.is_relative_to(args.root) else p}"
            )
    try:
        validate_operator_configs(op_paths)
    except OperatorConfigError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    if not issues:
        if not args.quiet:
            print("OK — no convention drift detected.")
        return 0

    print(f"\n{len(issues)} consistency issue(s) found:\n", file=sys.stderr)
    for issue in issues:
        print(issue.format_for_human(), file=sys.stderr)
        print("", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
