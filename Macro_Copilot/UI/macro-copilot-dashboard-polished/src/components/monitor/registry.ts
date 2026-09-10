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
<<<<<<< HEAD
// V1 hand-authored catalog (today: only the pre-aggregated yield_snapshot).
=======
// Curve / tenor option sets — used by parameterized widget config forms.
// Mirrors the substrate's registered curve_family values (shared/schemas
// + rates_agent/playbooks).  Keep aligned with the backend; changes
// here that drift from the registered families will produce 4xx on
// detail-endpoint calls.

export const CURVE_OPTIONS: { value: string; label: string }[] = [
  { value: 'UST', label: 'UST · US Treasuries' },
  { value: 'DE_BUND', label: 'Bund · German' },
  { value: 'UK_GILT', label: 'Gilt · UK' },
  { value: 'JGB', label: 'JGB · Japan' },
  { value: 'FR_OAT', label: 'OAT · France' },
  { value: 'IT_BTP', label: 'BTP · Italy' },
  { value: 'ES_BONO', label: 'Bono · Spain' },
  { value: 'AU_GOVT', label: 'AUS · Australia' },
  { value: 'CANADA_GOVT', label: 'CAN · Canada' },
];

export const TENOR_OPTIONS: { value: string; label: string }[] = [
  { value: '2Y', label: '2Y' },
  { value: '5Y', label: '5Y' },
  { value: '10Y', label: '10Y' },
  { value: '30Y', label: '30Y' },
];

export const LOOKBACK_OPTIONS: { value: string; label: string }[] = [
  { value: '252', label: '1Y · 252 trading days' },
  { value: '504', label: '2Y · 504 days' },
  { value: '1260', label: '5Y · 1260 days' },
];

// FX-specific option sets. The supported pairs + tenors mirror the
// backend's fx_agent/forwards/_shared.py::SUPPORTED_FORWARD_TENORS
// and fx_agent/playbooks/fx_forwards.yml v2.0 universe. Adding a new
// G10 pair or tenor on the backend means appending an entry here too.
export const FX_FORWARD_PAIR_OPTIONS: { value: string; label: string }[] = [
  { value: 'EURUSD', label: 'EURUSD' },
  { value: 'GBPUSD', label: 'GBPUSD' },
  { value: 'USDJPY', label: 'USDJPY' },
  { value: 'AUDUSD', label: 'AUDUSD' },
  { value: 'USDCAD', label: 'USDCAD' },
  { value: 'USDCHF', label: 'USDCHF' },
];

// V1 cross-currency-basis pairs — those with matching local + USD OIS
// coverage in rates_agent (fx_agent/forwards/tools/cross_currency_basis).
export const FX_BASIS_PAIR_OPTIONS: { value: string; label: string }[] = [
  { value: 'EURUSD', label: 'EURUSD' },
  { value: 'GBPUSD', label: 'GBPUSD' },
  { value: 'USDJPY', label: 'USDJPY' },
  { value: 'AUDUSD', label: 'AUDUSD' },
  { value: 'USDCAD', label: 'USDCAD' },
];

export const FX_BASKET_SCOPE_OPTIONS: { value: string; label: string }[] = [
  { value: 'G10', label: 'G10' },
  { value: 'EM', label: 'EM' },
  { value: 'ALL', label: 'ALL' },
];

export const FX_BASKET_CONSTRUCTION_OPTIONS: { value: string; label: string }[] = [
  { value: 'long_short_top_n', label: 'Long/short top-N (default)' },
  { value: 'long_only_top_n', label: 'Long-only top-N' },
];

export const FX_BASKET_TOPN_OPTIONS: { value: string; label: string }[] = [
  { value: '2', label: 'Top 2' },
  { value: '3', label: 'Top 3 (default)' },
  { value: '4', label: 'Top 4' },
  { value: '5', label: 'Top 5' },
];

export const FX_FORWARD_TENOR_OPTIONS: { value: string; label: string }[] = [
  { value: '1W', label: '1W' },
  { value: '1M', label: '1M' },
  { value: '3M', label: '3M' },
  { value: '6M', label: '6M' },
  { value: '12M', label: '12M' },
];

export const FX_CARRY_RANK_BY_OPTIONS: { value: string; label: string }[] = [
  { value: 'carry_signed', label: 'Signed carry (default)' },
  { value: 'abs_carry', label: 'Absolute carry magnitude' },
  { value: 'abs_z_score', label: 'Absolute carry z-score' },
];

// Calendar-day lookback choices for the FX forwards tools. Different
// from LOOKBACK_OPTIONS above (which is trading-day) — the FX tools
// take calendar-day lookback_days bounded by Pydantic [30, 7300].
export const FX_LOOKBACK_DAYS_OPTIONS: { value: string; label: string }[] = [
  { value: '180', label: '6M · 180 calendar days' },
  { value: '365', label: '1Y · 365 calendar days (default)' },
  { value: '730', label: '2Y · 730 calendar days' },
  { value: '1825', label: '5Y · 1825 calendar days' },
];

>>>>>>> origin/codex/fx-ui-wave2-widgets
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
<<<<<<< HEAD
=======
  scanner: {
    id: 'scanner',
    label: 'Z-Score Scanner',
    description:
      'Top instruments flagged above your z-score threshold across the global universe.',
    category: 'anomaly',
    defaultSize: 'medium',
    allowedSizes: ['medium'],
    parameterized: false,
    sourceTool: 'scan_extremes_tool',
  },
  cross_market_spreads: {
    // Was `cross_market` with label "Cross-Market RV".  Renamed to
    // mirror the backing tool name (`calculate_cross_market_spread_tool`).
    id: 'cross_market_spreads',
    label: 'Cross-Market Spreads',
    description:
      'Cross-sovereign spreads (BTP-Bund, OAT-Bund, UST-Bund) at the 10Y point, with daily / monthly change, percentile, z-score. Backed by calculate_cross_market_spread_tool.',
    category: 'analysis',
    defaultSize: 'medium',
    allowedSizes: ['medium'],
    parameterized: false,
    sourceTool: 'calculate_cross_market_spread_tool',
  },
  curve_spreads: {
    id: 'curve_spreads',
    label: 'Curve Spreads',
    description:
      '2s10s slope across G4 curves — current spread, daily change, z-score, sparkline. Backed by calculate_curve_spread_tool.',
    category: 'data',
    defaultSize: 'medium',
    allowedSizes: ['medium'],
    parameterized: false,
    sourceTool: 'calculate_curve_spread_tool',
  },
  curve_classifier: {
    // Renamed in PR (was `regime_monitor`).  The principle: widget
    // ids and labels mirror the backing tool name in user-friendly
    // form.  Backing tool here is `classify_curve_move_tool`, so the
    // widget reads "Curve Classifier", not "Regime Monitor".
    id: 'curve_classifier',
    label: 'Curve Classifier',
    description:
      'Classifies daily / weekly curve moves as steepener, flattener, twist, or parallel shift across G4 curves. Backed by classify_curve_move_tool.',
    category: 'analysis',
    defaultSize: 'medium',
    allowedSizes: ['medium'],
    parameterized: false,
    sourceTool: 'classify_curve_move_tool',
  },

  // ---- Parameterized widgets (3) ----
  yield_level: {
    id: 'yield_level',
    label: 'Yield Level',
    description:
      'A single yield (curve × tenor) with daily change, z-score, and a 252-day sparkline.',
    category: 'data',
    defaultSize: 'small',
    allowedSizes: ['small', 'medium'],
    parameterized: true,
    paramFields: [
      {
        kind: 'select',
        name: 'curve_family',
        label: 'Curve',
        defaultValue: 'UST',
        options: CURVE_OPTIONS,
      },
      {
        kind: 'select',
        name: 'tenor',
        label: 'Tenor',
        defaultValue: '10Y',
        options: TENOR_OPTIONS,
      },
      {
        kind: 'select',
        name: 'lookback_days',
        label: 'Lookback',
        defaultValue: '252',
        options: LOOKBACK_OPTIONS,
      },
    ],
    sourceTool: 'get_yield_levels_tool',
  },
  spread_chart: {
    id: 'spread_chart',
    label: 'Curve Spread',
    description:
      'A custom curve-spread chart (e.g. UST 2s10s, Bund 5s30s) with rolling z-score band.',
    category: 'data',
    defaultSize: 'medium',
    allowedSizes: ['medium', 'tall'],
    parameterized: true,
    paramFields: [
      {
        kind: 'select',
        name: 'curve_family',
        label: 'Curve',
        defaultValue: 'UST',
        options: CURVE_OPTIONS,
      },
      {
        kind: 'select',
        name: 'short_tenor',
        label: 'Short tenor',
        defaultValue: '2Y',
        options: TENOR_OPTIONS,
      },
      {
        kind: 'select',
        name: 'long_tenor',
        label: 'Long tenor',
        defaultValue: '10Y',
        options: TENOR_OPTIONS,
        mustDifferFrom: 'short_tenor',
      },
      {
        kind: 'select',
        name: 'lookback_days',
        label: 'Lookback',
        defaultValue: '252',
        options: LOOKBACK_OPTIONS,
      },
    ],
    sourceTool: 'calculate_curve_spread_tool',
  },
  cross_market_spread: {
    id: 'cross_market_spread',
    label: 'Cross-Market Spread',
    description:
      'Custom cross-sovereign spread (e.g. BTP-Bund 10Y, OAT-Bund 10Y) with rolling z-score.',
    category: 'analysis',
    defaultSize: 'medium',
    allowedSizes: ['medium', 'tall'],
    parameterized: true,
    paramFields: [
      {
        kind: 'select',
        name: 'curve_family_1',
        label: 'Curve A',
        defaultValue: 'IT_BTP',
        options: CURVE_OPTIONS,
      },
      {
        kind: 'select',
        name: 'curve_family_2',
        label: 'Curve B',
        defaultValue: 'DE_BUND',
        options: CURVE_OPTIONS,
        mustDifferFrom: 'curve_family_1',
      },
      {
        kind: 'select',
        name: 'tenor',
        label: 'Tenor',
        defaultValue: '10Y',
        options: TENOR_OPTIONS,
      },
      {
        kind: 'select',
        name: 'lookback_days',
        label: 'Lookback',
        defaultValue: '252',
        options: LOOKBACK_OPTIONS,
      },
    ],
    sourceTool: 'calculate_cross_market_spread_tool',
  },

  // ---- Pre-aggregated FX widgets (3) ----
  fx_spot_snapshot: {
    id: 'fx_spot_snapshot',
    label: 'FX Spot Snapshot',
    description:
      'Single FX pair (EURUSD in V1) — current spot, 1D / 1W / 1M change, 252-day z-score and range. Backed by get_fx_spot_level.',
    category: 'data',
    defaultSize: 'medium',
    allowedSizes: ['medium'],
    parameterized: false,
    sourceTool: 'get_fx_spot_level',
  },
  fx_scanner: {
    id: 'fx_scanner',
    label: 'FX Scanner',
    description:
      'Top FX pairs ranked by absolute rolling z-score with deterministic signal labels. Backed by scan_fx_spot.',
    category: 'anomaly',
    defaultSize: 'medium',
    allowedSizes: ['medium'],
    parameterized: false,
    sourceTool: 'scan_fx_spot',
  },
  fx_carry: {
    id: 'fx_carry',
    label: 'FX Carry Scanner',
    description:
      'Cross-sectional G10 FX carry scanner at a chosen tenor (1W / 1M / 3M / 6M / 12M), ranked by signed carry, absolute carry, or absolute carry z-score. Each row carries a rolling 252-day z-score / percentile / range on its own annualised-carry series. Backed by calculate_fx_carry.',
    category: 'analysis',
    defaultSize: 'wide',
    allowedSizes: ['wide'],
    parameterized: true,
    paramFields: [
      {
        kind: 'select',
        name: 'tenor',
        label: 'Tenor',
        defaultValue: '1M',
        options: FX_FORWARD_TENOR_OPTIONS,
      },
      {
        kind: 'select',
        name: 'rank_by',
        label: 'Rank by',
        defaultValue: 'carry_signed',
        options: FX_CARRY_RANK_BY_OPTIONS,
      },
      {
        kind: 'number',
        name: 'top_n',
        label: 'Top N',
        defaultValue: 6,
        min: 1,
        max: 20,
        step: 1,
      },
      {
        kind: 'select',
        name: 'lookback_days',
        label: 'Lookback',
        defaultValue: '365',
        options: FX_LOOKBACK_DAYS_OPTIONS,
      },
    ],
    sourceTool: 'calculate_fx_carry',
  },
  fx_forward_curve: {
    id: 'fx_forward_curve',
    label: 'FX Forward Curve',
    description:
      'For one G10 pair, the full forward-curve term structure (1W / 1M / 3M / 6M / 12M) with raw forward points, outright forward, annualised carry, and the rolling 252-day z-score / percentile / range on spot-unit forward points. Numerically consistent with FX Carry Scanner at any (pair, tenor). Backed by get_fx_forward_curve.',
    category: 'data',
    defaultSize: 'wide',
    allowedSizes: ['medium', 'wide'],
    parameterized: true,
    paramFields: [
      {
        kind: 'select',
        name: 'pair',
        label: 'Pair',
        defaultValue: 'EURUSD',
        options: FX_FORWARD_PAIR_OPTIONS,
      },
      {
        kind: 'select',
        name: 'lookback_days',
        label: 'Lookback',
        defaultValue: '365',
        options: FX_LOOKBACK_DAYS_OPTIONS,
      },
    ],
    sourceTool: 'get_fx_forward_curve',
  },
  fx_carry_basket: {
    id: 'fx_carry_basket',
    label: 'FX Carry Basket',
    description:
      'Paper STRATEGY INDEX: cumulative excess-return equity curve of a long-top-N / short-bottom-N FX carry basket (monthly rebalance, equal-weight, no transaction costs), with annualised return / vol / Sharpe / max-drawdown and the live basket constituents. Relative-value & regime tool, not an executable backtest. Backed by get_fx_carry_basket.',
    category: 'data',
    defaultSize: 'wide',
    allowedSizes: ['medium', 'wide'],
    parameterized: true,
    paramFields: [
      { kind: 'select', name: 'market_scope', label: 'Scope', defaultValue: 'G10', options: FX_BASKET_SCOPE_OPTIONS },
      { kind: 'select', name: 'tenor', label: 'Tenor', defaultValue: '1M', options: FX_FORWARD_TENOR_OPTIONS },
      { kind: 'select', name: 'top_n', label: 'Top-N', defaultValue: '3', options: FX_BASKET_TOPN_OPTIONS },
      { kind: 'select', name: 'basket_construction', label: 'Construction', defaultValue: 'long_short_top_n', options: FX_BASKET_CONSTRUCTION_OPTIONS },
    ],
    sourceTool: 'get_fx_carry_basket',
  },
  fx_vol_smile: {
    id: 'fx_vol_smile',
    label: 'FX Vol Smile',
    description:
      'The implied-vol smile reconstructed across delta (10ΔP · 25ΔP · ATM · 25ΔC · 10ΔC) from the ATM / 25Δ-RR / 25Δ-BF / 10Δ-RR / 10Δ-BF quotes, plus the raw RR/BF table with rolling 252-day z-scores. Backed by get_fx_vol_smile.',
    category: 'data',
    defaultSize: 'medium',
    allowedSizes: ['medium', 'wide'],
    parameterized: true,
    paramFields: [
      { kind: 'select', name: 'pair', label: 'Pair', defaultValue: 'EURUSD', options: FX_FORWARD_PAIR_OPTIONS },
      { kind: 'select', name: 'tenor', label: 'Tenor', defaultValue: '1M', options: FX_FORWARD_TENOR_OPTIONS },
      { kind: 'select', name: 'lookback_days', label: 'Lookback', defaultValue: '365', options: FX_LOOKBACK_DAYS_OPTIONS },
    ],
    sourceTool: 'get_fx_vol_smile',
  },
  fx_cross_currency_basis: {
    id: 'fx_cross_currency_basis',
    label: 'FX Cross-Currency Basis',
    description:
      'CIP basis (bps) for one V1 pair (EURUSD/GBPUSD/USDJPY/AUDUSD/USDCAD), with the FX-implied-vs-OIS decomposition that produces it, rolling z-score, 1d/1w/1m changes, and the 252-day range. Sign convention Bloomberg BCRX-style — NEGATIVE = USD scarcity. Cross-domain: reads the rates_agent OIS substrate. Backed by get_fx_cross_currency_basis.',
    category: 'data',
    defaultSize: 'medium',
    allowedSizes: ['medium', 'wide'],
    parameterized: true,
    paramFields: [
      { kind: 'select', name: 'pair', label: 'Pair', defaultValue: 'EURUSD', options: FX_BASIS_PAIR_OPTIONS },
      { kind: 'select', name: 'tenor', label: 'Tenor', defaultValue: '1M', options: FX_FORWARD_TENOR_OPTIONS },
      { kind: 'select', name: 'lookback_days', label: 'Lookback', defaultValue: '365', options: FX_LOOKBACK_DAYS_OPTIONS },
    ],
    sourceTool: 'get_fx_cross_currency_basis',
  },
>>>>>>> origin/codex/fx-ui-wave2-widgets
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
<<<<<<< HEAD
  ...HAND_AUTHORED_CATALOG_ORDER,
  ...MODULE_DERIVED_CATALOG_ORDER,
=======
  'yield_snapshot',
  'scanner',
  'cross_market_spreads',
  'curve_spreads',
  'curve_classifier',
  'yield_level',
  'spread_chart',
  'cross_market_spread',
  'fx_spot_snapshot',
  'fx_scanner',
  'fx_carry',
  'fx_forward_curve',
  'fx_carry_basket',
  'fx_vol_smile',
  'fx_cross_currency_basis',
>>>>>>> origin/codex/fx-ui-wave2-widgets
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
