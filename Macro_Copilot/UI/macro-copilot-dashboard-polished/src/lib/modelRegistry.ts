// ============================================================================
// Model registry — metadata layer over the primitive catalogue
// ----------------------------------------------------------------------------
// Augments each primitive's wire-level ToolCard (which carries input/output
// schema + methodology + conventions) with UI-only render hints:
//
//   - which output renderer to use (`pca`, `rolling_regression`,
//     `attribution`, `time_series`, `series_panel`, or `auto`)
//   - which custom control to render per input field (e.g. `series_spec`,
//     `multi_tenor`, `window_slider`, `lookback_slider`, or `auto`)
//   - human-friendly display names + categories for the workspace header
//   - optional interpretation cards (concrete one-liners about what
//     the output means in practice)
//
// Why a registry instead of more wire fields?
// -------------------------------------------
// The wire ToolCard is the substrate's source of truth and crosses the
// network.  Render hints are presentation-only — they belong to the UI.
// Keeping them client-side here means:
//
//   1. A new primitive registered on the backend gets a sensible default
//      workspace surface for free (`auto` everywhere) without any UI
//      change required.
//   2. As we polish a primitive's UX (custom curve+tenor picker, dedicated
//      output renderer), we add an entry here without touching the
//      primitive's config.yaml or its wire schema.
//
// Adding a new model
// ------------------
// 1. Confirm the primitive is registered on the backend (rates_primitive_
//    resolver in rates_agent/workflows/__init__.py).
// 2. Add an entry to MODEL_REGISTRY below.  Skip any field you don't
//    want to customise — every field has a sane default.
// 3. (Optional) Add a custom control or renderer if the primitive's
//    shape needs one.
//
// The workspace will read this registry at render time; the catalogue
// page checks `hasModelMetadata(toolName)` to decide whether the
// "Open in workspace" CTA routes to the rich model surface
// (`?tool=model&name=...`) or the simpler primitive surface
// (`?tool=primitive&name=...`).
// ============================================================================

export type ModelCategory =
  | 'pca'
  | 'regression'
  | 'attribution'
  | 'mean_reversion'
  | 'cross_market'
  | 'rates_curve'
  | 'event_signal';

/** Which output renderer dispatches on the run result. */
export type OutputRendererKind =
  | 'pca'
  | 'rolling_regression'
  | 'attribution'
  | 'time_series'
  | 'series_panel'
  | 'auto';

/** Which custom control to render for a given input field. */
export type ControlKind =
  | 'series_spec'        // single (curve_family, tenor, field_name) picker
  | 'series_spec_list'   // repeating series-spec picker (regressors)
  | 'multi_tenor'        // multi-select tenor list (PCA)
  | 'curve_family'       // single curve_family dropdown
  | 'tenor'              // single tenor dropdown
  | 'window_slider'      // int slider with regression-window presets
  | 'lookback_slider'    // int slider with lookback presets (calendar days)
  | 'enum'               // generic enum dropdown (uses field's examples)
  | 'date'               // YYYY-MM-DD date input
  | 'auto';              // fall back to ParameterPanel's schema-driven default

export type ParamHint = {
  control: ControlKind;
  /** Human-friendly label override.  When omitted, the field's
   *  schema name (snake_case) is humanised automatically. */
  label?: string;
  /** Compact help text shown under the control.  When omitted, the
   *  field's schema description is used. */
  help?: string;
  /** Slider preset values; used by *_slider controls. */
  presets?: number[];
  /** Hide this field from the controls rail entirely (e.g. for
   *  fields the substrate fills in but the user shouldn't touch). */
  hidden?: boolean;
};

export type InterpretationCard = {
  /** One-line headline shown bolded. */
  headline: string;
  /** Body copy — one short paragraph; no markdown rendering yet. */
  body: string;
};

export type ModelMetadata = {
  /** Backend tool_name.  Must match the rates_primitive_resolver key. */
  toolName: string;
  /** Display name shown in the workspace header (Title Case, no _tool). */
  displayName: string;
  /** Category chip rendered in the header + filterable in the catalogue. */
  category: ModelCategory;
  /** Whether this primitive emits time-series outputs, a snapshot, or both. */
  modelKind: 'time_series_model' | 'snapshot_model' | 'composite';
  /** Which output renderer to dispatch in the canvas. */
  outputRenderer: OutputRendererKind;
  /** Per-input-field render hints.  Keys are schema field names. */
  paramHints?: Record<string, ParamHint>;
  /** Optional interpretation cards rendered under the output canvas. */
  interpretationCards?: InterpretationCard[];
  /** Optional one-liner above the methodology block in the workspace. */
  oneLineSummary?: string;
  /** Default form values used when the workspace boots with no URL
   *  overrides and no saved preset.  Keys are schema field names; values
   *  are either:
   *    - structured (SeriesSpecValue, SeriesSpecValue[], string[]) for
   *      rich controls
   *    - strings for scalar text/number/select inputs
   *  These defaults must produce a *runnable* configuration — the user
   *  should be able to click Run on first load and get a real result. */
  defaultParams?: Record<string, unknown>;
};

// ---------------------------------------------------------------------------
// Sane defaults — every field below is overridable per-model.
// ---------------------------------------------------------------------------

export const DEFAULT_LOOKBACK_PRESETS = [365, 730, 1095, 1825, 3650];
export const DEFAULT_WINDOW_PRESETS = [22, 60, 126, 252, 504];

// ---------------------------------------------------------------------------
// Registry
// ---------------------------------------------------------------------------

const MODELS: ModelMetadata[] = [
  // -------------------------------------------------------------------
  // Rolling OLS regression (target ~ regressors).
  // -------------------------------------------------------------------
  {
    toolName: 'calculate_rolling_regression_tool',
    displayName: 'Rolling Regression',
    category: 'regression',
    modelKind: 'time_series_model',
    outputRenderer: 'rolling_regression',
    oneLineSummary:
      'Trailing-window OLS of one sovereign yield series on one or more regressor yield series. Single methodological knob: window length.',
    defaultParams: {
      target_spec: { curve_family: 'UST', tenor: '10Y' },
      regressor_specs: [{ curve_family: 'UST', tenor: '5Y' }],
      regression_window_days: '60',
      lookback_days: '730',
    },
    paramHints: {
      target_spec: {
        control: 'series_spec',
        label: 'Target series',
        help: 'The y in the regression — a single sovereign (curve, tenor) yield series.',
      },
      regressor_specs: {
        control: 'series_spec_list',
        label: 'Regressor series',
        help: 'One or more (curve, tenor) yield series — the columns of X.',
      },
      regression_window_days: {
        control: 'window_slider',
        label: 'Window (trading days)',
        help: 'Trailing-window length per rolling fit. Tactical = 60, annual = 252.',
        presets: DEFAULT_WINDOW_PRESETS,
      },
      lookback_days: {
        control: 'lookback_slider',
        label: 'Display lookback (calendar days)',
        help: 'Calendar days of history to render. Does NOT change the rolling-window length.',
        presets: DEFAULT_LOOKBACK_PRESETS,
      },
    },
    interpretationCards: [
      {
        headline: 'How to read the betas',
        body: 'Each beta time series is the partial elasticity of the target on that regressor at the rolling window date — the rest of the regressors held flat. A beta crossing 1 means the target moves one-for-one with the regressor in that window.',
      },
      {
        headline: 'When R² collapses',
        body: 'A sharp drop in rolling R² is a structural-break tell — the linear hedge ratio has lost predictive power for that horizon. Sustained low R² with high volatility is a regime-shift warning.',
      },
      {
        headline: 'Condition flag',
        body: 'A row flagged 1 means the design matrix was near-singular (e.g. two regressors became collinear) — that fit was suppressed and the row should be masked from interpretation.',
      },
    ],
  },

  // -------------------------------------------------------------------
  // PCA on the yield-changes panel of one sovereign curve.
  // -------------------------------------------------------------------
  {
    toolName: 'calculate_pca_yield_curve_tool',
    displayName: 'PCA · Yield Curve',
    category: 'pca',
    modelKind: 'composite',
    outputRenderer: 'pca',
    oneLineSummary:
      'Principal-component decomposition of a sovereign curve\'s yield changes — surfaces level, slope, and curvature factors plus their daily scores.',
    defaultParams: {
      curve_family: 'UST',
      lookback_days: '1825',
      n_components: '3',
      change_frequency: 'daily',
      // tenors: empty array → use the curve's full tenor universe.
      tenors: [],
    },
    paramHints: {
      curve_family: {
        control: 'curve_family',
        label: 'Curve family',
        help: 'Sovereign curve identifier — UST, DE_BUND, IT_BTP, FR_OAT, ES_BONO, UK_GILT, JGB.',
      },
      tenors: {
        control: 'multi_tenor',
        label: 'Tenors (subset)',
        help: 'Optional subset of tenors. Leave empty to use the full universe.',
      },
      lookback_days: {
        control: 'lookback_slider',
        label: 'Lookback (calendar days)',
        help: 'History fetched for the fit. 5 years is the desk-canonical default.',
        presets: DEFAULT_LOOKBACK_PRESETS,
      },
      n_components: {
        control: 'enum',
        label: 'Components to return',
        help: '3 captures level/slope/curvature on a normal sovereign panel.',
      },
      change_frequency: {
        control: 'enum',
        label: 'Change frequency',
        help: 'Differencing step: daily (1d) or weekly (5d).',
      },
      field_name: {
        control: 'auto',
        label: 'Field override',
        help: 'Bloomberg field. Leave blank to use config.yaml default (typically YLD_YTM_MID).',
      },
    },
    interpretationCards: [
      {
        headline: 'PC1 = Level',
        body: 'The first principal component on a normal sovereign curve loads positive across all tenors — it captures parallel shifts in the entire curve. Daily PC1 score moves correspond to broad rate-level moves.',
      },
      {
        headline: 'PC2 = Slope',
        body: 'PC2 typically loads positive at the long end and negative at the short end — it captures steepening vs flattening. PC2 moves track 2s10s and 5s30s dynamics.',
      },
      {
        headline: 'PC3 = Curvature',
        body: 'PC3 typically loads positive at the belly and negative at the wings — it captures butterfly moves. Watch this when belly-rich/cheap views are in play.',
      },
      {
        headline: 'Variance explained',
        body: 'The first three components typically capture >97% of yield-change variance on a developed sovereign. If they do not, the curve is in an atypical regime and the residuals are themselves the signal.',
      },
    ],
  },

  // -------------------------------------------------------------------
  // PCA-based attribution of a tenor's yield change over a window.
  // -------------------------------------------------------------------
  {
    toolName: 'calculate_yield_change_attribution_pca_tool',
    displayName: 'Yield-Change Attribution · PCA',
    category: 'attribution',
    modelKind: 'snapshot_model',
    outputRenderer: 'attribution',
    oneLineSummary:
      'Decomposes a single tenor\'s yield change between two dates into per-PCA-component contributions in basis points.',
    defaultParams: (() => {
      // Default window: ~last quarter, ending today.  ISO YYYY-MM-DD.
      const today = new Date();
      const start = new Date(today);
      start.setDate(start.getDate() - 90);
      const fmt = (d: Date) => d.toISOString().slice(0, 10);
      return {
        curve_family: 'UST',
        target_tenor: '10Y',
        start_date: fmt(start),
        end_date: fmt(today),
        n_components: '3',
      };
    })(),
    paramHints: {
      curve_family: { control: 'curve_family', label: 'Curve family' },
      target_tenor: { control: 'tenor', label: 'Target tenor' },
      start_date: {
        control: 'date',
        label: 'Window start',
        help: 'Calendar start of the change window. The tool resolves to the nearest trading day on or after.',
      },
      end_date: {
        control: 'date',
        label: 'Window end',
        help: 'Calendar end of the change window. The tool resolves to the nearest trading day on or before.',
      },
      n_components: {
        control: 'enum',
        label: 'Components',
        help: '3 captures level/slope/curvature on a normal sovereign panel.',
      },
      pasted_loadings: {
        control: 'auto',
        hidden: true,
      },
      pasted_loadings_input: {
        control: 'auto',
        hidden: true,
      },
    },
    interpretationCards: [
      {
        headline: 'Interpretation',
        body: 'Total change at the target tenor = sum of component contributions + residual. A residual >5bp on a 3-component decomposition signals atypical curve behaviour the level/slope/curvature basis cannot capture.',
      },
      {
        headline: 'Sign convention',
        body: 'Each PC\'s loading at the longest tenor is locked non-negative. So a positive PC1 contribution on a 10Y means the level component drove a yield rise; a negative PC2 contribution means the slope component compressed (flattening).',
      },
    ],
  },

  // -------------------------------------------------------------------
  // OU / AR(1) half-life of mean reversion.
  // -------------------------------------------------------------------
  {
    toolName: 'calculate_half_life_tool',
    displayName: 'Mean-Reversion Half-Life',
    category: 'mean_reversion',
    modelKind: 'snapshot_model',
    outputRenderer: 'auto',
    oneLineSummary:
      'Fits an Ornstein-Uhlenbeck / AR(1) process and reports the half-life of mean reversion — how long it takes a deviation to decay by half.',
    defaultParams: {
      series_spec: { curve_family: 'UST', tenor: '10Y' },
    },
    paramHints: {
      series_spec: { control: 'series_spec', label: 'Series', hidden: false },
      pair_spec: { control: 'auto', hidden: true },
      pasted_series: { control: 'auto', hidden: true },
    },
  },

  // -------------------------------------------------------------------
  // Beta-adjusted RV (rolling-OLS hedge ratio + bps residual).
  // -------------------------------------------------------------------
  {
    toolName: 'calculate_beta_adjusted_spread_tool',
    displayName: 'Beta-Adjusted Spread',
    category: 'regression',
    modelKind: 'time_series_model',
    outputRenderer: 'series_panel',
    oneLineSummary:
      'Bivariate beta-adjusted RV: rolling hedge ratio of one yield on another, residual in bps, residual z-score.',
    defaultParams: {
      target_spec: { curve_family: 'IT_BTP', tenor: '10Y' },
      hedge_spec: { curve_family: 'DE_BUND', tenor: '10Y' },
      regression_window_days: '60',
      lookback_days: '730',
    },
    paramHints: {
      target_spec: { control: 'series_spec', label: 'Target series' },
      hedge_spec: { control: 'series_spec', label: 'Hedge series' },
      regression_window_days: {
        control: 'window_slider',
        label: 'Hedge-ratio window',
        presets: DEFAULT_WINDOW_PRESETS,
      },
      lookback_days: {
        control: 'lookback_slider',
        label: 'Display lookback',
        presets: DEFAULT_LOOKBACK_PRESETS,
      },
    },
    interpretationCards: [
      {
        headline: 'How to read it',
        body: 'Residual = target - beta × hedge. A residual z-score >2 means the bivariate spread is rich on its own history; <-2 means cheap. The hedge ratio time series itself is the signal when betas drift.',
      },
    ],
  },
];

// ---------------------------------------------------------------------------
// Lookup helpers
// ---------------------------------------------------------------------------

const REGISTRY_INDEX: Record<string, ModelMetadata> = Object.fromEntries(
  MODELS.map((m) => [m.toolName, m] as const),
);

// R6.1 — every public lookup goes through ``normalizeToolName`` so
// Library manifest shorthand (e.g. ``half_life_tool``) resolves to the
// same canonical entry as the backend-prefixed form
// (``calculate_half_life_tool``).  Without this the Library "Open in
// Build" CTA mis-routes rich-model tools to the ``?context=`` decoder,
// producing the "Could not decode workspace context" card the user
// audit flagged.

import { normalizeToolName } from '@/lib/toolNames';

export function getModelMetadata(toolName: string): ModelMetadata | null {
  return REGISTRY_INDEX[normalizeToolName(toolName)] ?? null;
}

export function hasModelMetadata(toolName: string): boolean {
  return normalizeToolName(toolName) in REGISTRY_INDEX;
}

export function listModels(): ModelMetadata[] {
  return [...MODELS];
}

/** Resolve the ParamHint for a field, with a sensible default when the
 *  registry entry omits it (or doesn't exist at all).  ``toolName`` is
 *  normalised so callers can pass either the manifest shorthand or the
 *  backend-canonical form.
 *
 *  Priority (highest to lowest):
 *    1. Registry-supplied hint (explicit per-tool override).
 *    2. Field-name inference for canonical rates fields
 *       (``curve_family``, ``tenor``, ``lookback_days`` …).  Drives
 *       PR2's generic primitive builder: unregistered runnable
 *       primitives still get dropdown / slider controls for the
 *       shared rates vocabulary instead of falling back to plain
 *       text inputs.  Safe additive change — explicit registry
 *       hints always win.
 *    3. ``{control: 'auto'}`` — text/number/boolean fall-through.
 */
export function paramHintFor(
  toolName: string,
  fieldName: string,
): ParamHint {
  const meta = REGISTRY_INDEX[normalizeToolName(toolName)];
  const explicit = meta?.paramHints?.[fieldName];
  if (explicit) return explicit;
  return inferFieldControl(fieldName);
}

/** Field-name-based fallback for tools that don't ship a registry
 *  hint for the field.  Returns ``{control: 'auto'}`` for unknown
 *  field names so the ``ParametersPanel`` falls through to its
 *  text/number/boolean auto-renderer.
 *
 *  Used directly by the generic primitive builder (PR2) so the
 *  schema-driven controls rail recognises the shared rates
 *  vocabulary without the tool having to register a full
 *  ``ModelMetadata`` entry.  Exposed as a top-level helper so the
 *  routing-coverage tests can assert it. */
export function inferFieldControl(fieldName: string): ParamHint {
  // Curve-family pickers — every typed control set in the codebase
  // uses ``ALL_CURVES`` for both the sovereign and OIS variants.
  // Sovereign / OIS curves coexist in that option list; the user
  // picks whichever applies to the tool they're configuring.
  if (
    fieldName === 'curve_family' ||
    fieldName === 'curve_family_1' ||
    fieldName === 'curve_family_2' ||
    fieldName === 'sovereign_curve_family' ||
    fieldName === 'ois_curve_family' ||
    fieldName === 'nominal_curve_family' ||
    fieldName === 'real_curve_family' ||
    fieldName === 'proxy_curve'
  ) {
    return { control: 'curve_family' };
  }
  // Tenor pickers — short / long / belly / target / start / end /
  // forward-window all share the canonical TENORS option set.
  if (
    fieldName === 'tenor' ||
    fieldName === 'short_tenor' ||
    fieldName === 'long_tenor' ||
    fieldName === 'belly_tenor' ||
    fieldName === 'target_tenor' ||
    fieldName === 'front_tenor' ||
    fieldName === 'back_tenor' ||
    fieldName === 'start_tenor' ||
    fieldName === 'end_tenor' ||
    fieldName === 'forward_start' ||
    fieldName === 'forward_length'
  ) {
    return { control: 'tenor' };
  }
  // Lookback / rolling-window scalars — both surface as a slider
  // with calendar-day presets.  Different scope (display history
  // vs analytics window) but the control affordance is identical.
  if (
    fieldName === 'lookback_days' ||
    fieldName === 'rolling_window_days' ||
    fieldName === 'z_score_window_days' ||
    fieldName === 'regression_window_days'
  ) {
    return { control: 'lookback_slider' };
  }
  // Date / datetime fields — the date input is wire-compatible with
  // the FastAPI ``date`` query parameter.
  if (
    fieldName === 'start_date' ||
    fieldName === 'end_date' ||
    fieldName === 'as_of_date' ||
    fieldName === 'prior_date'
  ) {
    return { control: 'date' };
  }
  return { control: 'auto' };
}
