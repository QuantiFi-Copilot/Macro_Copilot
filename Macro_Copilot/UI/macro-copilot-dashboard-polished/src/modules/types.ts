// ============================================================================
// src/modules/types.ts — frontend module spec contract (Stage 2).
// ----------------------------------------------------------------------------
// Stage 2 — Build the src/modules/ infrastructure.  This file is the
// canonical type contract every primitive / workflow module satisfies.
// No modules exist yet (those land in Stage 3+ per the migration
// roadmap at docs_revamped/06_roadmap/frontend_migration.md); this
// file defines the SHAPE every module's ``module.ts`` must match so
// the loader barrel at ``src/modules/index.ts`` can compose them.
//
// Doctrine references
// -------------------
// - FM3 "Surface-tier capability declaration"  → SurfaceTier closed family
// - FM7 "Pure-spec assembly (no side effects)" → spec values, never functions
// - FM8 "Surface-file contract"                → surface-prop interfaces
// - FM6 "Unsupported-reason surfacing"         → UnsupportedKnownReason
// - FP3 "Surface-tier capability declaration"  → closed-family enforcement
// - FP5 "Pure-spec assembly"                   → no side effects
// See docs_revamped/02_components/frontend_module/README.md.
//
// Backwards compatibility
// -----------------------
// Stage 2 lands these types behind ``src/modules/`` with no other code
// importing them.  The existing UI continues to work exactly as it did
// pre-Stage-2 because nothing in the active code paths consumes this
// module yet.  Stage 3+ introduces the first per-primitive module
// folders that import + populate these types.
// ============================================================================

import type { ComponentType } from 'react';
import type {
  DecodedPrimitive,
  PrimitiveViewKind,
} from '@/components/build/primitive/contextDecoder';
import type {
  UnsupportedKnownReason as ToolUnsupportedKnownReason,
} from '@/lib/toolNames';

// ---------------------------------------------------------------------------
// SurfaceTier — closed family (FP3 / FM3).
// ---------------------------------------------------------------------------
//
// Adding a new tier requires an ADR amending the closed family per
// docs_revamped/02_components/frontend_module/tiers.md.  Drift between
// this literal and the tiers.md catalogue is a P8 violation.
//
// Two groups:
//   - Runtime-status tiers (exactly one per module):
//       generic_runnable | workflow_incompatible | paused | deferred
//   - Capability tiers (zero or more per module):
//       custom_build_surface | custom_preview_widget |
//       monitor_surface     | ask_surface

export type RuntimeStatusTier =
  | 'generic_runnable'
  | 'workflow_incompatible'
  | 'paused'
  | 'deferred';

export type CapabilityTier =
  | 'custom_build_surface'
  | 'custom_preview_widget'
  | 'monitor_surface'
  | 'ask_surface';

export type SurfaceTier = RuntimeStatusTier | CapabilityTier;

/** The exhaustive set of tier values — useful for validators that
 *  need to enumerate the closed family rather than rely on type-only
 *  narrowing (TypeScript erases the union literal at runtime).
 *
 *  Order matters for diagnostic output: runtime-status tiers first,
 *  capability tiers second, alphabetised within each group. */
export const ALL_SURFACE_TIERS: ReadonlyArray<SurfaceTier> = [
  // Runtime-status (mutually exclusive)
  'deferred',
  'generic_runnable',
  'paused',
  'workflow_incompatible',
  // Capability (free-combine)
  'ask_surface',
  'custom_build_surface',
  'custom_preview_widget',
  'monitor_surface',
];

export const RUNTIME_STATUS_TIERS: ReadonlySet<RuntimeStatusTier> = new Set([
  'generic_runnable',
  'workflow_incompatible',
  'paused',
  'deferred',
]);

export const CAPABILITY_TIERS: ReadonlySet<CapabilityTier> = new Set([
  'custom_build_surface',
  'custom_preview_widget',
  'monitor_surface',
  'ask_surface',
]);

export function isRuntimeStatusTier(t: SurfaceTier): t is RuntimeStatusTier {
  return RUNTIME_STATUS_TIERS.has(t as RuntimeStatusTier);
}

export function isCapabilityTier(t: SurfaceTier): t is CapabilityTier {
  return CAPABILITY_TIERS.has(t as CapabilityTier);
}

// ---------------------------------------------------------------------------
// Surface Prop interfaces (FM8).
// ---------------------------------------------------------------------------
//
// Each capability tier corresponds to exactly one surface file with a
// fixed default-exported component shape.  Module surfaces consume
// the standard prop interfaces declared here; modules MUST NOT
// require bespoke props beyond these (SI4 — stable Prop contracts).
//
// Stage 2 ships the SHAPES.  Stage 3+'s page shells start passing the
// prop values to module surfaces (today the existing page shells
// still consume per-tool components directly via legacy imports;
// migration to module-spec lookup happens in Stage 4a/4b/4c).

/** Props shared by every module surface — always carries the
 *  backend-canonical tool name + the URL params dict.  Surfaces that
 *  need workspace-scoped state read it from React context (the
 *  ``WorkspaceOverridesProvider`` exposed by BuildShell). */
export interface ModuleSurfaceBaseProps {
  /** Backend-canonical tool name (already passed through
   *  ``normalizeToolName`` by upstream callers). */
  toolName: string;
  /** Flat string-only params dict matching the typed-detail
   *  endpoint's query-param shape.  Empty when no URL params were
   *  supplied. */
  params: Record<string, string>;
}

/** Build-surface prop interface — used by ``surfaces/BuildSurface.tsx``
 *  on modules that claim ``custom_build_surface``.  The full
 *  ``decoded`` object is included so the surface can reach
 *  ``paramsStructured`` for rich-model and generic-builder
 *  pre-seeding (PR-B-β contract). */
export interface BuildSurfaceProps extends ModuleSurfaceBaseProps {
  /** PR-B-β — full decoded context entry.  Surfaces that need the
   *  structured (non-string-coerced) params dict read ``decoded.paramsStructured``;
   *  surfaces that only need the flat dict use ``params`` from the
   *  base interface. */
  decoded: DecodedPrimitive;
  /** PR-B-β — true when the URL carried ``handoff=ask``.  Lets the
   *  surface choose to render the missing-param tile vs silent-
   *  defaults for Ask-handoff vs Library-blank invocations. */
  askHandoff?: boolean;
}

/** Monitor-widget prop interface — used by ``surfaces/MonitorWidget.tsx``
 *  on modules that claim ``monitor_surface``.  The Monitor bento grid
 *  passes the user's per-widget configuration via ``params``.  Widgets
 *  that consume aggregated rates data read from ``RatesDataProvider``
 *  via ``useRatesDataContext``; per-widget bespoke fetches happen
 *  inside the widget body. */
export interface MonitorWidgetProps extends ModuleSurfaceBaseProps {
  /** Optional widget-instance id — useful when a widget needs to
   *  scope localStorage or telemetry per-instance.  When the user
   *  has multiple instances of the same widget in their layout
   *  each gets a distinct id. */
  instanceId?: string;
}

/** Ask-card prop interface — used by ``surfaces/AskCard.tsx`` on
 *  modules that claim ``ask_surface``.  The chat ``ConversationCanvas``
 *  passes the WorkflowResultMessage's typed output for this tool. */
export interface AskCardProps extends ModuleSurfaceBaseProps {
  /** The raw tool-result payload from the backend.  Each module's
   *  AskCard narrows this to its own ``*Output`` shape at the call
   *  site (e.g. ``output as CpiSurpriseOutput``).  Kept as
   *  ``unknown`` here so modules can declare their own narrowing
   *  without round-tripping every shape through this base interface. */
  output: unknown;
  /** PR-B-β — original chat message id for the result, so the card
   *  can deep-link back to the conversation if needed. */
  messageId?: string;
}

/** Preview-widget prop interface — used by ``surfaces/PreviewWidget.tsx``
 *  on modules that claim ``custom_preview_widget``.  The Build
 *  completed-workspace view passes a persisted artifact summary +
 *  per-tool adapter result; the widget renders the persisted-card
 *  preview.  Compatible with the existing ``NodeRenderer`` contract
 *  in ``components/build/lib/nodeRendererRegistry.ts`` so per-tool
 *  preview widgets that already exist (PCA / regression / etc.) can
 *  migrate without prop-shape churn. */
export interface PreviewWidgetProps extends ModuleSurfaceBaseProps {
  /** Persisted node identifier (workspace's DAG node hash). */
  nodeId: string;
}

// ---------------------------------------------------------------------------
// Unsupported reason (re-export for module-spec ergonomics).
// ---------------------------------------------------------------------------
//
// ``UNSUPPORTED_KNOWN_REASONS`` lives in src/lib/toolNames.ts so the
// existing ``UnsupportedKnownToolCanvas`` can read per-tool copy
// without depending on the module spec.  Modules declare their
// ``unsupportedReason`` directly on the spec; the loader composes
// per-tool reasons into the runtime map.

export type UnsupportedKnownReason = ToolUnsupportedKnownReason;

// ---------------------------------------------------------------------------
// PrimitiveModuleSpec — the canonical primitive-module value shape.
// ---------------------------------------------------------------------------
//
// Every primitive module's ``module.ts`` exports
// ``export const MODULE: PrimitiveModuleSpec = { ... };``.  The
// spec is a PURE VALUE (FM7 / FP5) — no functions invoked at module-
// eval time, no global mutation, no side-effect imports.

export interface PrimitiveModuleSpec {
  // -------------------------------------------------------------------
  // FM1 — identity.  Folder name MUST equal ``toolName`` exactly.
  // The loader-presence test (Stage 2) asserts this invariant when
  // each module's folder is added in Stage 3+.
  // -------------------------------------------------------------------
  /** Backend-canonical tool name (e.g. ``calculate_cpi_surprise_tool``).
   *  MUST match a key in either ``_PRIMITIVE_SPECS`` (runnable),
   *  ``WORKFLOW_INCOMPATIBLE_TOOLS`` (workflow-incompatible), or
   *  ``_MANIFEST_ONLY_BUILD_TOOLS`` / explicit ``deferred`` reservation
   *  on the backend.  The cross-side parity script asserts the union
   *  equality. */
  toolName: string;

  // -------------------------------------------------------------------
  // FM3 — tier claims (closed family + mutually-exclusive runtime
  // status).
  // -------------------------------------------------------------------
  /** Non-empty subset of the SurfaceTier closed family.  Exactly one
   *  runtime-status tier; zero or more capability tiers.  Validation
   *  rules in docs_revamped/02_components/frontend_module/tiers.md
   *  are encoded in ``assertStandardModuleInvariants``. */
  tiers: ReadonlyArray<SurfaceTier>;

  // -------------------------------------------------------------------
  // FM5 — display metadata (sourced from backend ToolCard where
  // possible; deliberate divergence is THESIS-documented).
  // -------------------------------------------------------------------
  /** Human-facing title in Title Case.  Shown in Library card title,
   *  Build header, DAG node chip. */
  displayName: string;
  /** Manifest ``category`` slug — drives Library category chip /
   *  per-card tone.  See CATEGORY_LABELS in src/types/library.ts. */
  category: string;
  /** One-sentence summary derived from backend
   *  ``methodology.what_it_does``. */
  oneLineSummary: string;
  /** Default form values for the generic builder / rich-model
   *  surface.  Keys match the backend ``input_fields`` names.  Empty
   *  ``{}`` is acceptable when the backend defaults are sufficient. */
  defaultParams?: Record<string, unknown>;
  /** Optional per-field paramHint overrides for the rich-model
   *  builder.  Most fields infer correctly from name via
   *  ``inferFieldControl``; override only when the inference is wrong
   *  for this specific primitive. */
  paramHints?: Record<string, unknown>;
  /** Optional rich-card content rendered below the canvas (PCA's
   *  "PC1 = Level" cards, etc.). */
  interpretationCards?: ReadonlyArray<{ headline: string; body: string }>;

  // -------------------------------------------------------------------
  // FM9 — routing claims.  Mutually exclusive: at most one of
  // ``typedView`` / ``richModel`` is non-default at a time, since
  // those routes preempt the generic builder.
  // -------------------------------------------------------------------
  /** When the module routes through an existing typed primitive view
   *  (Spread / CrossMarket / Butterfly / Yield / Regime / Scanner /
   *  Forward), set the kind here.  ``null`` (default) means the module
   *  does NOT claim a typed-view route. */
  typedView?: PrimitiveViewKind | null;
  /** True when the module uses the rich-model builder (BuilderCanvas).
   *  Mutually exclusive with ``typedView``.  Defaults to false. */
  richModel?: boolean;

  // -------------------------------------------------------------------
  // FM8 — surface references.  Only present for claimed capability
  // tiers.  Each value is a React component matching the matching
  // surface-prop interface above.
  // -------------------------------------------------------------------
  surfaces?: {
    build?: ComponentType<BuildSurfaceProps>;
    preview?: ComponentType<PreviewWidgetProps>;
    monitor?: ComponentType<MonitorWidgetProps>;
    ask?: ComponentType<AskCardProps>;
  };

  // -------------------------------------------------------------------
  // FM6 — unsupported reason.  Required when tiers includes
  // workflow_incompatible | paused | deferred; null / omitted when
  // the module is generic_runnable.
  // -------------------------------------------------------------------
  /** Per-tool reason copy surfaced on the UnsupportedKnownToolCanvas.
   *  For workflow_incompatible modules, this MUST mirror the backend's
   *  ``WORKFLOW_INCOMPATIBLE_TOOLS`` rationale verbatim. */
  unsupportedReason?: UnsupportedKnownReason | null;

  // -------------------------------------------------------------------
  // Optional metadata.
  // -------------------------------------------------------------------
  /** Manifest-shorthand aliases.  Rare — used when the manifest emits
   *  a tool under a different name than the backend registry (e.g.
   *  ``calculate_ois_rate_level_tool`` → ``get_ois_rate_level_tool``).
   *  The Stage 1 alias table in toolNames.ts is the source of truth
   *  today; this field is reserved for future per-module aliases. */
  aliases?: ReadonlyArray<string>;
}

// ---------------------------------------------------------------------------
// WorkflowModuleSpec — analogous shape for workflow templates.
// ---------------------------------------------------------------------------
//
// Workflow modules land in Stage 7 of the migration.  Stage 2 defines
// the shape so the loader barrel can declare ALL_WORKFLOW_MODULES
// uniformly with ALL_PRIMITIVE_MODULES.

export interface WorkflowModuleSpec {
  /** Backend canonical template_id (e.g. ``event_study``,
   *  ``regime_conditioned_relationship``). */
  templateId: string;
  /** Same closed-family tiers as primitives.  Workflows reuse the
   *  primitive tier set per docs_revamped/02_components/frontend_module/tiers.md
   *  open question 2; the default-active derivation in
   *  ``KNOWN_WORKFLOWS`` checks for "not paused, not deferred". */
  tiers: ReadonlyArray<SurfaceTier>;
  /** Human-facing title (e.g. ``Event Study``). */
  displayName: string;
  /** Category slug (workflow templates share the primitive category
   *  vocabulary today). */
  category: string;
  /** One-sentence description of the workflow's analytical shape. */
  oneLineSummary: string;
  /** Per-template surface refs.  Workflows render via the dashboard
   *  surface; build is reserved for templates that need a bespoke
   *  status canvas beyond the default WorkflowStatusCanvas. */
  surfaces?: {
    results?: ComponentType<BuildSurfaceProps>;
    status?: ComponentType<BuildSurfaceProps>;
  };
  /** Reason copy for paused / deferred workflow modules. */
  unsupportedReason?: UnsupportedKnownReason | null;
}
