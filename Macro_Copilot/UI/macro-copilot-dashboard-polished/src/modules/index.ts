// ============================================================================
// src/modules/index.ts — central loader barrel (Stage 2).
// ----------------------------------------------------------------------------
// The single point that imports every module's pure-spec ``MODULE``
// export and exposes them as two ordered arrays:
//
//   - ``ALL_PRIMITIVE_MODULES``  — every primitive module's spec.
//   - ``ALL_WORKFLOW_MODULES``   — every workflow-template module's spec.
//
// Stage 2 lands the barrel with both arrays EMPTY.  No module folders
// exist under ``src/modules/primitives/`` or ``src/modules/workflows/``
// today; those land per the Stage 3+ schedule in
// docs_revamped/06_roadmap/frontend_migration.md.
//
// The lookup helpers (``getPrimitiveModule`` / ``getWorkflowModule``)
// are exported so future consumers can resolve a module by its
// backend-canonical identifier without touching the array literal.
//
// Stage-2 contract (FP4 / FP5 / FM12)
// -----------------------------------
// - PURE values only.  No mutation, no side-effect imports, no
//   top-level function calls beyond the spec collection itself.
// - One import per module, ALPHABETISED by tool_name / template_id
//   (the loader-presence test asserts this in Stage 3+).
// - Adding a module = one import line + one array entry.  The
//   per-module round-trip test (FM11) catches drift between the
//   spec, the folder, and the loader.
//
// Backwards compatibility
// -----------------------
// Nothing else in the running UI imports from this barrel yet.  The
// existing central registries in src/lib/toolNames.ts and
// src/lib/modelRegistry.ts continue to be hand-authored through
// Stage 4a/4b/4c.  When those stages migrate primitives into module
// folders, the registries gradually derive from this barrel; Stage N
// flips the "no hand-authored registries" CI gate to fully enforce.
// ============================================================================

import type {
  PrimitiveModuleSpec,
  WorkflowModuleSpec,
} from './types';

// ----------------------------------------------------------------------------
// Module imports (one per module — alphabetised by tool_name / template_id).
// ----------------------------------------------------------------------------
//
// STAGE 2 — both lists are intentionally empty.  Modules land per the
// Stage 3+ schedule.  The empty case is the most important shape to
// get right: the loader-presence test asserts that ``find src/modules/{primitives,workflows}
// -name module.ts`` returns the same set of tool_names / template_ids
// as the import block below.  With zero entries on each side, the
// invariant is trivially satisfied.
//
// When Stage 3 begins, each new module folder adds a line like:
//
//   import { MODULE as calculate_cpi_surprise_tool } from './primitives/calculate_cpi_surprise_tool';
//
// — and a matching entry in the ``ALL_PRIMITIVE_MODULES`` array below.
// The per-module round-trip test (Stage 3+) verifies the import name
// matches the folder name and the spec's ``toolName``.

// (no primitive module imports yet — Stage 3+ adds them here)

// (no workflow module imports yet — Stage 7+ adds them here)

// ----------------------------------------------------------------------------
// Public exports — the derived registries read from these arrays.
// ----------------------------------------------------------------------------

/** Every primitive module the loader knows about, in stable
 *  alphabetical order.  Pure data — derived sets in
 *  ``src/lib/toolNames.ts`` (target-state Stage N+) will compute
 *  ``KNOWN_BACKEND_TOOLS`` / ``RUNNABLE_PRIMITIVE_TOOLS`` /
 *  ``WORKFLOW_INCOMPATIBLE_TOOLS`` / ``UNSUPPORTED_KNOWN_TOOLS`` /
 *  ``UNSUPPORTED_KNOWN_REASONS`` from this array. */
export const ALL_PRIMITIVE_MODULES: ReadonlyArray<PrimitiveModuleSpec> = [
  // (no entries yet — Stage 3+ adds modules here)
];

/** Every workflow module the loader knows about, in stable
 *  alphabetical order.  Pure data — derived sets
 *  ``KNOWN_WORKFLOWS`` / ``PAUSED_WORKFLOWS`` will compute from
 *  this array at Stage N+. */
export const ALL_WORKFLOW_MODULES: ReadonlyArray<WorkflowModuleSpec> = [
  // (no entries yet — Stage 7+ adds modules here)
];

// ----------------------------------------------------------------------------
// Lookup helpers.
// ----------------------------------------------------------------------------
//
// O(n) linear searches over the module arrays.  With O(100) modules
// at steady state this is well within the noise floor of any caller.
// Memoising into a Map would add complexity for no measurable benefit
// (and would require care around hot-reload during development).

/** Resolve a primitive module by its backend-canonical ``tool_name``.
 *  Returns ``undefined`` when no module declares that tool.
 *
 *  Stage 2: always returns ``undefined`` (arrays are empty).
 *  Stage 3+: returns the matching module spec.
 *
 *  Callers MUST normalise manifest shorthand BEFORE calling — use
 *  ``normalizeToolName`` from ``@/lib/toolNames``.  This function does
 *  NOT normalise internally to keep the lookup pure (separation of
 *  concerns: identity normalisation lives in toolNames; module
 *  lookup lives here). */
export function getPrimitiveModule(
  toolName: string,
): PrimitiveModuleSpec | undefined {
  return ALL_PRIMITIVE_MODULES.find((m) => m.toolName === toolName);
}

/** Resolve a workflow module by its backend-canonical ``template_id``.
 *  Returns ``undefined`` when no module declares that template.
 *
 *  Stage 2: always returns ``undefined`` (arrays are empty).
 *  Stage 7+: returns the matching workflow spec. */
export function getWorkflowModule(
  templateId: string,
): WorkflowModuleSpec | undefined {
  return ALL_WORKFLOW_MODULES.find((m) => m.templateId === templateId);
}

// ----------------------------------------------------------------------------
// Type re-exports.
// ----------------------------------------------------------------------------
//
// Surfaces that consume module specs via the lookup helpers also
// need the type definitions to narrow safely.  Re-exporting here
// gives callers one import path:
//
//   import { getPrimitiveModule, type PrimitiveModuleSpec } from '@/modules';
//
// — instead of two (the helper from this file + the type from
// ``./types``).

export type {
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
} from './types';
export {
  ALL_SURFACE_TIERS,
  RUNTIME_STATUS_TIERS,
  CAPABILITY_TIERS,
  isRuntimeStatusTier,
  isCapabilityTier,
} from './types';
