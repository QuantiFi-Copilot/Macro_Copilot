// ============================================================================
// Widget registry
// ----------------------------------------------------------------------------
// The single source of truth for which widgets exist, what they're called,
// what params they accept, what sizes they support, and which underlying
// tool they expose.
//
// Each widget belongs to a small, fixed catalog: pre-aggregated widgets
// (yield snapshot, scanner, cross-market, curve shapes, regime monitor)
// read from the rates-page card endpoints; parameterized widgets call
// the rates detail endpoints with user-picked params.
//
// Every widget id is also a stable string used as the key in localStorage
// layout state — bumping `LAYOUT_VERSION` invalidates user layouts when
// schema changes are not backward-compatible.
//
// V1 catalog: 8 rates widgets.  V2 will add OIS variants, swap-spread,
// PCA, half-life, beta-adjusted-spread, FX widgets, etc.  Adding a new
// widget = one entry here + one renderer file under widgets/ + one entry
// in the renderer registry (see WidgetRenderer.tsx).
// ============================================================================

export type WidgetCategory = 'data' | 'analysis' | 'anomaly';

/** Bento-grid sizes.  Small/medium are 4-up / 2-up on a 12-column grid;
 *  wide is full-row; tall is medium-width with double height for charts
 *  that need vertical space (PCA, attribution decomposition). */
export type WidgetSize = 'small' | 'medium' | 'wide' | 'tall';

/** Per-field config for parameterized widgets.  Each field renders a
 *  matching control in the catalog modal's config form.  Conservative
 *  set: select (enum) and number — enough for every V1 rates widget. */
export type WidgetParamField =
  | {
      kind: 'select';
      name: string;
      label: string;
      defaultValue: string;
      options: { value: string; label: string }[];
      /** Optional cross-field constraint hint surfaced in the form. */
      mustDifferFrom?: string;
    }
  | {
      kind: 'number';
      name: string;
      label: string;
      defaultValue: number;
      min?: number;
      max?: number;
      step?: number;
    };

export type WidgetTypeMeta = {
  /** Stable id; used as registry key + serialized in layout state. */
  id: string;
  /** User-facing label in the catalog. */
  label: string;
  /** One-line description shown in the catalog tile. */
  description: string;
  /** Drives the gradient top-rule color on the card. */
  category: WidgetCategory;
  /** Size assigned when the user adds this widget without specifying. */
  defaultSize: WidgetSize;
  /** Sizes the user can choose from at add-time. */
  allowedSizes: WidgetSize[];
  /** True if the widget needs user-supplied params (curve, tenor, etc).
   *  Pre-aggregated widgets are false — they show "the rates page" data
   *  unchanged. */
  parameterized: boolean;
  /** Per-field config used to render the catalog modal's config form. */
  paramFields?: WidgetParamField[];
  /** The substrate primitive (or pre-aggregated endpoint) this widget
   *  surfaces.  Documented for the catalog tile + provenance footer. */
  sourceTool: string;
};

// ----------------------------------------------------------------------------
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

// ----------------------------------------------------------------------------
// V1 widget catalog

export const WIDGET_TYPES: Record<string, WidgetTypeMeta> = {
  // ---- Pre-aggregated rate widgets (5) ----
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
};

/** Order in which widgets appear in the catalog modal. */
export const CATALOG_ORDER: string[] = [
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
];

// ----------------------------------------------------------------------------
// Layout schema (persisted in localStorage)

/** Bumping this invalidates all user layouts.  Bump only when the
 *  schema is structurally incompatible (renamed fields, new required
 *  fields, etc.).  Adding a new widget type to WIDGET_TYPES does NOT
 *  require a bump.
 *
 *  v1 → v2: renamed widget ids to mirror their backing tool names.
 *    - regime_monitor       → curve_classifier
 *    - cross_market         → cross_market_spreads
 *    - curve_shapes         → curve_spreads
 *  Existing localStorage layouts referencing the old ids would have
 *  their widgets dropped on read (defensive filter), so we bump the
 *  version to force a clean reset to defaults.
 */
export const LAYOUT_VERSION = 2;

export type WidgetInstance = {
  /** Unique per-instance id; minted via crypto.randomUUID. */
  id: string;
  /** Matches a key in WIDGET_TYPES.  Unknown types are dropped on read. */
  type: string;
  size: WidgetSize;
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
