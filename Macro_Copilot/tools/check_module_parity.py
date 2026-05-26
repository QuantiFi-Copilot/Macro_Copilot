#!/usr/bin/env python3
# =============================================================================
# tools/check_module_parity.py — Stage 2 cross-side parity check.
# -----------------------------------------------------------------------------
# Stage 2 — Build the src/modules/ infrastructure.  This script asserts
# that the set of frontend module folders matches the union of:
#
#   * Backend ``rates_agent.workflows._PRIMITIVE_SPECS`` keys
#     (every runnable primitive on the workflow bridge).
#   * Backend ``rates_agent.workflows.WORKFLOW_INCOMPATIBLE_TOOLS`` keys
#     (every shipped-but-not-bridge-compatible primitive).
#   * Explicit ``deferred`` reservations in the frontend module specs
#     (names reserved for planned backend features — Phase 2.5
#     ``cross_sectional_screen``, TD #35 ``attribution_decomposition``,
#     etc.).
#
# Stage 2 reality
# ---------------
# ``src/modules/{primitives,workflows}/`` is EMPTY today.  The Stage 1
# hand-authored entries in ``src/lib/toolNames.ts`` carry every
# backend tool's frontend routing for now.  As Stage 4a/4b/4c migrate
# primitives into module folders, the hand-authored entries shrink
# and the module folders multiply; this parity script tracks the
# crossover.
#
# The temporary whitelist
# -----------------------
# Stage 4a/4b/4c is incremental: at any point during the migration,
# some primitives live in module folders and others still ship via
# the Stage 1 hand-authored registry entries.  The whitelist captures
# the latter set so the parity check tolerates the in-flight state
# without failing CI prematurely.
#
# The whitelist SHRINKS as modules land:
#   * Stage 2 (today): whitelist = every backend primitive +
#     every WORKFLOW_INCOMPATIBLE_TOOLS entry (because no module
#     folders exist yet).
#   * Stage 4a end: whitelist removes the 17 sovereign + OIS primitives
#     that migrated into module folders.
#   * Stage 4b end: whitelist removes the 5 rich-model primitives.
#   * Stage 4c end: whitelist removes the 12 futures primitives.
#   * Stage 5 / 6 / ...: whitelist removes each new-feature primitive
#     as its module lands.
#   * Stage N: whitelist is empty; parity check binds in full.
#
# CLI
# ---
#   python3 tools/check_module_parity.py [--strict]
#
#   --strict  → fail loudly if any backend tool is missing from the
#               frontend (default behaviour today since whitelist
#               covers them all)
#
# Exit codes:
#   0 → parity holds
#   1 → parity drift detected
#   2 → environment problem (missing files, import error)
# =============================================================================

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable


# -----------------------------------------------------------------------------
# Paths.
# -----------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_ROOT = REPO_ROOT / "UI" / "macro-copilot-dashboard-polished"
MODULES_ROOT = FRONTEND_ROOT / "src" / "modules"
LOADER_BARREL = MODULES_ROOT / "index.ts"
PRIMITIVES_DIR = MODULES_ROOT / "primitives"
WORKFLOWS_DIR = MODULES_ROOT / "workflows"


# -----------------------------------------------------------------------------
# Backend ground-truth readers.
# -----------------------------------------------------------------------------


def read_backend_primitive_specs() -> set[str]:
    """Import the backend's ``known_rates_primitives`` set."""
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from rates_agent.workflows import known_rates_primitives  # type: ignore
    except ImportError as exc:
        print(
            f"error: could not import rates_agent.workflows.known_rates_primitives: {exc}",
            file=sys.stderr,
        )
        sys.exit(2)
    return set(known_rates_primitives())


def read_backend_workflow_incompatible() -> set[str]:
    """Import the backend's ``WORKFLOW_INCOMPATIBLE_TOOLS`` mapping."""
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from rates_agent.workflows import WORKFLOW_INCOMPATIBLE_TOOLS  # type: ignore
    except ImportError as exc:
        print(
            f"error: could not import rates_agent.workflows.WORKFLOW_INCOMPATIBLE_TOOLS: {exc}",
            file=sys.stderr,
        )
        sys.exit(2)
    return set(WORKFLOW_INCOMPATIBLE_TOOLS.keys())


def read_backend_manifest_only() -> set[str]:
    """Import ``orchestrator.events._MANIFEST_ONLY_BUILD_TOOLS`` — the
    closed set of tools that have a backend implementation (typed-detail
    endpoint or workflow-incompatible) but aren't in ``_PRIMITIVE_SPECS``.
    Stage 3 modules legitimately exist for these tools (e.g.
    ``calculate_butterfly_tool``, ``scan_extremes_tool``,
    ``scan_ois_extremes_tool``) so the parity check counts them as
    real backend tools, not as frontend-orphans."""
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from orchestrator.events import _MANIFEST_ONLY_BUILD_TOOLS  # type: ignore
    except ImportError as exc:
        print(
            f"error: could not import orchestrator.events._MANIFEST_ONLY_BUILD_TOOLS: {exc}",
            file=sys.stderr,
        )
        sys.exit(2)
    return set(_MANIFEST_ONLY_BUILD_TOOLS)


# -----------------------------------------------------------------------------
# Frontend ground-truth readers.
# -----------------------------------------------------------------------------


def read_frontend_primitive_modules() -> set[str]:
    """List every folder under ``src/modules/primitives/`` that ships
    a ``module.ts`` (the "this module is live" marker)."""
    if not PRIMITIVES_DIR.exists():
        return set()
    out: set[str] = set()
    for entry in PRIMITIVES_DIR.iterdir():
        if not entry.is_dir():
            continue
        if not (entry / "module.ts").exists():
            continue
        out.add(entry.name)
    return out


def read_frontend_workflow_modules() -> set[str]:
    """List every folder under ``src/modules/workflows/`` with a
    ``module.ts``."""
    if not WORKFLOWS_DIR.exists():
        return set()
    out: set[str] = set()
    for entry in WORKFLOWS_DIR.iterdir():
        if not entry.is_dir():
            continue
        if not (entry / "module.ts").exists():
            continue
        out.add(entry.name)
    return out


def _strip_comments(text: str) -> str:
    """Strip single-line ``//`` and block ``/* */`` comments so the
    regex doesn't false-match example import lines inside doc blocks.
    Naive but sufficient: we only need to suppress comments inside
    src/modules/index.ts, which is hand-curated TypeScript with no
    URLs containing ``//``."""
    # Block comments first (non-greedy across newlines).
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    # Then line comments (anything from `//` to end of line).  This is
    # safe here because TypeScript string literals in index.ts don't
    # contain `//` substrings (the file imports paths, not URLs).
    text = re.sub(r"//[^\n]*", "", text)
    return text


def read_loader_imports() -> set[str]:
    """Parse ``src/modules/index.ts`` and extract the import names.
    Catches the case where a folder exists but isn't wired into the
    loader (FM12 violation visible from the Python side)."""
    if not LOADER_BARREL.exists():
        return set()
    text = LOADER_BARREL.read_text(encoding="utf-8")
    # Strip JS/TS comments BEFORE matching — the loader barrel contains
    # example import lines inside doc comments that the regex would
    # otherwise pick up as real imports.
    text = _strip_comments(text)
    # Match: ``import { MODULE as <NAME> } from './primitives/<FOLDER>/module';``
    # (Stage 3 settled on the explicit ``/module`` path because the
    # TypeScript Bundler resolver doesn't auto-resolve a bare folder
    # path to ``module.ts``; only ``index.ts``.)  The optional ``/module``
    # tail keeps the regex compatible with both shapes for safety.
    pattern = re.compile(
        r"import\s*\{\s*MODULE\s+as\s+(\w+)\s*\}\s*from\s*['\"]\.\/(?:primitives|workflows)\/([\w\-]+)(?:\/module)?['\"]"
    )
    out: set[str] = set()
    for match in pattern.finditer(text):
        name = match.group(1)
        folder = match.group(2)
        # The import alias SHOULD match the folder name (per FM12) but
        # we record the folder name as the source of truth — the
        # per-module round-trip test catches alias mismatches.
        out.add(folder)
    return out


# -----------------------------------------------------------------------------
# Whitelist — primitives still served by the Stage 1 hand-authored
# registries until their modules land in Stage 4a/4b/4c.
# -----------------------------------------------------------------------------
#
# Until Stage 4a/4b/4c migrate these, the parity check must NOT flag
# them as "missing from frontend modules" — they're still surfaced,
# just via the legacy page-folder UI + hand-authored registry entries
# in src/lib/toolNames.ts (Stage 1).
#
# Maintenance:
#   * Stage 4a PR removes the 17 sovereign + OIS entries.
#   * Stage 4b PR removes the 5 rich-model entries.
#   * Stage 4c PR removes the 12 futures entries.
#   * Stages 5/6/... remove one entry each.
#   * Stage N removes the whitelist entirely.
#
# Every backend-shipped tool is here today (Stage 2).

STAGE_4_REFACTOR_WHITELIST: set[str] = {
    # ----------------------------------------------------------------
    # Stage 4e — whitelist shrunk to Stage 5+ targets ONLY.  Every
    # primitive that was migrated by Stage 4a/4b/4c/4d (sovereign +
    # OIS typed views, rich models, futures, monitor widgets, manifest-
    # typed-view tools) is no longer whitelisted; if any of them
    # disappear from the frontend module set, the parity check binds
    # and the missing module is reported as drift.  This matches the
    # roadmap's "whitelist SHRINKS as modules land" promise.
    # ----------------------------------------------------------------
    # Stage 1 net-new runnable primitives (18) — Stage 5+ targets.
    # These remain whitelisted because their module folders exist
    # (Stage 3 scaffolded them) but no Stage 4 PR migrated their
    # surfaces.  Stage 5+ ships per-tool surfaces + removes each
    # entry as it lands.
    "calculate_otr_ofr_spread_tool",
    "calculate_cpi_surprise_tool",
    "calculate_nfp_surprise_tool",
    "get_real_yield_level_tool",
    "calculate_breakeven_inflation_simple_tool",
    "calculate_forward_breakeven_simple_tool",
    "calculate_breakeven_curve_spread_tool",
    "calculate_cross_country_breakeven_spread_simple_tool",
    "calculate_real_yield_curve_spread_tool",
    "calculate_cross_country_real_yield_spread_simple_tool",
    "calculate_real_yield_butterfly_tool",
    "calculate_breakeven_butterfly_tool",
    "calculate_inflation_swap_rate_level_tool",
    "calculate_inflation_swap_curve_spread_tool",
    "calculate_inflation_swap_forward_tool",
    "calculate_cross_market_inflation_swap_spread_tool",
    "calculate_swap_breakeven_basis_simple_tool",
    "calculate_inflation_swap_butterfly_tool",
    # Workflow-incompatible (2) — Stage 5+ target (Stage 15/16 in
    # roadmap).
    "get_otr_history_tool",
    "calculate_wirp_meeting_pricing_tool",
}


# -----------------------------------------------------------------------------
# Reporting.
# -----------------------------------------------------------------------------


def print_problems(label: str, items: Iterable[str]) -> None:
    sorted_items = sorted(items)
    print(f"\n  {label} ({len(sorted_items)}):")
    for item in sorted_items:
        print(f"    {item}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check backend ↔ frontend module parity.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail on any whitelisted-but-still-not-migrated tools (post-Stage-N enforcement).",
    )
    args = parser.parse_args()

    # ---- Read ground truth ----------------------------------------
    backend_runnable = read_backend_primitive_specs()
    backend_workflow_incompat = read_backend_workflow_incompatible()
    backend_manifest_only = read_backend_manifest_only()
    # Union of all three — frontend modules may legitimately exist
    # for any tool the backend recognises through ANY of these paths.
    backend_primitives = (
        backend_runnable | backend_workflow_incompat | backend_manifest_only
    )

    frontend_primitive_folders = read_frontend_primitive_modules()
    loader_imports = read_loader_imports()

    # ---- Folder ↔ loader parity (FM12) ----------------------------
    folder_orphans = frontend_primitive_folders - loader_imports
    loader_orphans = loader_imports - frontend_primitive_folders

    # ---- Backend ↔ frontend parity --------------------------------
    backend_only = backend_primitives - frontend_primitive_folders
    frontend_only = frontend_primitive_folders - backend_primitives

    # During Stage 2–4 the whitelist absorbs the unmigrated portion.
    backend_only_after_whitelist = backend_only - STAGE_4_REFACTOR_WHITELIST

    # ---- Reporting ------------------------------------------------
    print("=" * 72)
    print("Module parity check (Stage 2)")
    print("=" * 72)
    print(f"  backend runnable primitives:        {len(backend_runnable):3d}")
    print(f"  backend workflow_incompatible:      {len(backend_workflow_incompat):3d}")
    print(f"  backend manifest_only_build:        {len(backend_manifest_only):3d}")
    print(f"  backend union (primitives total):   {len(backend_primitives):3d}")
    print(f"  frontend primitive module folders:  {len(frontend_primitive_folders):3d}")
    print(f"  Stage-4 refactor whitelist:        {len(STAGE_4_REFACTOR_WHITELIST):3d}")
    print(
        f"  backend tools not yet in module folders (after whitelist): "
        f"{len(backend_only_after_whitelist):3d}"
    )

    problems = 0

    if folder_orphans:
        print_problems(
            "FM12 violation — module folders missing from src/modules/index.ts",
            folder_orphans,
        )
        problems += 1

    if loader_orphans:
        print_problems(
            "FM12 violation — loader imports with no matching folder",
            loader_orphans,
        )
        problems += 1

    if frontend_only:
        print_problems(
            "FP6 violation — frontend modules with no matching backend tool "
            "(neither in _PRIMITIVE_SPECS nor WORKFLOW_INCOMPATIBLE_TOOLS)",
            frontend_only,
        )
        problems += 1

    if backend_only_after_whitelist:
        print_problems(
            "FP6 violation — backend tools with no matching frontend module "
            "(not in the Stage-4 refactor whitelist)",
            backend_only_after_whitelist,
        )
        problems += 1

    if args.strict and backend_only:
        # Strict mode (Stage N+): any whitelisted tool that hasn't
        # migrated is a hard failure.
        print_problems(
            "Strict-mode failure — whitelisted tool missing from frontend",
            backend_only & STAGE_4_REFACTOR_WHITELIST,
        )
        problems += 1

    # Stage 4g: strict mode binds whenever the whitelist is non-empty,
    # not just when `backend_only` is non-empty.  Pre-Stage-4g the
    # only fail path was "a whitelisted tool went missing on the
    # frontend"; since Stage 3 scaffolded all 58 module folders,
    # ``backend_only`` is always empty in practice and the strict
    # gate never tripped — making it indistinguishable from the
    # normal gate.  Stage N's goal is whitelist-empty (zero hand-
    # authored, fully derived from modules); strict mode enforces
    # that progress now.
    if args.strict and STAGE_4_REFACTOR_WHITELIST:
        print_problems(
            "Strict-mode failure — Stage 4 refactor whitelist is non-empty "
            "(Stage N goal: whitelist == empty.  Each Stage 5+ PR removes "
            "its tool from the whitelist as the per-tool surfaces land.)",
            STAGE_4_REFACTOR_WHITELIST,
        )
        problems += 1

    if problems == 0:
        print("\n  OK — parity invariants hold.")
        return 0
    print(f"\n  FAIL — {problems} parity problem(s) detected.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
