// ============================================================================
// Widget registry — Stage 4d hybrid (module-derived + pre-aggregated dashboard)
// ----------------------------------------------------------------------------
// The single source of truth for which widgets exist, what they're called,
// what params they accept, what sizes they support, and which underlying
// tool they expose.
//
// Stage 4d composition
// --------------------
// The public ``WIDGET_TYPES`` map is a hybrid:
//
//   * Module-derived entries  — every entry in
//     ``ALL_PRIMITIVE_MODULES.flatMap(m => m.monitorWidgets ?? [])``,
//     with ``sourceTool`` filled in from the owning module's
//     ``toolName``.  Today this contributes 7 widgets across 5 modules
//     (curve_spreads, spread_chart, cross_market_spreads,
//     cross_market_spread, curve_classifier, scanner, yield_level).
//
//   * Hand-authored entries   — only the pre-aggregated
//     ``yield_snapshot`` widget remains.  It reads from the dashboard
//     aggregate endpoint (``rates/yield-snapshot``) rather than a
//     specific primitive's typed-detail endpoint, so it doesn't bind
//     to a single primitive module.  Lives here until a future PR
//     either ships a dedicated dashboard "module" or moves it into a
//     non-primitive ``src/modules/dashboards/`` family.
//
// Catalog order
// -------------
// Hand-authored entries appear FIRST (so yield_snapshot keeps its top
// slot in the catalog modal as users expect), followed by the module-
// derived entries in alphabetical-by-id order.  Stable ordering is
// load-bearing — the localStorage layout schema treats widget ids as
// stable strings.
//
// Adding a new widget
// -------------------
// * Per-primitive widget: add an entry to the owning module's
//   ``monitorWidgets`` array in ``src/modules/primitives/<tool>/module.ts``.
//   No edit to this file required.
// * Pre-aggregated / cross-tool widget: add to ``HAND_AUTHORED_WIDGETS``
//   below + add the renderer wiring in ``WidgetRenderer.tsx``.
// ============================================================================

import { ALL_PRIMITIVE_MODULES } from '@/modules';

// Stage 4d — re-export the public types from the leaf-file shape so
// downstream consumers (registry-importing files, tests) continue to
// import everything from ``@/components/monitor/registry`` as before.
export type {
  WidgetCategory,
  WidgetSize,
  WidgetParamField,
  WidgetTypeMeta,
} from '@/types/monitorWidget';
export {
  CURVE_OPTIONS,
  TENOR_OPTIONS,
  LOOKBACK_OPTIONS,
} from '@/lib/monitorParamOptions';

import type { WidgetTypeMeta } from '@/types/monitorWidget';

// ----------------------------------------------------------------------------
// V1 hand-authored catalog (today: only the pre-aggregated yield_snapshot).
// ----------------------------------------------------------------------------

const HAND_AUTHORED_WIDGETS: Record<string, WidgetTypeMeta> = {
  yield_snapshot: {
    id: 'yield_snapshot',
    label: 'Yield Snapshot',
    description:
      'Sovereign benchmark yields across curves × tenors with daily change and z-score.',
    category: 'data',
    defaultSize: 'wide',
    allowedSizes: ['wide'],
    parameterized: false,
    sourceTool: 'rates/yield-snapshot (aggregated)',
  },
};

const HAND_AUTHORED_CATALOG_ORDER: string[] = ['yield_snapshot'];

// ----------------------------------------------------------------------------
// Module-derived contributions.  Build at module-load time by walking
// every module's ``monitorWidgets`` array.  Stable derivation: order
// follows ``ALL_PRIMITIVE_MODULES`` (alphabetical by tool name) and
// preserves the per-module declaration order of widgets within a tool.
// ----------------------------------------------------------------------------

const MODULE_DERIVED_WIDGETS: Record<string, WidgetTypeMeta> = {};
const MODULE_DERIVED_CATALOG_ORDER: string[] = [];

for (const m of ALL_PRIMITIVE_MODULES) {
  const widgets = m.monitorWidgets ?? [];
  for (const w of widgets) {
    if (MODULE_DERIVED_WIDGETS[w.id]) {
      // Two modules declared the same widget id — this is a spec bug
      // (every Monitor widget id must be globally unique).  Surface
      // it loudly at module load.
      throw new Error(
        `Monitor widget id collision: '${w.id}' declared by both ` +
          `'${MODULE_DERIVED_WIDGETS[w.id].sourceTool}' and '${m.toolName}'.`,
      );
    }
    MODULE_DERIVED_WIDGETS[w.id] = {
      id: w.id,
      label: w.label,
      description: w.description,
      category: w.category,
      defaultSize: w.defaultSize,
      allowedSizes: w.allowedSizes,
      parameterized: w.parameterized,
      paramFields: w.paramFields,
      sourceTool: m.toolName,
    };
    MODULE_DERIVED_CATALOG_ORDER.push(w.id);
  }
}

// ----------------------------------------------------------------------------
// Public exports — union of hand-authored + module-derived.
// ----------------------------------------------------------------------------

export const WIDGET_TYPES: Record<string, WidgetTypeMeta> = {
  ...HAND_AUTHORED_WIDGETS,
  ...MODULE_DERIVED_WIDGETS,
};

/** Order in which widgets appear in the catalog modal.  Hand-authored
 *  entries first (so yield_snapshot keeps its top slot), then module-
 *  derived entries in their declaration order.  Bumping
 *  ``LAYOUT_VERSION`` invalidates user layouts when the schema changes
 *  are not backward-compatible. */
export const CATALOG_ORDER: string[] = [
  ...HAND_AUTHORED_CATALOG_ORDER,
  ...MODULE_DERIVED_CATALOG_ORDER,
];

// ----------------------------------------------------------------------------
// Layout schema (persisted in localStorage)

/** Bumping this invalidates all user layouts.  Bump only when the
 *  schema is structurally incompatible (renamed fields, new required
 *  fields, etc.).  Adding a new widget type to ``monitorWidgets`` does
 *  NOT require a bump.
 *
 *  v1 → v2: renamed widget ids to mirror their backing tool names
 *  (regime_monitor → curve_classifier, cross_market → cross_market_spreads,
 *  curve_shapes → curve_spreads).
 *
 *  Stage 4d did NOT change any widget ids — the module-derived
 *  contributions reuse the exact ids the hand-authored set defined,
 *  so the LAYOUT_VERSION stays at 2 and existing user layouts keep
 *  rendering. */
export const LAYOUT_VERSION = 2;

export type WidgetInstance = {
  /** Unique per-instance id; minted via crypto.randomUUID. */
  id: string;
  /** Matches a key in WIDGET_TYPES.  Unknown types are dropped on read. */
  type: string;
  size: import('@/types/monitorWidget').WidgetSize;
  /** Empty for non-parameterized widgets. */
  params: Record<string, unknown>;
};

export type LayoutState = {
  version: number;
  widgets: WidgetInstance[];
};

// ----------------------------------------------------------------------------
// Helpers

export function isKnownWidgetType(id: string): boolean {
  return Object.prototype.hasOwnProperty.call(WIDGET_TYPES, id);
}

export function widgetMeta(id: string): WidgetTypeMeta | null {
  return WIDGET_TYPES[id] ?? null;
}

/** Mints a fresh widget instance with the given type's default size. */
export function buildWidgetInstance(
  typeId: string,
  overrides?: Partial<Pick<WidgetInstance, 'size' | 'params'>>,
): WidgetInstance | null {
  const meta = widgetMeta(typeId);
  if (!meta) return null;
  return {
    id: cryptoRandomId(),
    type: typeId,
    size: overrides?.size ?? meta.defaultSize,
    params: overrides?.params ?? defaultParamsFor(meta),
  };
}

/** Default params object for a parameterized widget.  Reads each
 *  paramField's defaultValue verbatim. */
export function defaultParamsFor(
  meta: WidgetTypeMeta,
): Record<string, unknown> {
  if (!meta.parameterized || !meta.paramFields) return {};
  const out: Record<string, unknown> = {};
  for (const field of meta.paramFields) {
    out[field.name] = field.defaultValue;
  }
  return out;
}

function cryptoRandomId(): string {
  // crypto.randomUUID is supported in all modern browsers; fall back
  // to a Math.random hex if the environment lacks it (Node / SSR).
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `w-${Math.random().toString(16).slice(2, 10)}-${Date.now().toString(16)}`;
}
