// ============================================================================
// workspaceParams
// ----------------------------------------------------------------------------
// Static reference data for the workspace parameter pickers. Curve families
// and tenors are mostly stable inputs to the sovereign-bonds tools; rather
// than fetch a /meta endpoint at boot, we hardcode the V1 set here. Adding
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

export const FX_PAIRS: { value: string; label: string }[] = [
  { value: 'EURUSD', label: 'EURUSD' },
  { value: 'GBPUSD', label: 'GBPUSD' },
  { value: 'USDJPY', label: 'USDJPY' },
  { value: 'AUDUSD', label: 'AUDUSD' },
  { value: 'USDCAD', label: 'USDCAD' },
  { value: 'USDCHF', label: 'USDCHF' },
  { value: 'EURGBP', label: 'EURGBP' },
  { value: 'EURJPY', label: 'EURJPY' },
];

export const FX_FORWARD_TENORS: { value: string; label: string }[] = [
  { value: '1W', label: '1W' },
  { value: '1M', label: '1M' },
  { value: '3M', label: '3M' },
  { value: '6M', label: '6M' },
];

/**
 * Build the parameter dropdown specs for a given view + current params dict.
 * The `value` on each spec falls back to the URL value, preserving any
 * bookmarked state; the controlling component owns the rest.
 */
export function paramSpecsForView(
  view: WorkspaceViewType,
  params: WorkspaceParams,
): ParamSpec[] {
  const get = (key: string, fallback: string) => params[key] || fallback;

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

    case 'fx_spot':
      return [
        {
          key: 'pair',
          label: 'Pair',
          options: FX_PAIRS,
          value: get('pair', 'EURUSD'),
        },
      ];

    case 'fx_carry':
      return [
        {
          key: 'tenor',
          label: 'Tenor',
          options: FX_FORWARD_TENORS,
          value: get('tenor', '1M'),
        },
      ];

    case 'fx_forward_curve':
      return [
        {
          key: 'pair',
          label: 'Pair',
          options: FX_PAIRS,
          value: get('pair', 'EURUSD'),
        },
      ];

    case 'scanner':
    case 'forward':
    default:
      return [];
  }
}

/**
 * Whether this view supports the lookback_days picker.
 * Regime owns its own lookback_period and scanner has no time dimension.
 */
export function viewUsesLookbackDays(view: WorkspaceViewType): boolean {
  return (
    view === 'spread' ||
    view === 'cross_market' ||
    view === 'butterfly' ||
    view === 'yield' ||
    view === 'fx_spot'
  );
}

/**
 * Pretty-name for a view — used as the page header title.
 */
export function viewTitle(
  view: WorkspaceViewType,
  params: WorkspaceParams,
): string {
  switch (view) {
    case 'spread': {
      const curveFamily = params['curve_family'] ?? 'UST';
      const shortTenor = params['short_tenor'] ?? '2Y';
      const longTenor = params['long_tenor'] ?? '10Y';
      return `${curveFamily} ${shortTenor}s${longTenor.replace('Y', '')}s`;
    }

    case 'cross_market': {
      const curveA = params['curve_family_1'] ?? 'IT_BTP';
      const curveB = params['curve_family_2'] ?? 'DE_BUND';
      const tenor = params['tenor'] ?? '10Y';
      return `${curveA}–${curveB} ${tenor}`;
    }

    case 'butterfly': {
      const curveFamily = params['curve_family'] ?? 'UST';
      const shortTenor = (params['short_tenor'] ?? '2Y').replace('Y', '');
      const bellyTenor = (params['belly_tenor'] ?? '5Y').replace('Y', '');
      const longTenor = (params['long_tenor'] ?? '10Y').replace('Y', '');
      return `${curveFamily} ${shortTenor}s${bellyTenor}s${longTenor}s butterfly`;
    }

    case 'yield': {
      const curveFamily = params['curve_family'] ?? 'UST';
      const tenor = params['tenor'] ?? '10Y';
      return `${curveFamily} ${tenor} yield`;
    }

    case 'regime': {
      const curveFamily = params['curve_family'] ?? 'UST';
      const window = params['lookback_period'] ?? '22d';
      return `${curveFamily} regime · ${window}`;
    }

    case 'scanner':
      return 'Z-score scanner';

    case 'forward':
      return 'OIS forward rates';

    case 'fx_spot': {
      const pair = params['pair'] ?? 'EURUSD';
      return `${pair} spot`;
    }

    case 'fx_carry': {
      const tenor = params['tenor'] ?? '1M';
      return `FX carry · ${tenor}`;
    }

    case 'fx_forward_curve': {
      const pair = params['pair'] ?? 'EURUSD';
      return `${pair} forward curve`;
    }

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

    case 'fx_spot':
      return 'FX spot level, momentum, percentile and rolling z-score.';

    case 'fx_carry':
      return 'Forward-implied FX carry ranking by tenor.';

    case 'fx_forward_curve':
      return 'Forward points, outrights and annualized carry across tenors.';

    default:
      return '';
  }
}
