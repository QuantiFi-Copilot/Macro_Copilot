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
// The workspace reads this registry at render time (``paramHintFor``
// drives the generic builder's controls; ``getModelMetadata`` lets the
// output canvas pick a bespoke renderer when an entry exists).
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
  | 'field_name'         // PR5 — Bloomberg observation-field dropdown
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
//
// Stage 4b moved the preset arrays to ``src/lib/modelPresets.ts`` so
// per-module ``module.ts`` files can value-import them without
// triggering the modelRegistry ↔ @/modules runtime cycle.  Re-exported
// here for backward compatibility with non-module consumers.

export { DEFAULT_LOOKBACK_PRESETS, DEFAULT_WINDOW_PRESETS } from './modelPresets';

// ---------------------------------------------------------------------------
// Registry — Stage 4b hybrid
// ---------------------------------------------------------------------------
//
// Pre-Stage-4b this file carried the full ``MODELS`` array verbatim.
// Stage 4b moved the 5 rich-model entries (PCA, rolling regression,
// attribution, half-life, beta-adjusted spread) onto each owning
// module's ``MODULE.modelMetadata`` field; the hand-authored set
// below is now empty.  The model list is the union of
// ``_HAND_AUTHORED_MODELS`` (empty today) and the module-derived
// contribution from ``ALL_PRIMITIVE_MODULES``.  The G-3.2 dual-view
// migration retired ``modelMetadata`` on every module, so the list is
// EMPTY today — the derivation stays as the contract for any future
// registry-backed metadata; no further edit to this file.

const _HAND_AUTHORED_MODELS: ModelMetadata[] = [
  // ----------------------------------------------------------------
  // Stage 4b removal — every prior entry (rolling regression, PCA,
  // attribution, half-life, beta-adjusted spread) moved onto its
  // owning module's ``modelMetadata`` field.  The module-derived
  // contribution below feeds those entries back into the union, so
  // the public ``MODELS`` export and ``REGISTRY_INDEX`` keys are
  // unchanged.
  // ----------------------------------------------------------------
];

import { ALL_PRIMITIVE_MODULES, getPrimitiveModule } from '@/modules';

// ---------------------------------------------------------------------------
// Lazy registry initialisation (Stage 4b)
// ---------------------------------------------------------------------------
//
// Why lazy: module ``module.ts`` files value-import per-tool Build
// surfaces, which can transitively import ``modelRegistry`` (e.g. for
// the shared preset arrays / param-hint helpers).  Touching
// ``ALL_PRIMITIVE_MODULES`` at modelRegistry's module-init time would
// mean reading it MID-cycle, before @/modules has finished populating
// it (TDZ → undefined → ``.filter`` throws).  Initialising on first
// lookup defers the read until after @/modules's load settles,
// breaking the cycle without restructuring the surface graph.  Every
// public accessor (``getModelMetadata`` / ``listModels`` /
// ``paramHintFor``) calls ``getRegistry()`` / ``getModels()`` instead
// of reading the raw module-scope binding.

let _modelsCache: ModelMetadata[] | null = null;
let _registryCache: Record<string, ModelMetadata> | null = null;

function getModels(): ModelMetadata[] {
  if (_modelsCache === null) {
    const moduleDerived: ModelMetadata[] = ALL_PRIMITIVE_MODULES
      .filter((m) => m.modelMetadata != null)
      .map((m) => m.modelMetadata as ModelMetadata);
    _modelsCache = [..._HAND_AUTHORED_MODELS, ...moduleDerived];
  }
  return _modelsCache;
}

function getRegistry(): Record<string, ModelMetadata> {
  if (_registryCache === null) {
    _registryCache = Object.fromEntries(
      getModels().map((m) => [m.toolName, m] as const),
    );
  }
  return _registryCache;
}

// R6.1 — every public lookup goes through ``normalizeToolName`` so
// Library manifest shorthand (e.g. ``half_life_tool``) resolves to the
// same canonical entry as the backend-prefixed form
// (``calculate_half_life_tool``).  Without this the Library "Open in
// Build" CTA mis-routes rich-model tools to the ``?context=`` decoder,
// producing the "Could not decode workspace context" card the user
// audit flagged.

import { normalizeToolName } from '@/lib/toolNames';

export function getModelMetadata(toolName: string): ModelMetadata | null {
  return getRegistry()[normalizeToolName(toolName)] ?? null;
}

export function listModels(): ModelMetadata[] {
  return [...getModels()];
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
  // Consolidation (G-3.2): the dual-view-migrated modules carry their
  // control hints on the SPEC's own ``paramHints`` field (FM5) — the
  // rich-model ``modelMetadata`` block is retired on those tools.
  // Spec hints take priority; the registry path remains for any
  // not-yet-migrated entry; field-name inference is the floor.
  const mod = getPrimitiveModule(normalizeToolName(toolName));
  const specHint = (
    mod?.paramHints as Record<string, ParamHint> | undefined
  )?.[fieldName];
  if (specHint) return specHint;
  const meta = getRegistry()[normalizeToolName(toolName)];
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
  // PR5 — Bloomberg observation-field pickers.  Every primitive's
  // config.yaml ships a ``default_field_name`` (``YLD_YTM_MID`` for
  // sovereigns, ``PX_LAST`` for OIS), but the override knob was
  // rendering as a free-text input because the inferer only
  // recognised the bare ``field_name`` field.  The schema-driven
  // generic builder hit this gap whenever a tool's input class
  // declared ``sovereign_field_name`` / ``ois_field_name`` (cross-
  // domain primitives), ``nominal_field_name`` / ``real_field_name``
  // (breakeven), or any prefixed Bloomberg-mnemonic field name.
  if (fieldName === 'field_name' || fieldName.endsWith('_field_name')) {
    return { control: 'field_name' };
  }
  return { control: 'auto' };
}
