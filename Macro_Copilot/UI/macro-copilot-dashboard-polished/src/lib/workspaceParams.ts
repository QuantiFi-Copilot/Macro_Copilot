// ============================================================================
// workspaceParams
// ----------------------------------------------------------------------------
// Static reference data for the workspace parameter pickers.  Curve families
// and tenors are mostly stable inputs to the sovereign-bonds tools; rather
// than fetch a /meta endpoint at boot, we hardcode the V1 set here.  Adding
// a new curve family means one entry in CURVE_FAMILIES + a backend ingestion
// patch — both surfaces are explicit.
// ============================================================================

import type { ParamSpec } from '@/components/workspace/WorkspaceHeader';
import type { WorkspaceParams, WorkspaceViewType } from '@/types/rates';

export const CURVE_FAMILIES: { value: string; label: string }[] = [
  { value: 'UST', label: 'UST · US Treasury' },
  { value: 'DE_BUND', label: 'BUND · Germany' },
  { value: 'IT_BTP', label: 'BTP · Italy' },
  { value: 'FR_OAT', label: 'OAT · France' },
  { value: 'ES_BONO', label: 'BONO · Spain' },
  { value: 'UK_GILT', label: 'GILT · UK' },
  { value: 'JP_JGB', label: 'JGB · Japan' },
];

export const TENORS: { value: string; label: string }[] = [
  { value: '2Y', label: '2Y' },
  { value: '3Y', label: '3Y' },
  { value: '5Y', label: '5Y' },
  { value: '7Y', label: '7Y' },
  { value: '10Y', label: '10Y' },
  { value: '20Y', label: '20Y' },
  { value: '30Y', label: '30Y' },
];

export const REGIME_LOOKBACKS: { value: string; label: string }[] = [
  { value: '1d', label: '1d (daily)' },
  { value: '5d', label: '5d (weekly)' },
  { value: '22d', label: '22d (monthly)' },
  { value: '63d', label: '63d (quarterly)' },
];

/**
 * Build the parameter dropdown specs for a given view + current params dict.
 * The `value` on each spec falls back to the URL value (preserving any
 * bookmarked state); the controlling component owns the rest.
 */
export function paramSpecsForView(
  view: WorkspaceViewType,
  params: WorkspaceParams,
): ParamSpec[] {
  const get = (k: string, fallback: string) => params[k] || fallback;

  switch (view) {
    case 'spread':
      return [
        {
          key: 'curve_family',
          label: 'Curve',
          options: CURVE_FAMILIES,
          value: get('curve_family', 'UST'),
        },
        {
          key: 'short_tenor',
          label: 'Short tenor',
          options: TENORS,
          value: get('short_tenor', '2Y'),
        },
        {
          key: 'long_tenor',
          label: 'Long tenor',
          options: TENORS,
          value: get('long_tenor', '10Y'),
        },
      ];

    case 'cross_market':
      return [
        {
          key: 'curve_family_1',
          label: 'Curve A',
          options: CURVE_FAMILIES,
          value: get('curve_family_1', 'IT_BTP'),
        },
        {
          key: 'curve_family_2',
          label: 'Curve B',
          options: CURVE_FAMILIES,
          value: get('curve_family_2', 'DE_BUND'),
        },
        {
          key: 'tenor',
          label: 'Tenor',
          options: TENORS,
          value: get('tenor', '10Y'),
        },
      ];

    case 'butterfly':
      return [
        {
          key: 'curve_family',
          label: 'Curve',
          options: CURVE_FAMILIES,
          value: get('curve_family', 'UST'),
        },
        {
          key: 'short_tenor',
          label: 'Short',
          options: TENORS,
          value: get('short_tenor', '2Y'),
        },
        {
          key: 'belly_tenor',
          label: 'Belly',
          options: TENORS,
          value: get('belly_tenor', '5Y'),
        },
        {
          key: 'long_tenor',
          label: 'Long',
          options: TENORS,
          value: get('long_tenor', '10Y'),
        },
      ];

    case 'yield':
      return [
        {
          key: 'curve_family',
          label: 'Curve',
          options: CURVE_FAMILIES,
          value: get('curve_family', 'UST'),
        },
        {
          key: 'tenor',
          label: 'Tenor',
          options: TENORS,
          value: get('tenor', '10Y'),
        },
      ];

    case 'regime':
      return [
        {
          key: 'curve_family',
          label: 'Curve',
          options: CURVE_FAMILIES,
          value: get('curve_family', 'UST'),
        },
        {
          key: 'front_tenor',
          label: 'Front',
          options: TENORS,
          value: get('front_tenor', '2Y'),
        },
        {
          key: 'back_tenor',
          label: 'Back',
          options: TENORS,
          value: get('back_tenor', '10Y'),
        },
        {
          key: 'lookback_period',
          label: 'Window',
          options: REGIME_LOOKBACKS,
          value: get('lookback_period', '22d'),
        },
      ];

    case 'scanner':
    case 'forward':
    default:
      return [];
  }
}

/**
 * Whether this view supports the lookback_days picker (1M / 3M / 6M / 1Y / 2Y).
 * Regime owns its own lookback_period and scanner has no time dimension.
 */
export function viewUsesLookbackDays(view: WorkspaceViewType): boolean {
  return view === 'spread'
    || view === 'cross_market'
    || view === 'butterfly'
    || view === 'yield';
}

/**
 * Pretty-name for a view — used as the page header title.
 */
export function viewTitle(view: WorkspaceViewType, params: WorkspaceParams): string {
  switch (view) {
    case 'spread': {
      const cf = params['curve_family'] ?? 'UST';
      const s = params['short_tenor'] ?? '2Y';
      const l = params['long_tenor'] ?? '10Y';
      return `${cf} ${s}s${l.replace('Y', '')}s`;
    }
    case 'cross_market': {
      const a = params['curve_family_1'] ?? 'IT_BTP';
      const b = params['curve_family_2'] ?? 'DE_BUND';
      const t = params['tenor'] ?? '10Y';
      return `${a}–${b} ${t}`;
    }
    case 'butterfly': {
      const cf = params['curve_family'] ?? 'UST';
      const s = (params['short_tenor'] ?? '2Y').replace('Y', '');
      const m = (params['belly_tenor'] ?? '5Y').replace('Y', '');
      const l = (params['long_tenor'] ?? '10Y').replace('Y', '');
      return `${cf} ${s}s${m}s${l}s butterfly`;
    }
    case 'yield': {
      const cf = params['curve_family'] ?? 'UST';
      const t = params['tenor'] ?? '10Y';
      return `${cf} ${t} yield`;
    }
    case 'regime': {
      const cf = params['curve_family'] ?? 'UST';
      const w = params['lookback_period'] ?? '22d';
      return `${cf} regime · ${w}`;
    }
    case 'scanner':
      return 'Z-score scanner';
    case 'forward':
      return 'OIS forward rates';
    default:
      return 'Workspace';
  }
}

export function viewSubtitle(view: WorkspaceViewType): string {
  switch (view) {
    case 'spread':
      return 'Curve spread between two tenors of the same sovereign curve.';
    case 'cross_market':
      return 'Same-tenor spread between two sovereign curves.';
    case 'butterfly':
      return 'Belly-vs-wings curvature across three tenors.';
    case 'yield':
      return 'Single-point yield level with rolling z-score.';
    case 'regime':
      return 'Curve-move classification over a configurable window.';
    case 'scanner':
      return 'Largest |z| moves across sovereign curves and tenors.';
    case 'forward':
      return 'OIS-implied forward rates (backend wiring pending).';
    default:
      return '';
  }
}
