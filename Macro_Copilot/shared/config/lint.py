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
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple

from shared.config.operator_config import (
    OperatorConfig,
    OperatorConfigError,
    load_operator_config,
)
from shared.config.tool_config import ToolConfigError, load_tool_config


# ============================================================================
# DISCOVERY
# ============================================================================

# Conventional layout for tool configs (lands fully in commit 3 of the pilot):
#
#   rates_agent/<domain>/tools/<tool_name>/config.yaml
#
# This glob walks all of them in one pass.
_DEFAULT_TOOL_CONFIG_GLOBS: Tuple[str, ...] = (
    "rates_agent/*/tools/*/config.yaml",
)

# Operator configs (build plan v5 / R4) live alongside operator code.
# Path determines kind; existing tool YAMLs are NOT touched.
_DEFAULT_OPERATOR_CONFIG_GLOBS: Tuple[str, ...] = (
    "shared/operators/*/config.yaml",
)

# The AUTHORITATIVE methodology-source-tag registry (PR12 / OPR12 / P8).
# Every ``Convention.source`` (tool configs) and every
# ``OperatorDefault.source`` (operator configs) MUST reference a tag
# registered here as a ``### `tag` `` header, or match one of the
# registered pattern families (``_REGISTERED_TAG_PATTERNS`` below) that
# ``docs_revamped/03_standards/methodology_disclosure.md`` §2 documents
# as open families.  This file is parsed by ``load_registered_tags`` so
# the registry stays a single source of truth — the lint never carries a
# hand-maintained copy of the tag set.
_METHODOLOGY_SOURCE_REGISTRY: str = "docs/architecture/methodology_sources.md"

# Open pattern families registered in
# ``docs_revamped/03_standards/methodology_disclosure.md`` §2.  A
# ``source`` tag is registered if it either appears verbatim as a
# ``### `tag` `` header in ``methodology_sources.md`` OR matches one of
# these anchored families.  Keeping these as patterns (rather than one
# exact entry per instance) is the documented contract for the open
# families: a new ADR-cited tag or a new ``<primitive>_primitive_v1``
# tag is registered by the family, not by a per-tag registry edit.
_REGISTERED_TAG_PATTERNS: Tuple[re.Pattern[str], ...] = (
    # ``industry_standard_<concept>`` — widely-accepted convention.
    re.compile(r"^industry_standard_[a-z0-9_]+$"),
    # ``adr_<NNNN>_<concept>`` — convention fixed by a specific ADR.
    re.compile(r"^adr_\d{4}_[a-z0-9_]+$"),
    # ``<primitive_name>_primitive_v1`` — a primitive-specific default
    # the substrate honours (e.g. ``rolling_regression_primitive_v1``).
    re.compile(r"^[a-z0-9_]+_primitive_v1$"),
    # ``most_defensible_proxy_for_<concept>`` — documented proxy when
    # the ideal datum is unavailable (e.g. GC-repo proxy).
    re.compile(r"^most_defensible_proxy_for_[a-z0-9_]+$"),
)

# The vague set (PR12 / methodology_sources.md "Anti-patterns").  These
# are AUTO-REJECT even if a pattern would otherwise admit them — e.g.
# ``standard`` would never match a pattern, but ``bloomberg`` is listed
# here explicitly so a stray ``source: bloomberg`` (rather than the
# registered ``bloomberg_field_convention``) is rejected loudly.
_VAGUE_SOURCE_TAGS: frozenset[str] = frozenset(
    {"default", "standard", "convention", "bloomberg", "tbd", "fixme", "change_me"}
)

# Matches a ``### `tag` `` header line in the registry markdown.
_REGISTRY_HEADER_RE = re.compile(r"^###\s+`([a-z0-9_]+)`\s*$")


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
# METHODOLOGY-SOURCE-TAG REGISTRY (PR12 / OPR12 / P8)
# ============================================================================


class SourceTagRegistryError(ValueError):
    """Raised when the methodology-source-tag registry cannot be read.

    A missing or empty registry is a precondition failure, not a lint
    finding: without the registry the lint cannot validate any tag, so
    it fails loudly (CI exit 2) rather than silently passing everything.
    """


def load_registered_tags(registry_path: Path) -> Set[str]:
    """Parse the exact registered tag set from the registry markdown.

    Reads every ``### `tag` `` header from ``methodology_sources.md``.
    These are the *exact-spelling* registered tags; the open pattern
    families (``_REGISTERED_TAG_PATTERNS``) are honoured separately by
    ``is_tag_registered``.

    Raises ``SourceTagRegistryError`` if the file is missing or yields
    no tags (a typo'd path or a gutted registry must fail CI, not pass).
    """
    if not registry_path.is_file():
        raise SourceTagRegistryError(
            f"methodology-source-tag registry not found: {registry_path}"
        )
    tags: Set[str] = set()
    for line in registry_path.read_text().splitlines():
        m = _REGISTRY_HEADER_RE.match(line)
        if m:
            tags.add(m.group(1))
    if not tags:
        raise SourceTagRegistryError(
            f"methodology-source-tag registry {registry_path} contained no "
            "``### `tag` `` headers — refusing to validate against an empty "
            "registry."
        )
    return tags


def is_tag_registered(tag: str, registered_exact: Set[str]) -> bool:
    """True iff ``tag`` is a registered methodology-source tag.

    Registration = (a) NOT in the vague auto-reject set, AND (b) either
    an exact registry header OR a match of a registered open-family
    pattern.  The vague check wins: a tag in ``_VAGUE_SOURCE_TAGS`` is
    rejected even if a pattern would otherwise admit it.
    """
    if tag in _VAGUE_SOURCE_TAGS:
        return False
    if tag in registered_exact:
        return True
    return any(pat.match(tag) for pat in _REGISTERED_TAG_PATTERNS)


@dataclass(frozen=True)
class SourceTagIssue:
    """One unregistered/vague methodology-source-tag finding."""

    tag: str
    kind: str  # "unregistered" | "vague"
    config_path: Path
    component: str  # tool/operator name
    convention_name: str

    def format_for_human(self) -> str:
        if self.kind == "vague":
            detail = (
                f"vague auto-reject tag {self.tag!r} (PR12 anti-pattern; "
                "name what makes the default standard, or use "
                "team_judgment_pending_review)"
            )
        else:
            detail = (
                f"unregistered methodology-source tag {self.tag!r} — add a "
                "one-line entry to docs/architecture/methodology_sources.md "
                "(or match a registered pattern family) before using it"
            )
        return (
            f"[UNREGISTERED-SOURCE] {detail}\n"
            f"  - component={self.component!r}  "
            f"convention={self.convention_name!r}  ({self.config_path})"
        )


def check_source_tags(
    tool_config_paths: Iterable[Path],
    operator_config_paths: Iterable[Path],
    registry_path: Path,
) -> List[SourceTagIssue]:
    """Validate EVERY ``source`` tag against the closed registry (PR12).

    Walks every tool ``Convention.source`` and every operator
    ``OperatorDefault.source``; each must be a registered tag per
    ``is_tag_registered`` (exact header OR registered open-family
    pattern) and must NOT be in the vague auto-reject set.  Returns the
    list of violations — empty means clean.  This is the PR12 / OPR12
    CI authority: a non-empty result drives a non-zero exit in
    ``main``.

    Loads configs through the product loaders so the same validated
    ``source`` strings the runtime sees are the ones checked here.
    Config-load failures propagate (``ToolConfigError`` /
    ``OperatorConfigError``) — a broken YAML is a precondition, not a
    tag finding.
    """
    registered_exact = load_registered_tags(registry_path)
    issues: List[SourceTagIssue] = []

    for path in tool_config_paths:
        cfg = load_tool_config(path)
        for conv_name, conv in cfg.conventions.items():
            tag = conv.source
            if is_tag_registered(tag, registered_exact):
                continue
            kind = "vague" if tag in _VAGUE_SOURCE_TAGS else "unregistered"
            issues.append(
                SourceTagIssue(
                    tag=tag,
                    kind=kind,
                    config_path=path,
                    component=cfg.tool.name,
                    convention_name=conv_name,
                )
            )

    for path in operator_config_paths:
        op_cfg = load_operator_config(path)
        for default_name, default in op_cfg.defaults.items():
            tag = default.source
            if is_tag_registered(tag, registered_exact):
                continue
            kind = "vague" if tag in _VAGUE_SOURCE_TAGS else "unregistered"
            issues.append(
                SourceTagIssue(
                    tag=tag,
                    kind=kind,
                    config_path=path,
                    component=op_cfg.operator.name,
                    convention_name=default_name,
                )
            )

    issues.sort(key=lambda i: (str(i.config_path), i.convention_name))
    return issues


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
    parser.add_argument(
        "--registry",
        type=Path,
        default=None,
        help=(
            "Path to the methodology-source-tag registry markdown "
            f"(default: <root>/{_METHODOLOGY_SOURCE_REGISTRY})."
        ),
    )
    args = parser.parse_args(argv)

    registry_path = (
        args.registry
        if args.registry is not None
        else args.root / _METHODOLOGY_SOURCE_REGISTRY
    )

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

    # Methodology-source-tag registry membership (PR12 / OPR12 / P8).
    # Every Convention.source / OperatorDefault.source must reference a
    # registered tag; the vague set is auto-rejected.  This is the named
    # PR12 CI authority — a violation here fails the build.
    try:
        tag_issues = check_source_tags(paths, op_paths, registry_path)
    except SourceTagRegistryError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    except (ToolConfigError, OperatorConfigError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    if not issues and not tag_issues:
        if not args.quiet:
            print(
                "OK — no convention drift detected; all methodology-source "
                f"tags registered (registry: {registry_path})."
            )
        return 0

    if tag_issues:
        print(
            f"\n{len(tag_issues)} unregistered/vague methodology-source "
            f"tag(s) found (PR12):\n",
            file=sys.stderr,
        )
        for tag_issue in tag_issues:
            print(tag_issue.format_for_human(), file=sys.stderr)
            print("", file=sys.stderr)

    if issues:
        print(f"\n{len(issues)} consistency issue(s) found:\n", file=sys.stderr)
        for issue in issues:
            print(issue.format_for_human(), file=sys.stderr)
            print("", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
