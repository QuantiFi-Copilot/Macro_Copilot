#!/usr/bin/env python3
# =============================================================================
# tools/scaffold_stage3_modules.py — Stage 3 module-folder generator.
# -----------------------------------------------------------------------------
# Generates the 58 per-primitive module folders under
# src/modules/primitives/ that Stage 3 introduces.  Each folder gets
# the minimum-viable trio:
#
#   <tool_name>/
#     THESIS.md            (skeleton answering the five required questions)
#     module.ts            (minimal spec: toolName + runtime-status tier +
#                           display metadata + unsupportedReason where needed)
#     __tests__/module.spec.ts  (assertStandardModuleInvariants round-trip)
#
# Stage 3 modules are INTENTIONALLY MINIMAL: only the runtime-status
# tier is claimed, no capability tiers, no surfaces.*  refs.  Surface
# code stays in its legacy page-folder locations; Stage 4a/4b/4c moves
# it INTO the module folders + populates the capability tiers + the
# surfaces field as part of the move.  This keeps Stage 3 a pure
# scaffolding PR (no behaviour change) and keeps Stage 4 PRs focused
# on one move at a time.
#
# Why a script rather than hand-writing 174 files
# -----------------------------------------------
# Per-module variance is purely DATA (toolName, tier, displayName,
# category, sub_agent, one_liner, unsupportedReason).  The file
# templates are identical.  A generator captures the data once and
# emits files mechanically — zero transcription errors, fully
# reproducible.  The script is preserved post-Stage-3 as documentation
# of the original module-folder shapes; future regenerations should
# never overwrite work that landed in Stage 4+.
#
# Source of truth
# ---------------
# Reads:
#   * rates_agent.workflows._PRIMITIVE_SPECS           (52 runnable)
#   * rates_agent.workflows.WORKFLOW_INCOMPATIBLE_TOOLS (3 entries)
#   * manifesto/03_tool_manifest/rates_agent/*.yml     (display labels)
#   * UI/.../src/lib/toolNames.ts                       (existing
#     UNSUPPORTED_KNOWN_REASONS text for the 3 already-classified
#     tools, parsed via regex so we don't duplicate the canonical
#     reason copy)
#
# Backwards compatibility
# -----------------------
# The script does NOT modify the existing toolNames.ts / modelRegistry.ts
# hand-authored registries.  That hybrid-derivation wiring is a
# separate edit driven by the Stage 3 PR (see the PR body), not
# this script.
# =============================================================================

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


# -----------------------------------------------------------------------------
# Paths.
# -----------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_ROOT = REPO_ROOT / "UI" / "macro-copilot-dashboard-polished"
MODULES_PRIMITIVES_DIR = FRONTEND_ROOT / "src" / "modules" / "primitives"
LOADER_BARREL = FRONTEND_ROOT / "src" / "modules" / "index.ts"
MANIFEST_DIR = REPO_ROOT / "manifesto" / "03_tool_manifest" / "rates_agent"
TOOLNAMES_TS = FRONTEND_ROOT / "src" / "lib" / "toolNames.ts"


# -----------------------------------------------------------------------------
# Alias map — manifest shorthand → backend-canonical.
# -----------------------------------------------------------------------------
# Mirrors KNOWN_TOOL_ALIASES in src/lib/toolNames.ts.  Used in REVERSE
# below (canonical → manifest shorthand) when looking up manifest
# metadata for backend-canonical names whose manifest entry uses the
# shorthand form.

CANONICAL_TO_MANIFEST_SHORTHAND: dict[str, str] = {
    "calculate_rolling_regression_tool": "rolling_regression_tool",
    "calculate_beta_adjusted_spread_tool": "beta_adjusted_spread_tool",
    "calculate_half_life_tool": "half_life_tool",
    "calculate_pca_yield_curve_tool": "pca_yield_curve_tool",
    "calculate_yield_change_attribution_pca_tool": "yield_change_attribution_pca_tool",
    "calculate_zscore_custom_tool": "zscore_custom_tool",
    "get_ois_rate_level_tool": "calculate_ois_rate_level_tool",
}


# -----------------------------------------------------------------------------
# Reading backend ground truth.
# -----------------------------------------------------------------------------


def _ensure_backend_importable() -> None:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))


def read_backend_runnable() -> set[str]:
    _ensure_backend_importable()
    from rates_agent.workflows import known_rates_primitives  # type: ignore

    return set(known_rates_primitives())


def read_backend_workflow_incompatible() -> dict[str, str]:
    """Returns {tool_name → backend-supplied rationale}."""
    _ensure_backend_importable()
    from rates_agent.workflows import WORKFLOW_INCOMPATIBLE_TOOLS  # type: ignore

    return dict(WORKFLOW_INCOMPATIBLE_TOOLS)


def read_manifest_metadata() -> dict[str, dict[str, str]]:
    """Read every YAML in manifesto/03_tool_manifest/rates_agent/ and
    flatten into {tool_function → {name, sub_agent, category, one_liner}}.
    Keyed by manifest-emitted name (which may be the shorthand for
    aliased tools)."""
    out: dict[str, dict[str, str]] = {}
    for yml in sorted(MANIFEST_DIR.glob("*.yml")):
        data = yaml.safe_load(yml.read_text())
        for tool in data.get("tools", []):
            impl = tool.get("implementation", {})
            fn = impl.get("tool_function")
            if not fn:
                continue
            out[fn] = {
                "name": tool.get("name", fn),
                "sub_agent": tool.get("sub_agent", ""),
                "category": tool.get("category", ""),
                "one_liner": (tool.get("one_liner") or "").strip(),
                "status": tool.get("status", ""),
                "bucket": tool.get("bucket", ""),
            }
    return out


def manifest_for(tool_name: str, manifest: dict[str, dict[str, str]]) -> Optional[dict[str, str]]:
    """Resolve manifest metadata for a backend-canonical tool name,
    walking through the manifest-shorthand alias table if needed."""
    if tool_name in manifest:
        return manifest[tool_name]
    shorthand = CANONICAL_TO_MANIFEST_SHORTHAND.get(tool_name)
    if shorthand and shorthand in manifest:
        return manifest[shorthand]
    return None


# -----------------------------------------------------------------------------
# Existing UnsupportedKnownReason map — parse from toolNames.ts so the
# scaffolder doesn't duplicate canonical copy.
# -----------------------------------------------------------------------------


def read_existing_unsupported_reasons() -> dict[str, dict[str, str]]:
    """Crude parser of UNSUPPORTED_KNOWN_REASONS in toolNames.ts.
    Returns {tool_name → {label, reason, whatWorksNow}} for the three
    tools the Stage 1 file already classifies (scan_ois_extremes_tool,
    get_otr_history_tool, calculate_wirp_meeting_pricing_tool)."""
    text = TOOLNAMES_TS.read_text()
    block_match = re.search(
        r"export\s+const\s+UNSUPPORTED_KNOWN_REASONS:\s*Record<[^>]+>\s*=\s*\{(.*?)\n\};",
        text,
        re.DOTALL,
    )
    if not block_match:
        return {}
    body = block_match.group(1)
    out: dict[str, dict[str, str]] = {}
    # Entry shape:
    #   tool_name_here: {
    #     label: '...',
    #     reason: '...',
    #     whatWorksNow: '...',
    #   },
    entry_re = re.compile(
        r"(\w+):\s*\{\s*label:\s*'((?:[^'\\]|\\.)*)',\s*reason:\s*'((?:[^'\\]|\\.)*)',\s*whatWorksNow:\s*'((?:[^'\\]|\\.)*)',?\s*\},",
        re.DOTALL,
    )
    for m in entry_re.finditer(body):
        name = m.group(1)
        out[name] = {
            "label": m.group(2).replace("\\'", "'"),
            "reason": m.group(3).replace("\\'", "'"),
            "whatWorksNow": m.group(4).replace("\\'", "'"),
        }
    return out


# -----------------------------------------------------------------------------
# Per-tool catalogue.  One ModuleEntry per output folder.
# -----------------------------------------------------------------------------


@dataclass
class ModuleEntry:
    tool_name: str                              # backend canonical (= folder name)
    runtime_tier: str                           # generic_runnable | workflow_incompatible | paused
    display_name: str
    sub_agent: str
    category: str
    one_liner: str
    unsupported_reason: Optional[dict[str, str]] = None
    stage_4_target_capabilities: list[str] = field(default_factory=list)
    notes: str = ""                             # extra THESIS prose


def title_case(snake: str) -> str:
    """Fallback display-name compute when manifest doesn't supply one.
    Strips '_tool' suffix and title-cases the remainder."""
    name = snake[: -len("_tool")] if snake.endswith("_tool") else snake
    return " ".join(p.title() for p in name.split("_"))


def build_catalogue() -> list[ModuleEntry]:
    runnable = read_backend_runnable()
    wi = read_backend_workflow_incompatible()
    manifest = read_manifest_metadata()
    existing_reasons = read_existing_unsupported_reasons()

    catalogue: list[ModuleEntry] = []

    # ----- generic_runnable: 52 _PRIMITIVE_SPECS entries ------------
    for tool in sorted(runnable):
        m = manifest_for(tool, manifest)
        catalogue.append(
            ModuleEntry(
                tool_name=tool,
                runtime_tier="generic_runnable",
                display_name=(m or {}).get("name") or title_case(tool),
                sub_agent=(m or {}).get("sub_agent", "sovereign_bonds"),
                category=(m or {}).get("category", "snapshots"),
                one_liner=(m or {}).get("one_liner") or "(no manifest description)",
                unsupported_reason=None,
                stage_4_target_capabilities=stage_4_target_for_runnable(tool),
                notes=(
                    "Stage 3 ships this module with only the `generic_runnable` runtime tier.  "
                    "Capability tiers + surface refs land in the relevant Stage 4 PR (4a "
                    "sovereign + OIS, 4b rich-model, 4c futures); existing UI continues to "
                    "render via legacy page-folder code until then."
                ),
            )
        )

    # ----- workflow_incompatible: backend's strict 3 ----------------
    for tool, backend_reason in sorted(wi.items()):
        m = manifest_for(tool, manifest)
        reason = existing_reasons.get(tool) or {
            "label": (m or {}).get("name") or title_case(tool),
            "reason": (
                "Backend declares this tool in `WORKFLOW_INCOMPATIBLE_TOOLS`.  "
                "Backend rationale: " + " ".join(backend_reason.split())[:250]
            ),
            "whatWorksNow": (
                "Ask can run the tool via MCP; a bespoke Build typed-view "
                "may exist via the rates typed-detail endpoints."
            ),
        }
        catalogue.append(
            ModuleEntry(
                tool_name=tool,
                runtime_tier="workflow_incompatible",
                display_name=(m or {}).get("name") or title_case(tool),
                sub_agent=(m or {}).get("sub_agent", "sovereign_bonds"),
                category=(m or {}).get("category", "snapshots"),
                one_liner=(m or {}).get("one_liner") or "(no manifest description)",
                unsupported_reason=reason,
                stage_4_target_capabilities=stage_4_target_for_workflow_incompatible(tool),
            )
        )

    # ----- manifest-only with typed-detail surface (2) ---------------
    # calculate_butterfly_tool, scan_extremes_tool ship typed-detail
    # endpoints + typed views on the frontend.  Strictly per the
    # tier doctrine they're not `paused` (backend has implementation)
    # and not `workflow_incompatible` (not in backend's strict dict).
    # Closest fit: `workflow_incompatible` with a per-tool reason that
    # explains the typed-detail-only surface — the workflow BRIDGE
    # cannot dispatch them either way.
    for tool in ("calculate_butterfly_tool", "scan_extremes_tool"):
        m = manifest_for(tool, manifest)
        catalogue.append(
            ModuleEntry(
                tool_name=tool,
                runtime_tier="workflow_incompatible",
                display_name=(m or {}).get("name") or title_case(tool),
                sub_agent=(m or {}).get("sub_agent", "sovereign_bonds"),
                category=(m or {}).get("category", "snapshots"),
                one_liner=(m or {}).get("one_liner") or "(no manifest description)",
                unsupported_reason={
                    "label": (m or {}).get("name") or title_case(tool),
                    "reason": (
                        "Manifest-declared tool with a backend implementation behind a "
                        "typed-detail endpoint (`/api/v1/rates/detail/<kind>`).  Not in "
                        "`_PRIMITIVE_SPECS` so the workflow bridge cannot dispatch it; "
                        "the typed view in Build is the live surface."
                    ),
                    "whatWorksNow": (
                        "The typed view in Build renders this tool against its "
                        "typed-detail endpoint.  Ask handoff works too — the tool is "
                        "in `_MANIFEST_ONLY_BUILD_TOOLS` on the backend gate."
                    ),
                },
                stage_4_target_capabilities=["custom_build_surface"],
                notes=(
                    "Stage 3 tier choice: `workflow_incompatible`.  The backend's strict "
                    "`WORKFLOW_INCOMPATIBLE_TOOLS` dict does not include this tool, but "
                    "the effective semantics are the same — the workflow bridge cannot "
                    "dispatch it because it's not in `_PRIMITIVE_SPECS`.  The typed-detail "
                    "endpoint at `/api/v1/rates/detail/<kind>` provides the live surface, "
                    "and the typed view in `src/components/build/primitive/` routes it "
                    "via the existing `TOOL_TO_VIEW` map in `contextDecoder.ts`."
                ),
            )
        )

    # ----- paused: scan_ois_extremes_tool (truly no backend impl) ----
    tool = "scan_ois_extremes_tool"
    m = manifest_for(tool, manifest)
    reason = existing_reasons.get(tool) or {
        "label": (m or {}).get("name") or title_case(tool),
        "reason": "Declared in the manifest but the backend has no live route yet.",
        "whatWorksNow": "Ask cannot run it today; the sovereign-bond scanner is the closest equivalent.",
    }
    catalogue.append(
        ModuleEntry(
            tool_name=tool,
            runtime_tier="paused",
            display_name=(m or {}).get("name") or title_case(tool),
            sub_agent=(m or {}).get("sub_agent", "ois"),
            category=(m or {}).get("category", "scanners"),
            one_liner=(m or {}).get("one_liner") or "(no manifest description)",
            unsupported_reason=reason,
        )
    )

    return sorted(catalogue, key=lambda e: e.tool_name)


def stage_4_target_for_runnable(tool: str) -> list[str]:
    """Document the Stage-4 capability targets per primitive class.
    These flow into the THESIS Question 4 ("what would change the
    design?") so future contributors know the migration intent."""
    # Typed-view sovereign primitives → Stage 4a will claim
    # custom_build_surface + (in some cases) monitor_surface.
    typed_view_sov = {
        "calculate_curve_spread_tool": ["custom_build_surface", "monitor_surface"],
        "calculate_cross_market_spread_tool": ["custom_build_surface", "monitor_surface"],
        "get_yield_levels_tool": ["custom_build_surface", "monitor_surface"],
    }
    # Rich-model primitives → Stage 4b will claim
    # custom_build_surface + custom_preview_widget + richModel=true.
    rich_model = {
        "calculate_pca_yield_curve_tool",
        "calculate_rolling_regression_tool",
        "calculate_yield_change_attribution_pca_tool",
        "calculate_half_life_tool",
        "calculate_beta_adjusted_spread_tool",
    }
    if tool in typed_view_sov:
        return typed_view_sov[tool]
    if tool in rich_model:
        return ["custom_build_surface", "custom_preview_widget"]
    return []


def stage_4_target_for_workflow_incompatible(tool: str) -> list[str]:
    """classify_curve_move_tool has a typed regime view today; the
    other two (get_otr_history, wirp) currently surface the bare
    unsupported card."""
    if tool == "classify_curve_move_tool":
        return ["custom_build_surface", "monitor_surface"]
    return []


# -----------------------------------------------------------------------------
# Render templates.
# -----------------------------------------------------------------------------


def render_thesis(e: ModuleEntry) -> str:
    surfaces_today = "(none — Stage 3 ships the minimum-viable module)"
    capabilities_planned = (
        ", ".join(e.stage_4_target_capabilities)
        if e.stage_4_target_capabilities
        else "(none planned beyond the current runtime tier)"
    )
    backend_status = {
        "generic_runnable": (
            "Backend ships this primitive in `_PRIMITIVE_SPECS`; it runs via "
            "`POST /api/v1/tools/{name}/run` through the workflow bridge."
        ),
        "workflow_incompatible": (
            "Backend recognises this tool but the workflow bridge cannot dispatch its output."
        ),
        "paused": (
            "Backend does not yet have a live implementation for this tool."
        ),
        "deferred": (
            "Reserved name for a planned backend feature; not yet built on either side."
        ),
    }[e.runtime_tier]

    notes_block = (
        "---\n\n## Stage 3 implementation notes\n\n" + e.notes + "\n"
        if e.notes
        else ""
    )

    return f"""# THESIS — `{e.tool_name}`

> Stage 3 minimum-viable module — runtime tier only.  Surface code lives in its
> legacy page-folder location until the Stage 4 refactor moves it into this folder.

**Version:** v1 (Stage 3 scaffold)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `{e.tool_name}` ({e.runtime_tier})
**Tier set:** `[{e.runtime_tier}]`
**Backend sub-agent:** `{e.sub_agent}` · **Category:** `{e.category}`

---

## 1. What surfaces does this module ship?

- **`{e.runtime_tier}`** — runtime-status tier only (Stage 3 minimal scaffold).
  {backend_status}

(Capability tiers — `custom_build_surface`, `custom_preview_widget`, `monitor_surface`,
`ask_surface` — are NOT claimed in Stage 3.  The existing UI continues to render
via the legacy page-folder code in `src/components/build/`, `src/components/monitor/`,
etc.  Stage 4 PRs add capability tiers as surfaces move into this folder.)

## 2. What does the user read off each surface?

Stage 3 surfaces today: {surfaces_today}.

User-facing read at the runtime tier level: opening this tool from Library →
`Open in Build` decodes per `contextDecoder.ts` and lands on the matching shared
surface (generic builder / typed view / unsupported card) per the current routing
priority.  The decoder lookup is unchanged by Stage 3 — the module spec contributes
to the central registries via the hybrid derivation but the resulting set
membership is identical to the Stage 1 hand-authored entries.

## 3. Why these surfaces and not others?

Stage 3 is a pure scaffolding stage.  Per the migration roadmap
([06_roadmap/frontend_migration.md](../../../../../../docs_revamped/06_roadmap/frontend_migration.md)),
every primitive that will eventually have a module gets its folder
materialised in this stage so Stage 4 refactor PRs have a destination
to move legacy surface code INTO.  Claiming capability tiers + populating
`surfaces.*` happens in Stage 4 alongside the actual code move (no
half-states between stages).

## 4. What would change the design?

Planned Stage 4+ capabilities: {capabilities_planned}.

Concrete triggers:
- Backend output-shape changes → re-evaluate the runtime tier.
- New typed-detail endpoint shipped → claim `custom_build_surface` and
  point `MODULE.typedView` at the new kind.
- Per-tool persisted-artifact preview needed → claim `custom_preview_widget`
  and add `surfaces/PreviewWidget.tsx`.
- Tool surfaces frequently in daily desk read → claim `monitor_surface`
  and add `surfaces/MonitorWidget.tsx`.

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name equals backend `tool_name` exactly.
- **FM3** (surface-tier capability declaration) — exactly one runtime-status
  tier (`{e.runtime_tier}`); no capability tiers in Stage 3.
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value.
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — `__tests__/module.spec.ts` calls
  `assertStandardModuleInvariants`.
- **FM12** (loader presence) — module imported in `src/modules/index.ts`.
- Stage 3 of the migration roadmap (`docs_revamped/06_roadmap/frontend_migration.md`).

{notes_block}---

## Version log

| Version | Date | Change |
|---|---|---|
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
"""


def render_module_ts(e: ModuleEntry) -> str:
    # Build the spec object as TypeScript source.
    parts: list[str] = []
    parts.append("// ============================================================================")
    parts.append(f"// src/modules/primitives/{e.tool_name}/module.ts — Stage 3 scaffold.")
    parts.append("// ----------------------------------------------------------------------------")
    parts.append("// Minimum-viable module spec — declares only the runtime-status tier.")
    parts.append("// Stage 4 PRs add capability tiers (custom_build_surface, etc.) when the")
    parts.append("// matching surface code moves into this folder from its legacy location.")
    parts.append("// See THESIS.md for the design intent + planned Stage-4 capabilities.")
    parts.append("// ============================================================================")
    parts.append("")
    parts.append("import type { PrimitiveModuleSpec } from '../../types';")
    parts.append("")
    parts.append("export const MODULE: PrimitiveModuleSpec = {")
    parts.append(f"  toolName: '{e.tool_name}',")
    parts.append(f"  tiers: ['{e.runtime_tier}'],")
    parts.append(f"  displayName: {json_string(e.display_name)},")
    parts.append(f"  category: '{e.category}',")
    parts.append(f"  oneLineSummary: {json_string(e.one_liner)},")

    if e.unsupported_reason is not None:
        parts.append("  unsupportedReason: {")
        parts.append(f"    label: {json_string(e.unsupported_reason['label'])},")
        parts.append(f"    reason: {json_string(e.unsupported_reason['reason'])},")
        parts.append(f"    whatWorksNow: {json_string(e.unsupported_reason['whatWorksNow'])},")
        parts.append("  },")
    parts.append("};")
    parts.append("")
    return "\n".join(parts)


def render_module_spec(e: ModuleEntry) -> str:
    return f"""/* eslint-disable @typescript-eslint/no-explicit-any */
// ============================================================================
// src/modules/primitives/{e.tool_name}/__tests__/module.spec.ts
// ----------------------------------------------------------------------------
// Stage 3 per-module round-trip — calls assertStandardModuleInvariants
// from src/modules/__test-utils.ts.  Catches FM11 invariants 1-8 in
// one check.  Identical boilerplate across every module; per-module
// customisation belongs in additional ``check(...)`` blocks below.
// ============================================================================

import {{ assertStandardModuleInvariants }} from '../../../__test-utils';
import {{ MODULE }} from '../module';

const FOLDER = '{e.tool_name}';

interface NodeGlobal {{ process?: {{ cwd?: () => string }} }}

type Check = {{ label: string; fn: () => Promise<void> | void }};
const _checks: Check[] = [];

function check(label: string, fn: () => Promise<void> | void): void {{
  _checks.push({{ label, fn }});
}}

function cwd(): string {{
  const _g = globalThis as unknown as NodeGlobal;
  return _g.process?.cwd?.() ?? '.';
}}

check('module satisfies the standard invariants', async () => {{
  await assertStandardModuleInvariants(MODULE, {{
    folderName: FOLDER,
    moduleFolderPath: `${{cwd()}}/src/modules/primitives/${{FOLDER}}`,
  }});
}});

export async function runAllModuleSpecTests(): Promise<void> {{
  let passed = 0;
  let failed = 0;
  for (const {{ label, fn }} of _checks) {{
    try {{
      await fn();
      passed += 1;
    }} catch (err) {{
      failed += 1;
      // eslint-disable-next-line no-console
      console.error(`✗ ${{label}}\\n  ${{(err as Error).message}}`);
    }}
  }}
  // eslint-disable-next-line no-console
  console.log(`\\n${{FOLDER}}: ${{passed}} passed, ${{failed}} failed`);
  if (failed > 0) {{
    throw new Error(`${{failed}} ${{FOLDER}} check(s) failed`);
  }}
}}

const _meta = (import.meta as unknown) as {{ main?: boolean }};
if (_meta && _meta.main) {{
  runAllModuleSpecTests();
}}
"""


def render_loader_barrel(catalogue: list[ModuleEntry]) -> str:
    imports = []
    for e in catalogue:
        # Explicit ``/module`` path because TypeScript's Bundler-mode
        # module resolution doesn't auto-resolve ``./primitives/X`` to
        # ``./primitives/X/module.ts`` (only ``./primitives/X/index.ts``).
        # The module spec contract uses ``module.ts`` per FM7; the
        # import path carries the explicit file name to match.
        imports.append(
            f"import {{ MODULE as {e.tool_name} }} from './primitives/{e.tool_name}/module';"
        )

    entries = []
    for e in catalogue:
        entries.append(f"  {e.tool_name},")

    return f"""// ============================================================================
// src/modules/index.ts — central loader barrel.
// ----------------------------------------------------------------------------
// Stage 3 — populated with all {len(catalogue)} primitive module imports + entries
// in ``ALL_PRIMITIVE_MODULES``.  Each module's spec is a pure value
// (FM7); the loader has zero side effects.
//
// Adding a module = one import line + one array entry, alphabetised by
// tool_name.  The per-module round-trip test (``__tests__/module.spec.ts``
// in each module folder) + the loader-presence test
// (``src/lib/__tests__/loaderPresence.test.ts``) catch drift.
//
// Stage 3 reality
// ---------------
// All {len(catalogue)} modules ship with the MINIMUM-VIABLE shape — just the runtime-
// status tier.  Capability tiers + surface refs land in Stage 4a/4b/4c
// as the legacy surface code moves into each module folder.  The
// hybrid-derivation wiring in src/lib/toolNames.ts unions module-
// derived sets with the Stage 1 hand-authored entries; today the
// derived sets contribute the same tool names already in the hand-
// authored entries, so the resulting central registries are
// behaviour-identical to Stage 1.
// ============================================================================

import type {{
  PrimitiveModuleSpec,
  WorkflowModuleSpec,
}} from './types';

// ----------------------------------------------------------------------------
// Primitive module imports — alphabetised by tool_name.
// ----------------------------------------------------------------------------

{chr(10).join(imports)}

// ----------------------------------------------------------------------------
// (no workflow module imports yet — Stage 7+ adds them here)
// ----------------------------------------------------------------------------

// ----------------------------------------------------------------------------
// Public exports — the derived registries read from these arrays.
// ----------------------------------------------------------------------------

/** Every primitive module the loader knows about, in stable
 *  alphabetical order.  Pure data — derived sets in
 *  ``src/lib/toolNames.ts`` union these spec-derived contributions
 *  with the Stage 1 hand-authored entries through the migration. */
export const ALL_PRIMITIVE_MODULES: ReadonlyArray<PrimitiveModuleSpec> = [
{chr(10).join(entries)}
];

/** Every workflow module the loader knows about, in stable
 *  alphabetical order.  Stage 7+ populates this. */
export const ALL_WORKFLOW_MODULES: ReadonlyArray<WorkflowModuleSpec> = [
  // (no entries yet — Stage 7+ adds workflow modules here)
];

// ----------------------------------------------------------------------------
// Lookup helpers.
// ----------------------------------------------------------------------------

/** Resolve a primitive module by its backend-canonical ``tool_name``.
 *  Returns ``undefined`` when no module declares that tool.  Callers
 *  MUST normalise manifest shorthand BEFORE calling — use
 *  ``normalizeToolName`` from ``@/lib/toolNames``. */
export function getPrimitiveModule(
  toolName: string,
): PrimitiveModuleSpec | undefined {{
  return ALL_PRIMITIVE_MODULES.find((m) => m.toolName === toolName);
}}

/** Resolve a workflow module by its backend-canonical ``template_id``. */
export function getWorkflowModule(
  templateId: string,
): WorkflowModuleSpec | undefined {{
  return ALL_WORKFLOW_MODULES.find((m) => m.templateId === templateId);
}}

// ----------------------------------------------------------------------------
// Type re-exports.
// ----------------------------------------------------------------------------

export type {{
  PrimitiveModuleSpec,
  WorkflowModuleSpec,
  SurfaceTier,
  RuntimeStatusTier,
  CapabilityTier,
  ModuleSurfaceBaseProps,
  BuildSurfaceProps,
  MonitorWidgetProps,
  AskCardProps,
  PreviewWidgetProps,
  UnsupportedKnownReason,
}} from './types';
export {{
  ALL_SURFACE_TIERS,
  RUNTIME_STATUS_TIERS,
  CAPABILITY_TIERS,
  isRuntimeStatusTier,
  isCapabilityTier,
}} from './types';
"""


# -----------------------------------------------------------------------------
# Helpers.
# -----------------------------------------------------------------------------


def json_string(s: str) -> str:
    """Render a Python string as a TypeScript single-quoted string,
    escaping appropriately.  TS string literals follow JS rules; we
    escape backslash, single quote, and newline."""
    s = s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", " ").strip()
    return f"'{s}'"


# -----------------------------------------------------------------------------
# Main.
# -----------------------------------------------------------------------------


def main() -> int:
    catalogue = build_catalogue()

    print(f"Generating {len(catalogue)} module folders under {MODULES_PRIMITIVES_DIR}")

    MODULES_PRIMITIVES_DIR.mkdir(parents=True, exist_ok=True)

    written = 0
    for e in catalogue:
        folder = MODULES_PRIMITIVES_DIR / e.tool_name
        folder.mkdir(exist_ok=True)
        (folder / "__tests__").mkdir(exist_ok=True)
        (folder / "THESIS.md").write_text(render_thesis(e))
        (folder / "module.ts").write_text(render_module_ts(e))
        (folder / "__tests__" / "module.spec.ts").write_text(render_module_spec(e))
        written += 3

    print(f"Wrote {written} files across {len(catalogue)} folders.")

    # Loader barrel.
    LOADER_BARREL.write_text(render_loader_barrel(catalogue))
    print(f"Rewrote {LOADER_BARREL.relative_to(REPO_ROOT)} with {len(catalogue)} imports.")

    # Summary by tier.
    by_tier: dict[str, int] = {}
    for e in catalogue:
        by_tier[e.runtime_tier] = by_tier.get(e.runtime_tier, 0) + 1
    print("\nTier breakdown:")
    for tier, count in sorted(by_tier.items()):
        print(f"  {tier:30s}  {count:3d}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
